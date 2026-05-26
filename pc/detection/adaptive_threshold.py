"""Adaptive threshold based on ambient score distribution."""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class AdaptiveThreshold:
    """
    Maintains a rolling window of motion scores from VACANT periods only,
    then computes threshold = mean + k * std.

    Only updates the buffer during VACANT state to avoid letting actual
    motion corrupt the baseline distribution.
    """

    def __init__(
        self,
        window_s: float = 60.0,
        sample_rate_hz: float = 20.0,
        k: float = 3.0,
        static_threshold: float = 0.25,
        min_threshold: float = 0.10,
        max_threshold: float = 0.80,
    ) -> None:
        self._window_size = max(10, int(window_s * sample_rate_hz))
        self._k = k
        self._static = static_threshold
        self._min = min_threshold
        self._max = max_threshold
        self._scores: deque[float] = deque(maxlen=self._window_size)

    def update(self, score: float, is_vacant: bool) -> None:
        """Add score to the distribution buffer only when the room is vacant."""
        if is_vacant:
            self._scores.append(score)

    def get_threshold(self) -> float:
        """
        Returns adaptive threshold if sufficient data, else static fallback.
        threshold = mean + k * std, clamped to [min, max].
        """
        if not self.has_sufficient_data:
            return self._static

        arr = np.array(self._scores, dtype=np.float32)
        t = float(arr.mean()) + self._k * float(arr.std())
        return float(np.clip(t, self._min, self._max))

    def get_clear_threshold(self) -> float:
        """Hysteresis gap: clear threshold is 60% of motion threshold."""
        return self.get_threshold() * 0.6

    @property
    def has_sufficient_data(self) -> bool:
        """True when at least 30% of the buffer is populated."""
        return len(self._scores) >= max(10, self._window_size // 3)

    @property
    def current_threshold(self) -> float:
        return self.get_threshold()

    @property
    def buffer_fill_pct(self) -> float:
        return len(self._scores) / self._window_size * 100.0

    @property
    def baseline_stats(self) -> dict:
        if not self._scores:
            return {"n": 0, "mean": None, "std": None}
        arr = np.array(self._scores, dtype=np.float32)
        return {
            "n": len(self._scores),
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "threshold": self.get_threshold(),
            "fill_pct": self.buffer_fill_pct,
        }
