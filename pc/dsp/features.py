"""Feature extraction from a sliding window of CSI frames."""

from __future__ import annotations

import logging
from typing import List

import numpy as np

from pc.common.types import CSIFrame, DSPFeatures
from pc.dsp.amplitude import AmplitudeExtractor
from pc.dsp.baseline import BaselineEstimator
from pc.dsp.filters import hampel_filter, linear_detrend
from pc.dsp.phase import PhaseSanitizer

logger = logging.getLogger(__name__)


class FeatureExtractor:
    """
    Converts a window of CSIFrames into a DSPFeatures instance.

    Processing chain per window:
      1. Extract smoothed amplitude for each frame
      2. Stack into (n_frames, n_features) matrix
      3. Hampel filter (outlier removal)
      4. Linear detrend (remove slow drift within window)
      5. Compute per-feature variance and mean
      6. Extract phase diff from last frame
      7. Normalize variance by baseline
      8. Compute scalar motion_score
    """

    def __init__(self, config: dict) -> None:
        dsp = config.get("dsp", {})
        self._amp = AmplitudeExtractor(
            smoothing_alpha=float(dsp.get("amplitude_smoothing_alpha", 0.15))
        )
        self._phase = PhaseSanitizer()
        self._baseline = BaselineEstimator(
            baseline_frames=int(dsp.get("baseline_frames", 300)),
            update_alpha=float(dsp.get("baseline_update_alpha", 0.001)),
        )
        self._hampel_window = int(dsp.get("hampel_window", 5))
        self._hampel_sigma = float(dsp.get("hampel_n_sigma", 3.0))
        self._detrend = bool(dsp.get("detrend", True))
        self._var_percentile = float(dsp.get("variance_percentile", 90))

    def extract(self, window: List[CSIFrame]) -> DSPFeatures:
        """Extract DSPFeatures from a window of frames."""
        if not window:
            raise ValueError("Cannot extract features from empty window")

        last_frame = window[-1]

        # Step 1: stack per-frame amplitudes
        amp_list = [self._amp.process(f) for f in window]
        amp_matrix = np.array(amp_list, dtype=np.float32)  # (n_frames, n_features)

        # Step 2: Hampel filter
        if amp_matrix.shape[0] >= 3:
            amp_matrix = hampel_filter(
                amp_matrix,
                window=self._hampel_window,
                n_sigma=self._hampel_sigma,
            )

        # Step 3: detrend
        if self._detrend and amp_matrix.shape[0] >= 2:
            amp_matrix = linear_detrend(amp_matrix)

        # Step 4: stats
        amp_mean = amp_matrix.mean(axis=0)
        amp_var = np.var(amp_matrix, axis=0, ddof=1 if amp_matrix.shape[0] > 1 else 0)

        # Step 5: phase diff from last frame
        phase_diff = self._phase.process(last_frame)

        # Step 6: baseline update (tracks expected per-feature *variance* when
        # static), then normalize the current window's variance against it.
        self._baseline.update(amp_var, amp_mean)
        normalized_var = self._baseline.normalize(amp_var)

        # Step 7: motion score
        motion_score = self._compute_motion_score(normalized_var)

        return DSPFeatures(
            timestamp_ns=last_frame.timestamp_ns,
            frame_seq=last_frame.seq_num,
            amplitude_mean=amp_mean,
            amplitude_var=amp_var,
            phase_diff=phase_diff,
            motion_score=motion_score,
            window_size=len(window),
            is_baseline=not self._baseline.is_established,
        )

    def _compute_motion_score(self, normalized_var: np.ndarray) -> float:
        """
        Scalar motion score from normalized per-feature variance.

        `normalized_var` is variance divided by baseline variance, so a value of
        ~1.0 means "matches baseline" (no motion). We subtract 1.0 to make the
        score reflect *excess* variance above baseline, then soft-clip with tanh.

        Result:
          - static scene (variance ≈ baseline) → score ≈ 0
          - moderate motion (5× baseline variance) → score ≈ 0.76
          - strong motion (10× baseline variance) → score ≈ 0.95
        """
        if normalized_var.size == 0:
            return 0.0
        raw = float(np.percentile(normalized_var, self._var_percentile))
        excess = max(0.0, raw - 1.0)
        return float(np.tanh(excess / 5.0))

    def reset_baseline(self) -> None:
        self._baseline.reset()
        self._amp.reset()

    @property
    def baseline_established(self) -> bool:
        return self._baseline.is_established

    @property
    def baseline(self) -> BaselineEstimator:
        return self._baseline
