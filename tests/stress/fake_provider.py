from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.stress import scenarios

_MODEL = "step-3.7-flash"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [{"id": _MODEL, "object": "model", "owned_by": "stress"}],
            })
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            request = {}
        messages = request.get("messages", []) or []
        status = scenarios.error_status(messages)
        if status is not None:
            self._send_json(status, scenarios.error_body(status))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for chunk in scenarios.iter_chunks(request):
                payload = b"data: " + json.dumps(chunk).encode() + b"\n\n"
                self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()
            done = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(done):x}\r\n".encode() + done + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            self.close_connection = True


def start() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
