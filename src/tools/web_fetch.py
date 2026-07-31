import os
import json
import httpx
from src.types.tools import Tool


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


def execute(url: str, timeout: int = 30, **kwargs) -> str:
    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("https://"):
        url = "https://" + url

    api_key = os.getenv("EXA_API_KEY")
    mcp_url = f"https://mcp.exa.ai/mcp?exaApiKey={api_key}" if api_key else "https://mcp.exa.ai/mcp"

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
                mcp_url, json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            resp.raise_for_status()
            text, is_error = _extract_result(resp.text)
            if is_error:
                return f"Failed to fetch URL: {text}"
            if text:
                return text
            return f"Successfully fetched {url}"
    except httpx.HTTPError as e:
        return f"Failed to fetch URL: {e}"
    except Exception as e:
        return f"Error fetching URL: {e}"


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
)

