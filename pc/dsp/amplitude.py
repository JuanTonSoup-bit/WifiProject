"""Per-subcarrier amplitude extraction with EMA smoothing."""

from __future__ import annotations

from typing import Optional

import numpy as np

from pc.common.types import CSIFrame


class AmplitudeExtractor:
    """
    Extracts amplitude from a CSIFrame and applies per-feature EMA smoothing.

    Output shape: (n_rx * n_tx * n_subcarriers,) — flattened across all antenna pairs.
    """

    def __init__(self, smoothing_alpha: float = 0.15) -> None:
        if not 0.0 <= smoothing_alpha <= 1.0:
            raise ValueError(f"smoothing_alpha must be in [0, 1], got {smoothing_alpha}")
        self._alpha = smoothing_alpha
        self._ema: Optional[np.ndarray] = None

    def process(self, frame: CSIFrame) -> np.ndarray:
        """
        Returns EMA-smoothed amplitude, shape (n_rx*n_tx*n_subcarriers,).

        Uses: ema = alpha * raw + (1-alpha) * ema
        First frame initializes the EMA state with raw amplitude.
        """
        raw = np.abs(frame.csi_matrix).ravel().astype(np.float32)

        if self._ema is None or self._ema.shape != raw.shape:
            self._ema = raw.copy()
        else:
            self._ema = self._alpha * raw + (1.0 - self._alpha) * self._ema

        return self._ema.copy()

    def reset(self) -> None:
        """Reset EMA state (call on capture restart or channel change)."""
        self._ema = None

    @property
    def is_initialized(self) -> bool:
        return self._ema is not None
