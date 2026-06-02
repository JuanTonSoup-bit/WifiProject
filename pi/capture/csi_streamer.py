"""Pi-side CSI capture daemon: UDP listener for up to N ESP32s, streams to PC."""

from __future__ import annotations

import argparse
import json
import queue
import signal
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
import numpy as np


MAGIC = b"\xC5\x49\x31\x00"
HEADER_FORMAT = ">4sQIHBBBbh2s"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

STATS_PATH = Path("/run/wifi-csi/streamer_stats.json")
QUEUE_MAXSIZE = 512

_DEFAULTS = {
    "listen_port": 5600,
    "pc_ip": "192.168.1.208",
    "udp_port": 5500,
    "stats_interval_s": 10.0,
}


@dataclass
class ParsedFrame:
    timestamp_ns: int
    rssi: int
    noise_floor: int
    bandwidth_mhz: int
    n_subcarriers: int
    csi_complex: np.ndarray


@dataclass
class _Stats:
    frames_sent: int = 0
    dropped_frames: int = 0
    start_time: float = field(default_factory=time.monotonic)

    def to_dict(self, rate_hz: float) -> dict:
        return {
            "rate_hz": round(rate_hz, 2),
            "dropped_frames": self.dropped_frames,
            "frames_sent": self.frames_sent,
            "uptime_s": round(time.monotonic() - self.start_time, 1),
        }


def _load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r") as fh:
        return yaml.safe_load(fh) or {}


def _get(cfg: dict, *keys, default):
    node = cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


class _TimestampUnwrapper:
    _WRAP_US: int = 1 << 32

    def __init__(self) -> None:
        self._offset_us: int = 0
        self._last_us: Optional[int] = None

    def to_ns(self, ts_us: int) -> int:
        if self._last_us is not None and ts_us < self._last_us - self._WRAP_US // 2:
            self._offset_us += self._WRAP_US
        self._last_us = ts_us
        return (self._offset_us + ts_us) * 1000


def _parse_line(line: str) -> Optional[ParsedFrame]:
    line = line.strip()
    if not line.startswith("CSI_DATA"):
        return None

    bracket_open = line.find("[")
    bracket_close = line.rfind("]")
    if bracket_open == -1 or bracket_close == -1:
        return None

    header_str = line[:bracket_open].rstrip(",")
    data_str = line[bracket_open + 1 : bracket_close]

    cols = header_str.split(",")
    if len(cols) < 24:
        return None

    try:
        rssi            = int(cols[3])
        bandwidth_raw   = int(cols[7])
        noise_floor     = int(cols[14])
        local_ts_us     = int(cols[18])
        csi_len         = int(cols[22])
        first_word_invalid = int(cols[23])
    except (ValueError, IndexError):
        return None

    if first_word_invalid:
        return None

    bandwidth_mhz = 40 if bandwidth_raw == 1 else 20
    n_subcarriers = csi_len // 2

    if not data_str.strip():
        return None

    try:
        raw_ints = [int(x) for x in data_str.split(",") if x.strip()]
    except ValueError:
        return None

    if len(raw_ints) != csi_len:
        return None

    raw = np.array(raw_ints, dtype=np.int8)
    i_vals = raw[1::2].astype(np.float32)
    q_vals = raw[0::2].astype(np.float32)
    csi_complex = i_vals + 1j * q_vals

    return ParsedFrame(
        timestamp_ns=local_ts_us * 1000,
        rssi=rssi,
        noise_floor=noise_floor,
        bandwidth_mhz=bandwidth_mhz,
        n_subcarriers=n_subcarriers,
        csi_complex=csi_complex,
    )


class UDPListener(threading.Thread):
    """Listens on one UDP port for CSI_DATA lines from any number of ESP32s."""

    def __init__(
        self,
        listen_port: int,
        frame_queue: "queue.Queue[ParsedFrame]",
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True, name="udp-listener")
        self._port = listen_port
        self._queue = frame_queue
        self._stop = stop_event
        self._unwrappers: dict[str, _TimestampUnwrapper] = {}

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        sock.bind(("", self._port))
        sock.settimeout(0.5)

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            src_ip = addr[0]
            if src_ip not in self._unwrappers:
                self._unwrappers[src_ip] = _TimestampUnwrapper()

            try:
                line = data.decode("ascii", errors="replace")
            except Exception:
                continue

            frame = _parse_line(line)
            if frame is None:
                continue

            frame.timestamp_ns = self._unwrappers[src_ip].to_ns(frame.timestamp_ns // 1000)
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                pass

        sock.close()


def _pack(frame: ParsedFrame, seq_num: int) -> bytes:
    header = struct.pack(
        HEADER_FORMAT,
        MAGIC,
        frame.timestamp_ns,
        seq_num,
        frame.n_subcarriers,
        1,
        1,
        frame.bandwidth_mhz,
        max(-128, min(127, frame.rssi)),
        max(-32768, min(32767, frame.noise_floor)),
        b"\x00\x00",
    )
    floats = np.empty(len(frame.csi_complex) * 2, dtype=np.float32)
    floats[0::2] = frame.csi_complex.real
    floats[1::2] = frame.csi_complex.imag
    return header + floats.astype("<f4").tobytes()


def _write_stats(stats: _Stats, rate_hz: float) -> None:
    try:
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats.to_dict(rate_hz)))
        tmp.replace(STATS_PATH)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="WiFi CSI UDP streaming daemon")
    parser.add_argument("--config", default="/etc/wifi-csi/config.yaml")
    parser.add_argument("--listen-port", type=int, default=None)
    args = parser.parse_args()

    cfg = _load_config(args.config)

    listen_port    = args.listen_port or _get(cfg, "esp32", "listen_port", default=_DEFAULTS["listen_port"])
    pc_ip          = _get(cfg, "network", "pc_ip",       default=_DEFAULTS["pc_ip"])
    udp_port       = _get(cfg, "network", "udp_port",    default=_DEFAULTS["udp_port"])
    stats_interval = _get(cfg, "pi", "stats_interval_s", default=_DEFAULTS["stats_interval_s"])

    stop_event = threading.Event()

    def _shutdown(sig, frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    frame_queue: "queue.Queue[ParsedFrame]" = queue.Queue(maxsize=QUEUE_MAXSIZE)

    listener = UDPListener(listen_port, frame_queue, stop_event)
    listener.start()
    print(f"Listening for ESP32s on UDP port {listen_port}, forwarding to {pc_ip}:{udp_port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (pc_ip, int(udp_port))

    stats = _Stats()
    seq_num = 0
    last_stats_time = time.monotonic()
    window_frames = 0

    while not stop_event.is_set():
        try:
            frame: ParsedFrame = frame_queue.get(timeout=0.5)
        except queue.Empty:
            now = time.monotonic()
            elapsed = now - last_stats_time
            if elapsed >= stats_interval:
                _write_stats(stats, window_frames / elapsed if elapsed > 0 else 0.0)
                window_frames = 0
                last_stats_time = now
            continue

        try:
            sock.sendto(_pack(frame, seq_num), dest)
            seq_num = (seq_num + 1) & 0xFFFFFFFF
            stats.frames_sent += 1
            window_frames += 1
        except OSError:
            stats.dropped_frames += 1

        now = time.monotonic()
        elapsed = now - last_stats_time
        if elapsed >= stats_interval:
            _write_stats(stats, window_frames / elapsed if elapsed > 0 else 0.0)
            window_frames = 0
            last_stats_time = now

    sock.close()
    listener.join(timeout=5.0)


if __name__ == "__main__":
    main()
