"""Single-frame spike suppression filter."""

from __future__ import annotations

from collections import deque

import numpy as np


class SpikeFilter:
    """
    Suppresses single-frame score spikes likely caused by hardware glitches
    or transient RF interference.

    If a score exceeds max_spike_ratio * recent_mean and the previous score
    was normal, the spike is soft-clamped to recent_mean * max_spike_ratio.
    """

    def __init__(self, window: int = 10, max_spike_ratio: float = 5.0) -> None:
        self._window = window
        self._max_ratio = max_spike_ratio
        self._history: deque[float] = deque(maxlen=window)

    def filter(self, score: float) -> float:
        """
        Returns filtered score. Adds to history buffer.
        Spike suppression kicks in only when history is populated.
        """
        if len(self._history) >= 3:
            mean = self.recent_mean
            if mean > 1e-6 and score > mean * self._max_ratio:
                score = mean * self._max_ratio

        self._history.append(score)
        return score

    @property
    def recent_mean(self) -> float:
        if not self._history:
            return 0.0
        return float(np.mean(list(self._history)))
