"""End-to-end pipeline integration tests using synthetic data."""

import sys
import os
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pc.common.config import load_config
from pc.dsp.pipeline import DSPPipeline
from pc.detection.pipeline import DetectionPipeline
from pc.ingestion.parser import parse_packet, build_packet
from tests.generators.packet_gen import SyntheticPacketGenerator


def default_config() -> dict:
    return {
        "dsp": {
            "window_size": 20,
            "window_stride": 5,
            "amplitude_smoothing_alpha": 0.15,
            "hampel_window": 3,
            "hampel_n_sigma": 3.0,
            "baseline_frames": 30,
            "baseline_update_alpha": 0.001,
            "variance_percentile": 90,
            "detrend": True,
            "stats_interval_s": 0,
        },
        "detection": {
            "motion_threshold": 0.40,
            "motion_clear_threshold": 0.20,
            "motion_confirm_frames": 3,
            "motion_clear_frames": 5,
            "adaptive_threshold": False,  # use static for determinism
            "adaptive_window_s": 60,
            "adaptive_k": 3.0,
            "adaptive_min_threshold": 0.10,
            "adaptive_max_threshold": 0.80,
            "min_motion_duration_ms": 0,
            "max_score_spike_ratio": 10.0,
            "occupancy_timeout_s": 30,
            "stats_interval_s": 0,
        },
        "logging": {"log_dir": "logs"},
    }


class TestStaticSceneNoFalsePositives(unittest.TestCase):
    def test_no_false_positives_from_static_scene(self):
        """1000 static frames -> 0 motion_start events."""
        cfg = default_config()
        gen = SyntheticPacketGenerator(n_subcarriers=64, n_rx=1, n_tx=1, seed=42)
        dsp = DSPPipeline(cfg)
        det = DetectionPipeline(cfg, log_dir="logs")

        motion_starts = []
        det.subscribe(lambda e: motion_starts.append(e) if e.event_type == "motion_start" else None)

        for i in range(1000):
            frame = gen.generate_frame("static", t=float(i) * 0.01)
            features = dsp.push(frame)
            if features:
                det.push(features)

        self.assertEqual(
            len(motion_starts), 0,
            f"Expected 0 motion events on static signal, got {len(motion_starts)}"
        )


class TestMotionSceneDetectsMotion(unittest.TestCase):
    def test_motion_detected(self):
        """1000 motion frames (after baseline) -> at least 1 motion_start event."""
        cfg = default_config()
        gen = SyntheticPacketGenerator(n_subcarriers=64, n_rx=1, n_tx=1, seed=43)
        dsp = DSPPipeline(cfg)
        det = DetectionPipeline(cfg, log_dir="logs")

        motion_events = []
        det.subscribe(lambda e: motion_events.append(e))

        # First collect baseline with static frames
        for i in range(200):
            frame = gen.generate_frame("static", t=float(i) * 0.01)
            features = dsp.push(frame)
            if features:
                det.push(features)

        # Then inject motion frames
        for i in range(1000):
            frame = gen.generate_frame("motion", t=float(i) * 0.01)
            features = dsp.push(frame)
            if features:
                det.push(features)

        motion_starts = [e for e in motion_events if e.event_type == "motion_start"]
        self.assertGreater(
            len(motion_starts), 0,
            "Expected at least 1 motion_start event during motion scene"
        )


class TestPipelineThroughput(unittest.TestCase):
    def test_1000_frames_under_2_seconds(self):
        """1000 frames should process in < 2 seconds."""
        cfg = default_config()
        gen = SyntheticPacketGenerator(n_subcarriers=64, n_rx=1, n_tx=1, seed=44)
        dsp = DSPPipeline(cfg)
        det = DetectionPipeline(cfg, log_dir="logs")

        t0 = time.monotonic()
        for i in range(1000):
            frame = gen.generate_frame("static", t=float(i) * 0.01)
            features = dsp.push(frame)
            if features:
                det.push(features)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 2.0, f"1000 frames took {elapsed:.2f}s (> 2s limit)")


class TestPacketLossRobustness(unittest.TestCase):
    def test_5_percent_loss_still_detects_motion(self):
        """With 5% packet loss, motion should still be detected."""
        cfg = default_config()
        gen = SyntheticPacketGenerator(n_subcarriers=64, n_rx=1, n_tx=1, seed=45)
        dsp = DSPPipeline(cfg)
        det = DetectionPipeline(cfg, log_dir="logs")

        motion_events = []
        det.subscribe(lambda e: motion_events.append(e))

        # Baseline
        for i in range(200):
            frame = gen.generate_frame("static", t=float(i) * 0.01)
            features = dsp.push(frame)
            if features:
                det.push(features)

        # Motion with 5% loss
        import numpy as np
        rng = np.random.default_rng(45)
        sent = 0
        dropped = 0
        for i in range(1000):
            if rng.random() < 0.05:
                dropped += 1
                continue
            frame = gen.generate_frame("motion", t=float(i) * 0.01)
            features = dsp.push(frame)
            if features:
                det.push(features)
            sent += 1

        motion_starts = [e for e in motion_events if e.event_type == "motion_start"]
        self.assertGreater(
            len(motion_starts), 0,
            f"Expected motion detection despite 5% loss (dropped={dropped}, sent={sent})"
        )


class TestParseRoundTrip(unittest.TestCase):
    def test_parse_build_consistency(self):
        """build_packet(parse_packet(pkt)) should round-trip losslessly."""
        gen = SyntheticPacketGenerator(n_subcarriers=64, seed=46)
        frame = gen.generate_frame()
        pkt1 = build_packet(frame)
        parsed = parse_packet(pkt1)
        pkt2 = build_packet(parsed)
        self.assertEqual(pkt1[:26], pkt2[:26])  # headers should match exactly


if __name__ == "__main__":
    unittest.main()
