import json
import os
import subprocess
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError

import httpx


def _check_cancel() -> None:
    from src.agent.cancel import TurnCancelled, is_cancelled

    if is_cancelled():
        raise TurnCancelled


def _parse_response(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[len("data:"):].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj):
            return obj
    return None


class McpHttpClient:
    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: float = 30.0) -> None:
        self.name = name
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        self._active = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            client = self._active
            self._active = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict | None:
        _check_cancel()
        notification = method.startswith("notifications/")
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if not notification:
            self._next_id += 1
            payload["id"] = self._next_id
        if params is not None:
            payload["params"] = params

        client = httpx.Client(timeout=timeout or self.timeout)
        with self._lock:
            self._active = client
        try:
            headers = {"Accept": "application/json, text/event-stream"}
            headers.update(self.headers)
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            resp = client.post(self.url, json=payload, headers=headers)
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            if resp.status_code != 200:
                return None
            if notification:
                return None
            return _parse_response(resp.text)
        finally:
            with self._lock:
                if self._active is client:
                    self._active = None
            try:
                client.close()
            except Exception:
                pass

    def initialize(self) -> None:
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "xagent", "version": "0.2.0"},
                },
            )
            try:
                self._request("notifications/initialized")
            except Exception:
                pass
        except Exception:
            pass

    def list_tools(self) -> list[dict]:
        self.initialize()
        result = self._request("tools/list", {})
        if not isinstance(result, dict):
            return []
        inner = result.get("result") or {}
        tools = inner.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: dict) -> dict:
        _check_cancel()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        _check_cancel()
        if not isinstance(result, dict):
            return {"output": "MCP call failed: empty response", "metadata": {"error": True}}
        if "error" in result:
            err = result.get("error") or {}
            return {"output": f"MCP error: {err.get('message', str(err))}", "metadata": {"error": True}}
        inner = result.get("result") or {}
        texts: list[str] = []
        for block in inner.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "resource":
                texts.append(str(block.get("resource", "")))
        output = "\n\n".join(texts) if texts else (inner.get("output") or "OK")
        return {"output": output, "metadata": {"error": bool(inner.get("isError"))}}


class McpStdioClient:
    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict | None = None) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    def _start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            full_env = dict(os.environ)
            full_env.update(self.env or {})
            try:
                self._proc = subprocess.Popen(
                    [self.command, *self.args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    env=full_env,
                )
            except Exception:
                self._proc = None
                raise
            proc = self._proc
        threading.Thread(target=self._read_loop, args=(proc,), daemon=True).start()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stdout or []:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = msg.get("id")
                if isinstance(rid, int):
                    with self._lock:
                        fut = self._pending.pop(rid, None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except Exception:
            pass
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
            proc_alive = self._proc is proc
            if proc_alive:
                self._proc = None
        for rid, fut in pending:
            if not fut.done():
                fut.set_result({"error": {"message": "MCP stdio server closed"}})

    def _request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict | None:
        _check_cancel()
        self._start()
        with self._lock:
            proc = self._proc
        if proc is None:
            raise RuntimeError("MCP stdio server failed to start")
        notification = method.startswith("notifications/")
        payload: dict = {"jsonrpc": "2.0", "method": method}
        rid = 0
        if not notification:
            with self._lock:
                self._next_id += 1
                rid = self._next_id
            payload["id"] = rid
        if params is not None:
            payload["params"] = params
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except Exception as e:
            _check_cancel()
            raise RuntimeError(f"MCP stdio write failed: {e}")
        if notification or rid == 0:
            return None
        fut: Future = Future()
        with self._lock:
            self._pending[rid] = fut
        try:
            return fut.result(timeout=timeout)
        except FutureTimeoutError:
            with self._lock:
                self._pending.pop(rid, None)
            return None

    def initialize(self) -> None:
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "xagent", "version": "0.2.0"},
                },
            )
            try:
                self._request("notifications/initialized")
            except Exception:
                pass
        except Exception:
            pass

    def list_tools(self) -> list[dict]:
        self.initialize()
        result = self._request("tools/list", {})
        if not isinstance(result, dict):
            return []
        inner = result.get("result") or {}
        tools = inner.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: dict) -> dict:
        _check_cancel()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        _check_cancel()
        if not isinstance(result, dict):
            return {"output": "MCP call failed: empty response", "metadata": {"error": True}}
        if "error" in result:
            err = result.get("error") or {}
            return {"output": f"MCP error: {err.get('message', str(err))}", "metadata": {"error": True}}
        inner = result.get("result") or {}
        texts: list[str] = []
        for block in inner.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "resource":
                texts.append(str(block.get("resource", "")))
        output = "\n\n".join(texts) if texts else (inner.get("output") or "OK")
        return {"output": output, "metadata": {"error": bool(inner.get("isError"))}}
