#!/usr/bin/env python3
"""
Pi health monitor: exposes /health HTTP endpoint on port 8080.
Reads stats from a shared state file written by csi_streamer.py.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "/run/wifi-csi/streamer_stats.json"


class HealthState:
    def __init__(self) -> None:
        self.capture_hz: float = 0.0
        self.dropped_frames: int = 0
        self.uptime_s: float = 0.0
        self.start_time: float = time.monotonic()
        self._lock = threading.Lock()

    def update(self, hz: float, dropped: int) -> None:
        with self._lock:
            self.capture_hz = hz
            self.dropped_frames = dropped
            self.uptime_s = time.monotonic() - self.start_time

    def to_json(self) -> str:
        with self._lock:
            hz = self.capture_hz
        if hz > 80:
            status = "ok"
        elif hz > 20:
            status = "degraded"
        else:
            status = "down"
        return json.dumps({
            "status": status,
            "capture_hz": round(self.capture_hz, 1),
            "dropped_frames": self.dropped_frames,
            "uptime_s": round(self.uptime_s, 1),
        })


_state = HealthState()


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = _state.to_json().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress default access log spam


def poll_state_file() -> None:
    """Background thread: reads stats file written by csi_streamer."""
    while True:
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as fh:
                    data = json.load(fh)
                _state.update(
                    hz=float(data.get("rate_hz", 0)),
                    dropped=int(data.get("dropped_frames", 0)),
                )
        except (json.JSONDecodeError, OSError):
            pass
        time.sleep(2.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    t = threading.Thread(target=poll_state_file, daemon=True)
    t.start()

    server = http.server.HTTPServer(("0.0.0.0", args.port), HealthHandler)
    logger.info("Health monitor on port %d", args.port)

    def _stop(sig, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    server.serve_forever()


if __name__ == "__main__":
    main()
