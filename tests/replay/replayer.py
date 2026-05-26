"""Replay recorded CSI captures at controllable speed."""

from __future__ import annotations

import json
import logging
import queue
import socket
import struct
import time
from typing import Callable, List, Optional

import numpy as np

from pc.common.types import CSIFrame
from pc.ingestion.parser import build_packet

logger = logging.getLogger(__name__)


class CaptureReplayer:
    """
    Loads a capture file and replays frames at controllable speed.

    File format written by CaptureRecorder:
      For each frame: [8B record_length uint64][JSON header bytes][raw csi_matrix bytes]
    """

    def __init__(self, frames: List[CSIFrame]) -> None:
        self._frames = frames

    @classmethod
    def from_file(cls, path: str) -> "CaptureReplayer":
        frames = []
        with open(path, "rb") as fh:
            while True:
                length_bytes = fh.read(8)
                if not length_bytes or len(length_bytes) < 8:
                    break
                record_len = struct.unpack(">Q", length_bytes)[0]
                record = fh.read(record_len)
                if len(record) < record_len:
                    break

                # Split: first 4 bytes of record = JSON length
                json_len = struct.unpack(">I", record[:4])[0]
                json_bytes = record[4: 4 + json_len]
                csi_bytes = record[4 + json_len:]

                try:
                    meta = json.loads(json_bytes.decode("utf-8"))
                    n_rx = int(meta["n_rx"])
                    n_tx = int(meta["n_tx"])
                    n_sub = int(meta["n_subcarriers"])
                    csi_raw = np.frombuffer(csi_bytes, dtype=np.complex64)
                    csi_matrix = csi_raw.reshape(n_rx, n_tx, n_sub)
                    frame = CSIFrame(
                        timestamp_ns=int(meta["timestamp_ns"]),
                        seq_num=int(meta["seq_num"]),
                        received_ns=int(meta.get("received_ns", meta["timestamp_ns"])),
                        csi_matrix=csi_matrix,
                        n_subcarriers=n_sub,
                        n_rx=n_rx,
                        n_tx=n_tx,
                        bandwidth_mhz=int(meta.get("bandwidth_mhz", 20)),
                        rssi=float(meta.get("rssi", -65)),
                        noise_floor=float(meta.get("noise_floor", -95)),
                    )
                    frames.append(frame)
                except (json.JSONDecodeError, KeyError, ValueError, struct.error) as exc:
                    logger.warning("Skipping corrupt record: %s", exc)
                    continue

        logger.info("Loaded %d frames from %s", len(frames), path)
        return cls(frames)

    def replay_to_queue(
        self,
        q: queue.Queue,
        speed: float = 1.0,
        loop: bool = False,
    ) -> None:
        """
        Replay frames into a queue at `speed`x real-time.
        speed=0: as fast as possible.
        """
        while True:
            prev_ts: Optional[int] = None
            for frame in self._frames:
                if prev_ts is not None and speed > 0:
                    dt_s = (frame.timestamp_ns - prev_ts) / 1e9
                    if dt_s > 0:
                        time.sleep(dt_s / speed)
                prev_ts = frame.timestamp_ns
                try:
                    q.put(frame, timeout=1.0)
                except queue.Full:
                    logger.debug("Replay queue full, dropping frame")

            if not loop:
                break

    def replay_to_callback(
        self,
        callback: Callable[[CSIFrame], None],
        speed: float = 1.0,
    ) -> None:
        prev_ts: Optional[int] = None
        for frame in self._frames:
            if prev_ts is not None and speed > 0:
                dt_s = (frame.timestamp_ns - prev_ts) / 1e9
                if dt_s > 0:
                    time.sleep(dt_s / speed)
            prev_ts = frame.timestamp_ns
            callback(frame)

    def replay_to_udp(self, host: str, port: int, speed: float = 1.0) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        prev_ts: Optional[int] = None
        sent = 0
        for frame in self._frames:
            if prev_ts is not None and speed > 0:
                dt_s = (frame.timestamp_ns - prev_ts) / 1e9
                if dt_s > 0:
                    time.sleep(dt_s / speed)
            prev_ts = frame.timestamp_ns
            pkt = build_packet(frame)
            sock.sendto(pkt, (host, port))
            sent += 1
        sock.close()
        logger.info("Replay to UDP complete: %d packets sent to %s:%d", sent, host, port)

    def get_frame(self, index: int) -> CSIFrame:
        return self._frames[index]

    @property
    def n_frames(self) -> int:
        return len(self._frames)

    @property
    def duration_s(self) -> float:
        if len(self._frames) < 2:
            return 0.0
        return (self._frames[-1].timestamp_ns - self._frames[0].timestamp_ns) / 1e9

    @property
    def sample_rate_hz(self) -> float:
        if self.duration_s == 0:
            return 0.0
        return (self.n_frames - 1) / self.duration_s
