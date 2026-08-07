_mcp_tool_names: set[str] = set()


def register_mcp_tool_names(names: list[str]) -> None:
    _mcp_tool_names.update(names)


def is_mcp_tool(name: str) -> bool:
    return name in _mcp_tool_names
