"""Baseline estimator for ambient CSI channel normalization."""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class BaselineEstimator:
    """
    Two-phase baseline estimator for per-feature *variance* normalization.

    Tracks the expected per-feature variance when the room is empty, then
    normalizes each new window's variance against that baseline. The ratio is
    the motion signal: ~1.0 when static, >>1.0 when there is motion.

    Phase 1 (collection): Accumulate baseline_frames variance vectors.
        When full, compute the baseline median (robust to startup transients).
        is_established becomes True.

    Phase 2 (tracking): Slowly update with EMA to track environment drift
        (AC cycles, temperature changes, etc.) — only safe to call this during
        VACANT periods, otherwise the baseline absorbs the motion.
    """

    def __init__(
        self,
        baseline_frames: int = 300,
        update_alpha: float = 0.001,
    ) -> None:
        self._n = baseline_frames
        self._alpha = update_alpha

        self._collection: List[np.ndarray] = []
        self._baseline_var: Optional[np.ndarray] = None  # per-feature variance baseline
        self._baseline_mean: Optional[np.ndarray] = None
        self._established = False

    def update(self, amplitude_var: np.ndarray, amplitude_mean: Optional[np.ndarray] = None) -> None:
        """
        Add one window's variance vector to the baseline.

        amplitude_var: per-feature variance from one window, shape (n_features,)
        amplitude_mean: optional per-feature mean (kept for diagnostics)
        """
        if not self._established:
            self._collection.append(amplitude_var.astype(np.float32))
            if amplitude_mean is not None and self._baseline_mean is None:
                self._baseline_mean = amplitude_mean.astype(np.float32)

            if len(self._collection) >= self._n:
                stacked = np.array(self._collection)  # (n, n_features)
                # Median is robust to startup spikes in the variance estimate.
                self._baseline_var = np.median(stacked, axis=0) + 1e-9
                self._collection = []
                self._established = True
                logger.info(
                    "Baseline established from %d windows. Median variance range: [%.6f, %.6f]",
                    self._n,
                    float(self._baseline_var.min()),
                    float(self._baseline_var.max()),
                )
        else:
            assert self._baseline_var is not None
            v = amplitude_var.astype(np.float32)
            self._baseline_var = (1.0 - self._alpha) * self._baseline_var + self._alpha * v
            self._baseline_var = np.maximum(self._baseline_var, 1e-9)
            if amplitude_mean is not None and self._baseline_mean is not None:
                self._baseline_mean = (
                    (1.0 - self._alpha) * self._baseline_mean
                    + self._alpha * amplitude_mean.astype(np.float32)
                )

    def normalize(self, amplitude_var: np.ndarray) -> np.ndarray:
        """
        Returns amplitude_var / baseline_var per feature (clamped to [0, 50]).

        Result interpretation:
          ~1.0 means current variance matches baseline (no motion)
          >>1.0 means elevated variance (motion)
        """
        if not self._established:
            # During baseline collection, return zeros so motion_score stays low.
            return np.zeros_like(amplitude_var, dtype=np.float32)
        normalized = amplitude_var / (self._baseline_var + 1e-9)  # type: ignore[operator]
        return np.clip(normalized, 0.0, 50.0).astype(np.float32)

    # Backward-compatible property aliases used by signal_check + tests
    @property
    def baseline_std(self) -> Optional[np.ndarray]:
        if self._baseline_var is None:
            return None
        return np.sqrt(self._baseline_var)

    def reset(self) -> None:
        self._collection = []
        self._baseline_mean = None
        self._baseline_var = None
        self._established = False

    @property
    def is_established(self) -> bool:
        return self._established

    @property
    def frames_collected(self) -> int:
        return len(self._collection) if not self._established else self._n

    @property
    def baseline_mean(self) -> Optional[np.ndarray]:
        return self._baseline_mean

    @property
    def baseline_var(self) -> Optional[np.ndarray]:
        return self._baseline_var
