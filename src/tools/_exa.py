import json


def endpoint_url(api_key: str) -> str:
    return f"https://mcp.exa.ai/mcp?exaApiKey={api_key}" if api_key else "https://mcp.exa.ai/mcp"


def extract_result(text: str) -> tuple[str | None, bool]:
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
