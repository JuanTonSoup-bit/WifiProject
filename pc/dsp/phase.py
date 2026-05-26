"""Phase extraction and sanitization for WiFi CSI."""

from __future__ import annotations

import numpy as np

from pc.common.types import CSIFrame


class PhaseSanitizer:
    """
    Extracts sanitized phase features from a CSIFrame.

    Raw WiFi CSI phase is corrupted by:
    - Sampling frequency offset (SFO): linear phase ramp across subcarriers
    - Packet detection delay (PDD): constant phase offset per packet

    Without hardware timestamps we can't fully recover calibrated phase, but
    inter-subcarrier phase differences are more stable than raw phase and
    partially cancel SFO and PDD effects.
    """

    def process(self, frame: CSIFrame) -> np.ndarray:
        """
        Returns inter-subcarrier phase difference array.

        Steps:
        1. Extract complex phase: angle = np.angle(csi_matrix)
        2. Unwrap along subcarrier axis (axis=2)
        3. Compute adjacent differences along subcarrier axis
        4. Flatten to 1D

        Output shape: (n_rx * n_tx * (n_subcarriers - 1),)
        """
        angle = np.angle(frame.csi_matrix)  # (n_rx, n_tx, n_subcarriers)
        unwrapped = np.unwrap(angle, axis=2)
        diff = np.diff(unwrapped, axis=2)    # (n_rx, n_tx, n_subcarriers - 1)
        return diff.ravel().astype(np.float32)

    def sanitize_linear_trend(self, phase: np.ndarray) -> np.ndarray:
        """
        Remove linear SFO trend from a 1D phase array using least-squares fit.

        Fits phase = a*k + b and returns the residuals.
        """
        n = len(phase)
        if n < 2:
            return phase
        k = np.arange(n, dtype=np.float32)
        # Least-squares: [k, 1] * [a, b]^T = phase
        A = np.column_stack([k, np.ones(n)])
        coeffs, *_ = np.linalg.lstsq(A, phase, rcond=None)
        trend = A @ coeffs
        return (phase - trend).astype(np.float32)
