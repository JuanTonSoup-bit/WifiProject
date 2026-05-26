"""Sliding window manager for CSI frames."""

from __future__ import annotations

from collections import deque
from typing import List, Optional

from pc.common.types import CSIFrame


class SlidingWindow:
    """
    Maintains a rolling buffer of CSIFrames and emits windows at a fixed stride.

    size:   number of frames per window (e.g. 50 = 0.5s at 100 Hz)
    stride: emit a new window every N new frames (e.g. 5 = 20 Hz output)
    """

    def __init__(self, size: int, stride: int) -> None:
        if size < 1:
            raise ValueError(f"size must be >= 1, got {size}")
        if stride < 1:
            raise ValueError(f"stride must be >= 1, got {stride}")
        self._size = size
        self._stride = stride
        self._buf: deque[CSIFrame] = deque(maxlen=size)
        self._frames_since_emit = 0

    def push(self, frame: CSIFrame) -> Optional[List[CSIFrame]]:
        """
        Add a frame to the window.

        Returns a snapshot list of all buffered frames when the stride is met
        and the window is full. Returns None otherwise.
        """
        self._buf.append(frame)
        self._frames_since_emit += 1

        if self._frames_since_emit >= self._stride and len(self._buf) == self._size:
            self._frames_since_emit = 0
            return list(self._buf)

        return None

    def reset(self) -> None:
        self._buf.clear()
        self._frames_since_emit = 0

    @property
    def is_full(self) -> bool:
        return len(self._buf) == self._size

    @property
    def current_size(self) -> int:
        return len(self._buf)

    @property
    def size(self) -> int:
        return self._size

    @property
    def stride(self) -> int:
        return self._stride
