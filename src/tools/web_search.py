import json
from datetime import date
import httpx
from src.types.tools import Tool
from src.utils.config import get_exa_api_key

_year = date.today().year


def _extract_texts(data: dict) -> list[str] | None:
    content = data.get("result", {}).get("content")
    if isinstance(content, list):
        return [c["text"] for c in content if isinstance(c, dict) and "text" in c]
    return None


def _extract_result(text: str) -> tuple[str | None, bool]:
    def parse(data: dict) -> tuple[str | None, bool]:
        texts = _extract_texts(data)
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


def execute(query: str, num_results: int = 8, type: str = "auto",
            livecrawl: str = "fallback", contextMaxCharacters: int = 10000, **kwargs) -> str:
    api_key = get_exa_api_key()
    url = f"https://mcp.exa.ai/mcp?exaApiKey={api_key}" if api_key else "https://mcp.exa.ai/mcp"

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
                "maxContext": max(1000, min(20000, contextMaxCharacters)),
            },
        },
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers={"Accept": "application/json, text/event-stream"})
        resp.raise_for_status()
        result, is_error = _extract_result(resp.text)
        if is_error:
            return f"Search failed: {result}"
        return result if result else resp.text[:2000]


tool = Tool(
    name="web_search",
    description=f"""Search the web using the session's web search provider.

The current year is {_year}. You MUST use this year when searching for recent information or current events
- Example: If the current year is {_year} and the user asks for "latest AI news", search for "AI news {_year}", NOT "AI news {_year - 1}\"""",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Web search query"},
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
        },
        "required": ["query"],
    },
    execute=execute,
)

