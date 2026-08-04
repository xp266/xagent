import json
import threading

import httpx
from datetime import date

from src.types.tools import Tool
from src.utils.config import get_exa_api_key
from src.agent.cancel import is_cancelled, register_abort

_year = date.today().year

_active_client = None
_client_lock = threading.Lock()


def _set_active_client(client) -> None:
    global _active_client
    with _client_lock:
        _active_client = client


def _abort_web() -> None:
    global _active_client
    with _client_lock:
        client = _active_client
        _active_client = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


register_abort(_abort_web)


def _endpoint_url(api_key: str) -> str:
    return f"https://mcp.exa.ai/mcp?exaApiKey={api_key}" if api_key else "https://mcp.exa.ai/mcp"


def _extract_result(text: str) -> tuple[str | None, bool]:
    def parse(data: dict) -> tuple[str | None, bool]:
        content = data.get("result", {}).get("content")
        if isinstance(content, list):
            texts = [c["text"] for c in content if isinstance(c, dict) and "text" in c]
            if texts:
                return "\n\n".join(texts), bool(data.get("result", {}).get("isError"))
        return None, False

    try:
        for line in text.strip().split("\n"):
            if line.startswith("data: "):
                joined, is_error = parse(json.loads(line[6:]))
                if joined is not None:
                    return joined, is_error
        joined, is_error = parse(json.loads(text))
        if joined is not None:
            return joined, is_error
    except Exception:
        pass
    return None, False


def _search(query: str, num_results: int, type: str, livecrawl: str, context_max: int) -> dict:
    if is_cancelled():
        return {"title": query, "output": "Web search interrupted by user.", "metadata": {"error": True}}

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "type": type,
                "numResults": max(1, min(10, num_results)),
                "livecrawl": livecrawl,
                "maxContext": max(1000, min(20000, context_max)),
            },
        },
    }

    try:
        client = httpx.Client(timeout=30)
        _set_active_client(client)
        try:
            resp = client.post(_endpoint_url(get_exa_api_key()), json=payload, headers={"Accept": "application/json, text/event-stream"})
            resp.raise_for_status()
            result, is_error = _extract_result(resp.text)
        finally:
            _set_active_client(None)
            client.close()
        if is_error:
            return {"title": query, "output": f"Search failed: {result}", "metadata": {"error": True}}
        output = result if result else resp.text[:2000]
        return {"title": query, "output": output, "metadata": {}}
    except httpx.HTTPError as e:
        return {"title": query, "output": f"Search failed: {e}", "metadata": {"error": True}}


def _fetch(url: str, timeout: int) -> dict:
    if is_cancelled():
        return {"title": url, "output": "URL fetch interrupted by user.", "metadata": {"error": True}}

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_fetch_exa",
            "arguments": {
                "urls": [url],
            },
        },
    }

    try:
        client = httpx.Client(timeout=min(timeout, 120))
        _set_active_client(client)
        try:
            resp = client.post(
                _endpoint_url(get_exa_api_key()),
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            resp.raise_for_status()
            text, is_error = _extract_result(resp.text)
        finally:
            _set_active_client(None)
            client.close()
        if is_error:
            return {"title": url, "output": f"Failed to fetch URL: {text}", "metadata": {"error": True}}
        if text:
            return {"title": url, "output": text, "metadata": {}}
        return {"title": url, "output": f"Successfully fetched {url}", "metadata": {}}
    except httpx.HTTPError as e:
        return {"title": url, "output": f"Failed to fetch URL: {e}", "metadata": {"error": True}}
    except Exception as e:
        return {"title": url, "output": f"Error fetching URL: {e}", "metadata": {"error": True}}


def execute(action: str = "search", query: str = "", url: str = "", num_results: int = 8,
            type: str = "auto", livecrawl: str = "fallback",
            contextMaxCharacters: int = 10000, timeout: int = 30, **kwargs) -> dict:
    if action not in ("search", "fetch"):
        return {"title": "web", "output": f"Error: unknown action '{action}', must be 'search' or 'fetch'", "metadata": {"error": True}}
    if action == "fetch":
        if not url:
            return {"title": "web", "output": "Error fetching URL: url is required when action is 'fetch'", "metadata": {"error": True}}
        if url.startswith("http://"):
            url = "https://" + url[7:]
        elif not url.startswith("https://"):
            url = "https://" + url
        return _fetch(url, timeout)

    if not query:
        return {"title": "web", "output": "Search failed: query is required when action is 'search'", "metadata": {"error": True}}
    return _search(query, num_results, type, livecrawl, contextMaxCharacters)


def to_model_output(data: dict) -> str:
    return data.get("output", "")


tool = Tool(
    name="web",
    description=f"""Search the web or fetch content from a URL using Exa.

Use action="search" to search the web with keywords.
The current year is {_year}. You MUST use this year when searching for recent information or current events
- Example: If the current year is {_year} and the user asks for "latest AI news", search for "AI news {_year}", NOT "AI news {_year - 1}"

Use action="fetch" to fetch content from a specific URL.
Usage notes:
  - IMPORTANT: if another tool is present that offers better web fetching capabilities, is more targeted to the task, or has fewer restrictions, prefer using that tool instead of this one.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS""",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "fetch"],
                "description": "'search': search the web with keywords; 'fetch': fetch content from a URL",
            },
            "query": {"type": "string", "description": "Web search query (required when action is 'search')"},
            "url": {"type": "string", "description": "The URL to fetch content from (required when action is 'fetch')"},
            "num_results": {"type": "integer", "description": "Number of search results to return (default: 8)"},
            "type": {
                "type": "string",
                "enum": ["auto", "fast", "deep"],
                "description": "Search type - 'auto': balanced search (default), 'fast': quick results, 'deep': comprehensive search",
            },
            "livecrawl": {
                "type": "string",
                "enum": ["fallback", "preferred"],
                "description": "Live crawl mode - 'fallback': use live crawling as backup if cached content unavailable, 'preferred': prioritize live crawling (default: 'fallback')",
            },
            "contextMaxCharacters": {"type": "integer", "description": "Maximum characters for context string optimized for LLMs (default: 10000)"},
            "timeout": {"type": "integer", "description": "Optional timeout in seconds for fetch (max 120)"},
        },
        "required": ["action"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
