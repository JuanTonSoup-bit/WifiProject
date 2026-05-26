#!/usr/bin/env python3
"""
WiFi traffic generator for Nexmon CSI excitation.

Sends ~100 UDP packets/sec to the Pi's WiFi IP so Nexmon extracts one
CSI frame per received packet (= 100 Hz CSI).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import struct
import sys
import time
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "transmitter": {
        "target_ip": "192.168.1.100",
        "target_port": 9999,
        "rate_hz": 100,
        "packet_size_bytes": 64,
        "method": "udp",
        "broadcast_addr": "255.255.255.255",
        "ramp_up_seconds": 2.0,
        "status_interval_s": 5,
        "log_level": "INFO",
    },
    "network": {
        "pc_ip": "192.168.2.100",
        "transmitter_port": 5501,
    },
}


def load_config(path: Optional[str]) -> dict:
    if path and os.path.exists(path):
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
        for section, defaults in DEFAULT_CONFIG.items():
            cfg.setdefault(section, {})
            for k, v in defaults.items():
                cfg[section].setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()


class Transmitter:
    def __init__(self, config: dict) -> None:
        tx = config["transmitter"]
        net = config["network"]

        self._target_ip = tx["target_ip"]
        self._target_port = int(tx["target_port"])
        self._rate_hz = float(tx["rate_hz"])
        self._pkt_size = int(tx["packet_size_bytes"])
        self._method = tx["method"]
        self._broadcast_addr = tx["broadcast_addr"]
        self._ramp_time = float(tx["ramp_up_seconds"])
        self._status_interval = float(tx["status_interval_s"])

        self._pc_ip = net.get("pc_ip", "")
        self._status_port = int(net.get("transmitter_port", 5501))

        self._sock: Optional[socket.socket] = None
        self._status_sock: Optional[socket.socket] = None
        self._running = False
        self._seq = 0
        self._packets_sent = 0
        self._start_time = 0.0
        self._last_status = 0.0

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self._method == "broadcast":
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            logger.info("Broadcast mode enabled")

        self._status_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._running = True
        self._start_time = time.perf_counter()
        self._last_status = self._start_time
        logger.info(
            "Transmitter started: target=%s:%d rate=%.0fHz pkt_size=%dB method=%s",
            self._target_ip, self._target_port, self._rate_hz, self._pkt_size, self._method,
        )
        try:
            self._run()
        finally:
            self._cleanup()

    def _run(self) -> None:
        interval = 1.0 / self._rate_hz
        ramp_steps = max(1, int(self._ramp_time * self._rate_hz))

        next_send = time.perf_counter()

        while self._running:
            # Ramp up rate during initial period
            if self._packets_sent < ramp_steps:
                ramp_factor = (self._packets_sent + 1) / ramp_steps
                effective_interval = interval / ramp_factor
            else:
                effective_interval = interval

            self._send_packet()
            next_send += effective_interval
            sleep_time = next_send - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)

            self._maybe_send_status()

    def _build_payload(self) -> bytes:
        """Build transmit payload: seq(4) + timestamp_ms(8) + padding."""
        ts_ms = int(time.monotonic() * 1000)
        header = struct.pack(">IQ", self._seq, ts_ms)
        payload_len = max(self._pkt_size, len(header))
        return header + b'\x00' * (payload_len - len(header))

    def _send_packet(self) -> None:
        payload = self._build_payload()
        target = (
            self._broadcast_addr if self._method == "broadcast" else self._target_ip,
            self._target_port,
        )
        try:
            self._sock.sendto(payload, target)  # type: ignore[union-attr]
            self._packets_sent += 1
            self._seq = (self._seq + 1) & 0xFFFFFFFF
        except OSError as exc:
            logger.warning("Send error: %s", exc)

    def _maybe_send_status(self) -> None:
        now = time.perf_counter()
        if now - self._last_status < self._status_interval:
            return

        elapsed = now - self._start_time
        actual_hz = self._packets_sent / max(elapsed, 1e-6)

        status = json.dumps({
            "type": "tx_stats",
            "seq": self._seq,
            "rate_hz": round(actual_hz, 2),
            "packets_sent": self._packets_sent,
            "timestamp_ms": int(time.monotonic() * 1000),
        }).encode()

        if self._pc_ip:
            try:
                self._status_sock.sendto(status, (self._pc_ip, self._status_port))  # type: ignore[union-attr]
            except OSError:
                pass

        logger.info(
            "TX stats: sent=%d actual_rate=%.1f Hz target=%.1f Hz",
            self._packets_sent, actual_hz, self._rate_hz,
        )
        self._last_status = now

    def _cleanup(self) -> None:
        for s in (self._sock, self._status_sock):
            if s:
                try:
                    s.close()
                except OSError:
                    pass
        logger.info("Transmitter stopped. Total packets sent: %d", self._packets_sent)

    def stop(self) -> None:
        self._running = False


def main() -> None:
    parser = argparse.ArgumentParser(description="WiFi CSI traffic generator")
    parser.add_argument("--config", default=None)
    parser.add_argument("--rate", type=float, help="Target Hz (overrides config)")
    parser.add_argument("--target", help="Target IP (overrides config)")
    parser.add_argument("--port", type=int, help="Target port (overrides config)")
    parser.add_argument("--method", choices=["udp", "broadcast"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    cfg = load_config(args.config)
    if args.rate:
        cfg["transmitter"]["rate_hz"] = args.rate
    if args.target:
        cfg["transmitter"]["target_ip"] = args.target
    if args.port:
        cfg["transmitter"]["target_port"] = args.port
    if args.method:
        cfg["transmitter"]["method"] = args.method

    tx = Transmitter(cfg)

    def _stop(sig, frame):
        logger.info("Received signal %d, stopping...", sig)
        tx.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    tx.start()


if __name__ == "__main__":
    main()
