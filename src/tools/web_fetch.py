import httpx
from src.types.tools import Tool
from src.tools._exa import endpoint_url, extract_result
from src.utils.config import get_exa_api_key


def execute(url: str, timeout: int = 30, **kwargs) -> dict:
    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("https://"):
        url = "https://" + url

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
        with httpx.Client(timeout=min(timeout, 120)) as client:
            resp = client.post(
                endpoint_url(get_exa_api_key()),
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            resp.raise_for_status()
            text, is_error = extract_result(resp.text)
            if is_error:
                return {"title": url, "output": f"Failed to fetch URL: {text}", "metadata": {"error": True}}
            if text:
                return {"title": url, "output": text, "metadata": {}}
            return {"title": url, "output": f"Successfully fetched {url}", "metadata": {}}
    except httpx.HTTPError as e:
        return {"title": url, "output": f"Failed to fetch URL: {e}", "metadata": {"error": True}}
    except Exception as e:
        return {"title": url, "output": f"Error fetching URL: {e}", "metadata": {"error": True}}


def to_model_output(data: dict) -> str:
    return data.get("output", "")


tool = Tool(
    name="web_fetch",
    description="""Fetches content from a specified URL.

Usage notes:
  - IMPORTANT: if another tool is present that offers better web fetching capabilities, is more targeted to the task, or has fewer restrictions, prefer using that tool instead of this one.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - This tool is read-only and does not modify any files""",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (max 120)",
            },
        },
        "required": ["url"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
