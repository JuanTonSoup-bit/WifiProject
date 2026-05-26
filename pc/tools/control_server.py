"""
Embedded TCP control server for the running pipeline.

Listens on a local socket (default 127.0.0.1:5599) and accepts JSON-encoded
commands from the diagnostics CLI. Stays running in the background while the
main pipeline executes.

Wire protocol (newline-delimited JSON):
    Request:  {"cmd": "status"}
    Response: {"ok": true, "data": {...}}

    Request:  {"cmd": "threshold", "value": 0.30}
    Response: {"ok": true, "data": {"threshold": 0.30}}
"""

from __future__ import annotations

import json
import logging
import socket
import socketserver
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ControlHandler(socketserver.StreamRequestHandler):
    """Per-connection handler. Reads one JSON request per line."""

    def handle(self) -> None:
        server: "ControlServer" = self.server  # type: ignore[assignment]
        try:
            line = self.rfile.readline().strip()
            if not line:
                return
            try:
                req = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                self._respond({"ok": False, "error": f"invalid JSON: {exc}"})
                return

            cmd = req.get("cmd")
            handler = server.handlers.get(cmd)
            if handler is None:
                self._respond({"ok": False, "error": f"unknown command: {cmd}"})
                return

            try:
                data = handler(req)
                self._respond({"ok": True, "data": data})
            except Exception as exc:
                logger.exception("Handler %s failed", cmd)
                self._respond({"ok": False, "error": str(exc)})

        except (BrokenPipeError, ConnectionResetError):
            pass

    def _respond(self, obj: Dict[str, Any]) -> None:
        payload = (json.dumps(obj) + "\n").encode("utf-8")
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, OSError):
            pass


class ControlServer(socketserver.ThreadingTCPServer):
    """Threading TCP server with a registry of command handlers."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 5599) -> None:
        super().__init__((host, port), ControlHandler)
        self.handlers: Dict[str, Callable[[dict], Any]] = {}
        self._thread: Optional[threading.Thread] = None
        self._host = host
        self._port = port

    def register(self, cmd: str, handler: Callable[[dict], Any]) -> None:
        """Register a handler. Handler signature: (request_dict) -> response_data."""
        self.handlers[cmd] = handler

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self.serve_forever, name="control-server", daemon=True
        )
        self._thread.start()
        logger.info("Control server listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread:
            self._thread.join(timeout=2.0)


def send_command(host: str, port: int, cmd: str, **kwargs) -> dict:
    """Client helper: send a single command, return parsed response."""
    req = {"cmd": cmd, **kwargs}
    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        # Read until newline
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))
