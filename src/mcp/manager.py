import threading

from src.mcp import register_mcp_tool_names
from src.mcp.client import McpHttpClient, McpStdioClient


class McpManager:
    def __init__(self) -> None:
        self._servers: dict[str, dict] = {}
        self._clients: dict = {}
        self._tools: list[dict] = []
        self._status: dict[str, str] = {}
        self._threads: list[threading.Thread] = []
        self._generation = 0
        self._loading = False
        self._loaded = False
        self._lock = threading.Lock()

    def configure(self, servers: dict[str, dict] | None) -> None:
        raw = servers or {}
        enabled = {
            name: cfg
            for name, cfg in raw.items()
            if isinstance(cfg, dict) and str(cfg.get("status", "enabled")).lower() != "disabled"
        }
        with self._lock:
            if enabled == self._servers and (self._loaded or self._loading):
                return
            self._servers = enabled
            self._reset_locked()
            self._loaded = False
            self._loading = False

    def connect_async(self, servers: dict[str, dict] | None) -> None:
        self.configure(servers)
        self._start_load()

    def _reset_locked(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients = {}
        self._tools = []
        self._threads = []
        self._generation += 1
        self._status = {name: "connecting" for name in self._servers}

    def _start_load(self) -> None:
        with self._lock:
            if self._loaded or self._loading:
                return
            if not self._servers:
                self._loaded = True
                return
            self._loading = True
            generation = self._generation
            self._threads = [
                threading.Thread(target=self._load_server, args=(name, cfg, generation), daemon=True)
                for name, cfg in self._servers.items()
            ]
            threads = list(self._threads)
        for thread in threads:
            thread.start()

    def _join_load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            threads = list(self._threads)
            if not threads:
                self._loaded = True
                return
        for thread in threads:
            thread.join()
        with self._lock:
            self._threads = []
            self._loading = False
            self._loaded = True
            tools = list(self._tools)
        register_mcp_tool_names([t["name"] for t in tools])

    def _ensure_loaded(self) -> None:
        self._start_load()
        self._join_load()

    def _load_server(self, name: str, cfg: dict, generation: int) -> None:
        try:
            if cfg.get("url"):
                client = McpHttpClient(name, cfg["url"], headers=cfg.get("headers"))
            elif cfg.get("command"):
                client = McpStdioClient(name, cfg["command"], args=cfg.get("args"), env=cfg.get("env"))
            else:
                with self._lock:
                    if generation == self._generation:
                        self._status[name] = "failed"
                return
            tools = client.list_tools()
            with self._lock:
                if generation != self._generation:
                    try:
                        client.close()
                    except Exception:
                        pass
                    return
                self._clients[name] = client
                self._status[name] = "connected"
                for tool in tools:
                    entry = dict(tool)
                    entry["server"] = name
                    self._tools.append(entry)
        except Exception:
            with self._lock:
                if generation == self._generation:
                    self._status[name] = "failed"

    @property
    def tools(self) -> list[dict]:
        self._start_load()
        with self._lock:
            return list(self._tools)

    def status_counts(self) -> dict[str, int]:
        counts = {"connected": 0, "connecting": 0, "failed": 0}
        with self._lock:
            for state in self._status.values():
                if state in counts:
                    counts[state] += 1
        return counts

    def server_status(self, name: str) -> str:
        with self._lock:
            return self._status.get(name, "connecting")

    def execute(self, tool_name: str, arguments: dict) -> dict:
        self._ensure_loaded()
        with self._lock:
            tools = list(self._tools)
            clients = dict(self._clients)
        for tool in tools:
            if tool.get("name") == tool_name:
                client = clients.get(tool.get("server", ""))
                if client is None:
                    break
                return client.call_tool(tool_name, arguments)
        return {"output": f"Unknown MCP tool: {tool_name}", "metadata": {"error": True}}


_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager
