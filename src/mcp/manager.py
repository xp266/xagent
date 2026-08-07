from src.mcp import register_mcp_tool_names
from src.mcp.client import McpHttpClient, McpStdioClient


class McpManager:
    def __init__(self) -> None:
        self._servers: dict[str, dict] = {}
        self._clients: dict = {}
        self._tools: list[dict] = []
        self._loaded = False
        self._abort_registered = False

    def configure(self, servers: dict[str, dict] | None) -> None:
        raw = servers or {}
        self._servers = {
            name: cfg
            for name, cfg in raw.items()
            if isinstance(cfg, dict) and str(cfg.get("status", "enabled")).lower() != "disabled"
        }
        self._reset()
        if self._servers and not self._abort_registered:
            self._abort_registered = True
            from src.agent.cancel import register_abort
            register_abort(self._abort_all)

    def _reset(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients = {}
        self._tools = []
        self._loaded = False
        register_mcp_tool_names([])

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for name, cfg in self._servers.items():
            try:
                if cfg.get("url"):
                    client = McpHttpClient(name, cfg["url"], headers=cfg.get("headers"))
                elif cfg.get("command"):
                    client = McpStdioClient(name, cfg["command"], args=cfg.get("args"), env=cfg.get("env"))
                else:
                    continue
                tools = client.list_tools()
                self._clients[name] = client
                for tool in tools:
                    entry = dict(tool)
                    entry["server"] = name
                    self._tools.append(entry)
            except Exception:
                try:
                    if name in self._clients:
                        self._clients[name].close()
                except Exception:
                    pass
        register_mcp_tool_names([t["name"] for t in self._tools])

    @property
    def tools(self) -> list[dict]:
        self._ensure_loaded()
        return self._tools

    def execute(self, tool_name: str, arguments: dict) -> dict:
        self._ensure_loaded()
        for tool in self._tools:
            if tool.get("name") == tool_name:
                client = self._clients.get(tool.get("server", ""))
                if client is None:
                    break
                return client.call_tool(tool_name, arguments)
        return {"output": f"Unknown MCP tool: {tool_name}", "metadata": {"error": True}}

    def _abort_all(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass


_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager
