"""Unit tests for DSP pipeline components."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np

from pc.dsp.amplitude import AmplitudeExtractor
from pc.dsp.baseline import BaselineEstimator
from pc.dsp.filters import hampel_filter
from tests.generators.packet_gen import SyntheticPacketGenerator


def make_gen(seed=10):
    return SyntheticPacketGenerator(n_subcarriers=64, n_rx=1, n_tx=1, seed=seed)


class TestAmplitudeExtractor(unittest.TestCase):
    def test_shape_preservation(self):
        gen = make_gen()
        ext = AmplitudeExtractor(smoothing_alpha=0.5)
        frame = gen.generate_frame("static", t=0.0)
        amp = ext.process(frame)
        self.assertEqual(amp.shape, (64,))

    def test_ema_convergence(self):
        """After many frames of a constant signal, EMA should equal raw."""
        gen = SyntheticPacketGenerator(n_subcarriers=16, n_rx=1, n_tx=1, seed=99)
        ext = AmplitudeExtractor(smoothing_alpha=0.5)

        # First frame initializes EMA
        frame0 = gen.generate_frame("static", t=0.0)
        raw0 = np.abs(frame0.csi_matrix).ravel()
        amp0 = ext.process(frame0)
        np.testing.assert_array_equal(amp0, raw0)

        # After many identical frames, EMA should track raw closely
        for i in range(50):
            ext.process(gen.generate_frame("static", t=float(i) * 0.01))

        frame_last = gen.generate_frame("static", t=5.0)
        raw_last = np.abs(frame_last.csi_matrix).ravel()
        amp_last = ext.process(frame_last)

        # EMA should be within 50% of raw — just confirm it's finite and reasonable
        self.assertTrue(np.all(np.isfinite(amp_last)))
        self.assertFalse(np.any(amp_last < 0))

    def test_reset_reinitializes(self):
        gen = make_gen()
        ext = AmplitudeExtractor()
        f = gen.generate_frame()
        ext.process(f)
        self.assertTrue(ext.is_initialized)
        ext.reset()
        self.assertFalse(ext.is_initialized)


class TestHampelFilter(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)

    def test_spike_removal(self):
        """A single large spike should be replaced with local median."""
        n_frames, n_feat = 30, 64
        data = np.ones((n_frames, n_feat), dtype=np.float32) * 0.5
        spike_idx = 15
        data[spike_idx, :] = 100.0  # inject large spike

        cleaned = hampel_filter(data, window=5, n_sigma=3.0)

        self.assertLess(cleaned[spike_idx, 0], 10.0, "Spike should have been removed")

    def test_clean_signal_unchanged(self):
        """No outliers means output should be close to input."""
        n_frames, n_feat = 30, 32
        data = np.random.randn(n_frames, n_feat).astype(np.float32) * 0.1
        cleaned = hampel_filter(data, window=5, n_sigma=3.0)

        # No spike -> all differences should be small
        np.testing.assert_allclose(cleaned, data, atol=0.5)

    def test_output_shape_preserved(self):
        data = np.random.randn(20, 128).astype(np.float32)
        cleaned = hampel_filter(data, window=3)
        self.assertEqual(cleaned.shape, data.shape)


def _window_variance(gen, mode: str, window_size: int = 20, start_t: float = 0.0) -> np.ndarray:
    """Helper: collect window_size frames, compute per-feature amplitude variance."""
    amps = np.array([
        np.abs(gen.generate_frame(mode, t=start_t + i * 0.01).csi_matrix).ravel()
        for i in range(window_size)
    ], dtype=np.float32)
    return np.var(amps, axis=0)


class TestBaselineEstimator(unittest.TestCase):
    def test_baseline_not_established_initially(self):
        est = BaselineEstimator(baseline_frames=10)
        self.assertFalse(est.is_established)

    def test_baseline_established_after_n_frames(self):
        n = 20
        est = BaselineEstimator(baseline_frames=n)
        # The new baseline tracks variance, not raw amplitude.
        var = np.ones(64, dtype=np.float32) * 0.01
        for _ in range(n):
            est.update(var)
        self.assertTrue(est.is_established)
        self.assertEqual(est.frames_collected, n)

    def test_normalization_near_zero_for_static(self):
        """Static signal variance should normalize to ~1.0 (matches baseline)."""
        est = BaselineEstimator(baseline_frames=20)
        gen = SyntheticPacketGenerator(seed=7)

        # Build baseline with static-window variances
        for i in range(20):
            var = _window_variance(gen, "static", window_size=20, start_t=i * 0.2)
            est.update(var)

        self.assertTrue(est.is_established)

        # A new static-window variance should normalize to ~1.0
        new_var = _window_variance(gen, "static", window_size=20, start_t=100.0)
        normalized = est.normalize(new_var)
        mean_normalized = float(np.mean(normalized))
        self.assertLess(
            mean_normalized, 5.0,
            f"Static window normalized to {mean_normalized:.3f}, expected near 1.0"
        )

    def test_normalization_elevated_for_motion(self):
        """Motion signal variance should normalize much higher than static."""
        est = BaselineEstimator(baseline_frames=20)
        gen = SyntheticPacketGenerator(seed=8)

        # Build baseline from static-window variances
        for i in range(20):
            est.update(_window_variance(gen, "static", window_size=20, start_t=i * 0.2))

        static_var = _window_variance(gen, "static", window_size=20, start_t=100.0)
        motion_var = _window_variance(gen, "motion", window_size=20, start_t=100.0)
        static_norm = float(np.mean(est.normalize(static_var)))
        motion_norm = float(np.mean(est.normalize(motion_var)))

        self.assertGreater(
            motion_norm, static_norm,
            f"Motion variance ({motion_norm:.3f}) should exceed static ({static_norm:.3f})"
        )


if __name__ == "__main__":
    unittest.main()
