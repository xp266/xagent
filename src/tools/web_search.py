import httpx
from datetime import date
from src.types.tools import Tool
from src.tools._exa import endpoint_url, extract_result
from src.utils.config import get_exa_api_key

_year = date.today().year


def execute(query: str, num_results: int = 8, type: str = "auto",
            livecrawl: str = "fallback", contextMaxCharacters: int = 10000, **kwargs) -> dict:
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

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(endpoint_url(get_exa_api_key()), json=payload, headers={"Accept": "application/json, text/event-stream"})
            resp.raise_for_status()
            result, is_error = extract_result(resp.text)
            if is_error:
                return {"title": query, "output": f"Search failed: {result}", "metadata": {"error": True}}
            output = result if result else resp.text[:2000]
            return {"title": query, "output": output, "metadata": {}}
    except httpx.HTTPError as e:
        return {"title": query, "output": f"Search failed: {e}", "metadata": {"error": True}}


def to_model_output(data: dict) -> str:
    return data.get("output", "")


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
    to_model_output=to_model_output,
)
