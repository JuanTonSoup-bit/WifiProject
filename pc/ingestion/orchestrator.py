"""Ingestion pipeline orchestrator: wires receiver + ring buffer + dispatch."""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional

from pc.common.types import CSIFrame
from pc.ingestion.receiver import CSIReceiver
from pc.ingestion.ring_buffer import RingBuffer

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    Top-level ingestion coordinator.

    Starts the UDP receiver and provides both pull (get_frame) and
    push (subscribe) access to incoming CSIFrames.
    """

    def __init__(self, config: dict) -> None:
        ing = config.get("ingestion", {})
        self._buffer: RingBuffer[CSIFrame] = RingBuffer(
            maxsize=int(ing.get("ring_buffer_size", 500))
        )
        self._receiver = CSIReceiver(config, self._buffer)
        self._callbacks: List[Callable[[CSIFrame], None]] = []
        self._dispatch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Bind UDP socket and start receiving."""
        self._receiver.start()
        if self._callbacks:
            self._stop_event.clear()
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_loop, name="ingestion-dispatch", daemon=True
            )
            self._dispatch_thread.start()

    def stop(self) -> None:
        """Graceful shutdown."""
        self._stop_event.set()
        self._receiver.stop()
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=3.0)

    def subscribe(self, callback: Callable[[CSIFrame], None]) -> None:
        """Register a callback invoked for each new frame (push mode)."""
        self._callbacks.append(callback)

    def get_frame(self, timeout: float = 1.0) -> Optional[CSIFrame]:
        """Pull one frame from the buffer. Returns None on timeout."""
        return self._buffer.get(timeout=timeout)

    def get_frames_batch(self, n: int, timeout: float = 0.1) -> List[CSIFrame]:
        """Pull up to n frames from the buffer."""
        return self._buffer.get_batch(n=n, timeout=timeout)

    def get_stats(self) -> dict:
        return {
            "receiver": self._receiver.get_stats(),
            "buffer_qsize": self._buffer.qsize(),
            "buffer_maxsize": self._buffer.maxsize,
            "buffer_drops": self._buffer.drop_count,
        }

    @property
    def receive_rate_hz(self) -> float:
        return self._receiver.receive_rate_hz

    def _dispatch_loop(self) -> None:
        """Background thread that pushes frames to subscribers."""
        while not self._stop_event.is_set():
            frame = self._buffer.get(timeout=0.5)
            if frame is None:
                continue
            for cb in self._callbacks:
                try:
                    cb(frame)
                except Exception:
                    logger.exception("Exception in ingestion callback")
