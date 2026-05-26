"""UDP echo server for round-trip latency testing from the laptop."""

from __future__ import annotations

import logging
import socket
import threading

logger = logging.getLogger(__name__)


class UDPEchoServer:
    """Echoes every UDP datagram back to the sender. Used by laptop/latency_probe.py."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5502) -> None:
        self._host = host
        self._port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._echo_count = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="echo-server", daemon=True)
        self._thread.start()
        logger.info("UDP echo server listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock.bind((self._host, self._port))
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                sock.sendto(data, addr)
                self._echo_count += 1
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    logger.error("Echo server error: %s", exc)
                break
        sock.close()

    @property
    def echo_count(self) -> int:
        return self._echo_count


if __name__ == "__main__":
    import argparse
    import time

    p = argparse.ArgumentParser(description="UDP echo server for latency testing")
    p.add_argument("--port", type=int, default=5502)
    args = p.parse_args()

    server = UDPEchoServer(port=args.port)
    server.start()
    print(f"Echo server on :{args.port} — Ctrl+C to stop")
    try:
        while True:
            time.sleep(5)
            print(f"Echoed {server.echo_count} packets")
    except KeyboardInterrupt:
        server.stop()
