"""Records live CSIFrames to disk for later replay."""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
from typing import Optional

import numpy as np

from pc.common.types import CSIFrame

logger = logging.getLogger(__name__)


class CaptureRecorder:
    """
    Records CSIFrames to a binary file for later replay.

    File format per record:
      [8B record_length uint64][4B json_length uint32][JSON header bytes][raw csi_matrix bytes]
    """

    def __init__(
        self,
        output_path: str,
        max_frames: Optional[int] = None,
        max_duration_s: Optional[float] = None,
    ) -> None:
        self._path = output_path
        self._max_frames = max_frames
        self._max_duration = max_duration_s
        self._fh = None
        self._lock = threading.Lock()
        self._count = 0
        self._start_time: Optional[float] = None

    def __enter__(self) -> "CaptureRecorder":
        self._fh = open(self._path, "wb")
        self._start_time = time.monotonic()
        logger.info("Recording to %s", self._path)
        return self

    def __exit__(self, *args) -> None:
        self.stop()

    def record(self, frame: CSIFrame) -> bool:
        """
        Thread-safe. Returns False if recording limit reached.
        """
        with self._lock:
            if self._max_frames is not None and self._count >= self._max_frames:
                return False
            if (self._max_duration is not None and self._start_time is not None
                    and time.monotonic() - self._start_time >= self._max_duration):
                return False

            if self._fh is None:
                return False

            meta = {
                "timestamp_ns": frame.timestamp_ns,
                "seq_num": frame.seq_num,
                "received_ns": frame.received_ns,
                "n_rx": frame.n_rx,
                "n_tx": frame.n_tx,
                "n_subcarriers": frame.n_subcarriers,
                "bandwidth_mhz": frame.bandwidth_mhz,
                "rssi": frame.rssi,
                "noise_floor": frame.noise_floor,
            }
            json_bytes = json.dumps(meta).encode("utf-8")
            csi_bytes = frame.csi_matrix.astype(np.complex64).tobytes()

            json_len_bytes = struct.pack(">I", len(json_bytes))
            payload = json_len_bytes + json_bytes + csi_bytes
            record_len = struct.pack(">Q", len(payload))

            self._fh.write(record_len)
            self._fh.write(payload)
            self._count += 1
            return True

    def stop(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.flush()
                self._fh.close()
                self._fh = None
                logger.info("Capture saved: %d frames to %s", self._count, self._path)

    @property
    def frames_recorded(self) -> int:
        return self._count

    @property
    def duration_s(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time
