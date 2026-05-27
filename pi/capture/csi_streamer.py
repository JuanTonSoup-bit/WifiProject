#!/usr/bin/env python3
"""
Nexmon CSI capture and UDP streaming daemon.

Reads CSI packets emitted by Nexmon on localhost UDP, re-packages them in
the shared wire format, and streams to the PC over Ethernet UDP.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import socket
import struct
import sys
import time
from collections import deque
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NEXMON_MAGIC = b'\x11\x11\x11\x11'
NEXMON_LOCAL_PORT = 5500          # Nexmon writes here by default
SHARED_MAGIC = b'\xC5\x49\x31\x00'

# Shared wire format header: magic(4) + ts_ns(8) + seq(4) + n_sub(2) + n_rx(1)
#   + n_tx(1) + bw(1) + rssi(1) + noise(2) + pad(2)
SHARED_HEADER_FMT = ">4sQIHBBBbh2s"
SHARED_HEADER_SIZE = struct.calcsize(SHARED_HEADER_FMT)  # 26

# Nexmon CSI header (binary layout from nexmon_csi source)
NEXMON_HDR_FMT = ">IbB6sHHHH"
NEXMON_HDR_SIZE = struct.calcsize(NEXMON_HDR_FMT)

SUBCARRIERS_BY_BW = {20: 64, 40: 128, 80: 256}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(log_level: str, log_dir: str = "/var/log/wifi-csi") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "csi_streamer.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
        ),
    ]
    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)
    return logging.getLogger("csi_streamer")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "network": {"pc_ip": "192.168.1.208", "udp_port": 5500},
    "csi": {"interface": "wlan0", "channel": 6, "bandwidth": 20},
    "pi": {"log_level": "INFO", "stats_interval_s": 10},
}

def load_config(path: str) -> dict:
    if not os.path.exists(path):
        return DEFAULT_CONFIG
    with open(path, "r") as fh:
        cfg = yaml.safe_load(fh) or {}
    # Merge with defaults
    for section, defaults in DEFAULT_CONFIG.items():
        cfg.setdefault(section, {})
        for k, v in defaults.items():
            cfg[section].setdefault(k, v)
    return cfg


# ---------------------------------------------------------------------------
# Nexmon packet parsing
# ---------------------------------------------------------------------------
def parse_nexmon_packet(data: bytes, bandwidth: int) -> Optional[dict]:
    """
    Parse a raw Nexmon CSI UDP packet.

    Returns dict with rssi, n_rx, n_tx, n_subcarriers, csi_bytes, or None on error.
    """
    if len(data) < NEXMON_HDR_SIZE:
        return None

    try:
        (magic, rssi, frame_ctrl, src_mac, seq_num,
         core_spatial, chanspec, chip) = struct.unpack_from(NEXMON_HDR_FMT, data, 0)
    except struct.error:
        return None

    if magic != struct.unpack(">I", NEXMON_MAGIC)[0]:
        return None

    n_subcarriers = SUBCARRIERS_BY_BW.get(bandwidth, 64)
    csi_offset = NEXMON_HDR_SIZE
    expected_csi_bytes = n_subcarriers * 4  # 2 bytes real + 2 bytes imag per subcarrier (int16)
    if len(data) < csi_offset + expected_csi_bytes:
        return None

    csi_raw = data[csi_offset: csi_offset + expected_csi_bytes]
    return {
        "rssi": rssi,
        "n_rx": 1,
        "n_tx": 1,
        "n_subcarriers": n_subcarriers,
        "bandwidth": bandwidth,
        "csi_int16_bytes": csi_raw,
    }


def csi_int16_to_float32(csi_int16: bytes, n_subcarriers: int) -> bytes:
    """
    Convert Nexmon int16 complex CSI to float32 complex and return as LE bytes.
    Nexmon stores: [real0, imag0, real1, imag1, ...] as int16.
    Output: [real0, imag0, real1, imag1, ...] as float32 LE.
    """
    ints = struct.unpack(f"<{n_subcarriers * 2}h", csi_int16)
    floats = [float(v) for v in ints]
    return struct.pack(f"<{len(floats)}f", *floats)


def build_shared_packet(
    nexmon: dict,
    seq_num: int,
    timestamp_ns: int,
) -> bytes:
    """Build the shared-format UDP packet from a parsed Nexmon frame."""
    n_rx = nexmon["n_rx"]
    n_tx = nexmon["n_tx"]
    n_sub = nexmon["n_subcarriers"]
    bw = nexmon["bandwidth"]
    rssi = nexmon["rssi"]

    csi_float32 = csi_int16_to_float32(nexmon["csi_int16_bytes"], n_sub)

    header = struct.pack(
        SHARED_HEADER_FMT,
        SHARED_MAGIC,
        timestamp_ns,
        seq_num,
        n_sub,
        n_rx,
        n_tx,
        bw,
        rssi,
        -95,          # noise floor placeholder
        b'\x00\x00',
    )
    return header + csi_float32


# ---------------------------------------------------------------------------
# Ring buffer (absorb bursts before forwarding)
# ---------------------------------------------------------------------------
class SendBuffer:
    def __init__(self, maxsize: int = 200) -> None:
        self._q: deque[bytes] = deque(maxlen=maxsize)
        self._dropped = 0

    def put(self, pkt: bytes) -> None:
        if len(self._q) >= self._q.maxlen:  # type: ignore[arg-type]
            self._dropped += 1
        self._q.append(pkt)

    def get_all(self) -> list:
        items = list(self._q)
        self._q.clear()
        return items

    @property
    def dropped(self) -> int:
        return self._dropped


# ---------------------------------------------------------------------------
# Main streamer
# ---------------------------------------------------------------------------
class CSIStreamer:
    def __init__(self, config: dict, logger: logging.Logger) -> None:
        net = config["network"]
        csi = config["csi"]
        pi = config["pi"]

        self._pc_ip = net["pc_ip"]
        self._pc_port = int(net["udp_port"])
        self._bandwidth = int(csi["bandwidth"])
        self._stats_interval = float(pi["stats_interval_s"])

        self._recv_sock: Optional[socket.socket] = None
        self._send_sock: Optional[socket.socket] = None
        self._running = False
        self._seq = 0
        self._buf = SendBuffer()
        self._log = logger

        self._frames_captured = 0
        self._frames_sent = 0
        self._start_time = 0.0
        self._last_stats = 0.0

    def start(self) -> None:
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._recv_sock.settimeout(1.0)
        self._recv_sock.bind(("127.0.0.1", NEXMON_LOCAL_PORT))

        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._running = True
        self._start_time = time.monotonic()
        self._last_stats = self._start_time
        self._log.info("CSI streamer started. Forwarding to %s:%d", self._pc_ip, self._pc_port)

        try:
            self._run()
        finally:
            self._cleanup()

    def _run(self) -> None:
        while self._running:
            try:
                data, _ = self._recv_sock.recvfrom(8192)
            except socket.timeout:
                continue

            timestamp_ns = time.monotonic_ns()
            nexmon = parse_nexmon_packet(data, self._bandwidth)
            if nexmon is None:
                continue

            self._frames_captured += 1
            pkt = build_shared_packet(nexmon, self._seq, timestamp_ns)
            self._seq = (self._seq + 1) & 0xFFFFFFFF

            try:
                self._send_sock.sendto(pkt, (self._pc_ip, self._pc_port))
                self._frames_sent += 1
            except OSError as exc:
                self._log.warning("Send error: %s", exc)

            self._maybe_log_stats()

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats >= self._stats_interval:
            elapsed = now - self._start_time
            rate = self._frames_sent / max(elapsed, 1)
            self._log.info(
                "Streamer stats: captured=%d sent=%d dropped=%d rate=%.1fHz uptime=%.0fs",
                self._frames_captured, self._frames_sent, self._buf.dropped, rate, elapsed,
            )
            self._last_stats = now

    def _cleanup(self) -> None:
        if self._recv_sock:
            self._recv_sock.close()
        if self._send_sock:
            self._send_sock.close()
        self._log.info("Streamer stopped.")

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Nexmon CSI UDP streamer")
    parser.add_argument("--config", default="/etc/wifi-csi/config.yaml")
    parser.add_argument("--pc-ip")
    parser.add_argument("--port", type=int)
    parser.add_argument("--bandwidth", type=int, choices=[20, 40, 80])
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.pc_ip:
        cfg["network"]["pc_ip"] = args.pc_ip
    if args.port:
        cfg["network"]["udp_port"] = args.port
    if args.bandwidth:
        cfg["csi"]["bandwidth"] = args.bandwidth
    if args.log_level:
        cfg["pi"]["log_level"] = args.log_level

    log = setup_logging(cfg["pi"]["log_level"])
    streamer = CSIStreamer(cfg, log)

    def _handle_signal(sig, frame):
        log.info("Received signal %d, shutting down...", sig)
        streamer.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    streamer.start()


if __name__ == "__main__":
    main()
