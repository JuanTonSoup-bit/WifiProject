"""Unit tests for the motion state machine."""

import sys
import os
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import numpy as np

from pc.common.types import DSPFeatures
from pc.detection.state_machine import MotionState, MotionStateMachine

DUMMY_ARRAY = np.zeros(64, dtype=np.float32)


def make_features(score: float, is_baseline: bool = False, seq: int = 0) -> DSPFeatures:
    return DSPFeatures(
        timestamp_ns=time.monotonic_ns(),
        frame_seq=seq,
        amplitude_mean=DUMMY_ARRAY,
        amplitude_var=DUMMY_ARRAY,
        phase_diff=DUMMY_ARRAY,
        motion_score=score,
        window_size=50,
        is_baseline=is_baseline,
    )


CONFIG = {
    "detection": {
        "motion_threshold": 0.25,
        "motion_clear_threshold": 0.10,
        "motion_confirm_frames": 3,
        "motion_clear_frames": 5,
        "min_motion_duration_ms": 0,  # disable for unit tests
    }
}

THRESHOLD = 0.25
CLEAR_THRESHOLD = 0.10


class TestBaselineCollection(unittest.TestCase):
    def test_no_events_during_baseline(self):
        fsm = MotionStateMachine(CONFIG)
        self.assertEqual(fsm.state, MotionState.BASELINE_COLLECTION)
        for _ in range(10):
            event = fsm.update(make_features(0.9, is_baseline=True), THRESHOLD, CLEAR_THRESHOLD)
            self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.BASELINE_COLLECTION)

    def test_transitions_to_vacant_when_baseline_ends(self):
        fsm = MotionStateMachine(CONFIG)
        event = fsm.update(make_features(0.05, is_baseline=False), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.VACANT)


class TestVacantToMotion(unittest.TestCase):
    def _reach_vacant(self) -> MotionStateMachine:
        fsm = MotionStateMachine(CONFIG)
        fsm.update(make_features(0.0, is_baseline=False), THRESHOLD, CLEAR_THRESHOLD)
        self.assertEqual(fsm.state, MotionState.VACANT)
        return fsm

    def test_single_spike_stays_pending(self):
        fsm = self._reach_vacant()
        event = fsm.update(make_features(0.5), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.MOTION_PENDING)

    def test_confirm_frames_triggers_motion_start(self):
        fsm = self._reach_vacant()
        event = None
        for _ in range(CONFIG["detection"]["motion_confirm_frames"]):
            event = fsm.update(make_features(0.5), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "motion_start")
        self.assertEqual(fsm.state, MotionState.MOTION_ACTIVE)

    def test_pending_rollback_if_score_drops(self):
        fsm = self._reach_vacant()
        fsm.update(make_features(0.5), THRESHOLD, CLEAR_THRESHOLD)
        self.assertEqual(fsm.state, MotionState.MOTION_PENDING)
        event = fsm.update(make_features(0.05), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.VACANT)


class TestHysteresis(unittest.TestCase):
    def _reach_active(self) -> MotionStateMachine:
        fsm = MotionStateMachine(CONFIG)
        fsm.update(make_features(0.0, is_baseline=False), THRESHOLD, CLEAR_THRESHOLD)
        for _ in range(CONFIG["detection"]["motion_confirm_frames"]):
            fsm.update(make_features(0.5), THRESHOLD, CLEAR_THRESHOLD)
        self.assertEqual(fsm.state, MotionState.MOTION_ACTIVE)
        return fsm

    def test_score_below_motion_but_above_clear_stays_active(self):
        fsm = self._reach_active()
        # Score between clear_threshold (0.10) and motion_threshold (0.25)
        event = fsm.update(make_features(0.15), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.MOTION_ACTIVE)

    def test_score_below_clear_enters_clearing(self):
        fsm = self._reach_active()
        event = fsm.update(make_features(0.05), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.CLEARING)

    def test_clearing_resurgence_returns_to_active(self):
        fsm = self._reach_active()
        fsm.update(make_features(0.05), THRESHOLD, CLEAR_THRESHOLD)
        self.assertEqual(fsm.state, MotionState.CLEARING)
        event = fsm.update(make_features(0.5), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNone(event)
        self.assertEqual(fsm.state, MotionState.MOTION_ACTIVE)

    def test_clearing_completes_emits_motion_end(self):
        fsm = self._reach_active()
        events = []
        for _ in range(CONFIG["detection"]["motion_clear_frames"] + 2):
            e = fsm.update(make_features(0.05), THRESHOLD, CLEAR_THRESHOLD)
            if e:
                events.append(e)
        motion_ends = [e for e in events if e.event_type == "motion_end"]
        self.assertEqual(len(motion_ends), 1)
        self.assertEqual(fsm.state, MotionState.VACANT)


class TestMinDuration(unittest.TestCase):
    def test_short_burst_suppressed(self):
        cfg = {
            "detection": {
                "motion_threshold": 0.25,
                "motion_clear_threshold": 0.10,
                "motion_confirm_frames": 2,
                "motion_clear_frames": 2,
                "min_motion_duration_ms": 10000,  # 10 seconds — will suppress
            }
        }
        fsm = MotionStateMachine(cfg)
        fsm.update(make_features(0.0, is_baseline=False), THRESHOLD, CLEAR_THRESHOLD)
        # Confirm motion
        for _ in range(2):
            fsm.update(make_features(0.5), THRESHOLD, CLEAR_THRESHOLD)
        # Now clear quickly
        event = None
        for _ in range(3):
            event = fsm.update(make_features(0.01), THRESHOLD, CLEAR_THRESHOLD)
        # Should be suppressed because duration < min_motion_duration_ms
        self.assertIsNone(event)


class TestFullCycle(unittest.TestCase):
    def test_full_motion_start_end_cycle(self):
        fsm = MotionStateMachine(CONFIG)
        fsm.update(make_features(0.0, is_baseline=False), THRESHOLD, CLEAR_THRESHOLD)

        start_event = None
        for _ in range(CONFIG["detection"]["motion_confirm_frames"]):
            start_event = fsm.update(make_features(0.8), THRESHOLD, CLEAR_THRESHOLD)
        self.assertIsNotNone(start_event)
        self.assertEqual(start_event.event_type, "motion_start")

        end_events = []
        for _ in range(CONFIG["detection"]["motion_clear_frames"] + 2):
            e = fsm.update(make_features(0.01), THRESHOLD, CLEAR_THRESHOLD)
            if e:
                end_events.append(e)
        motion_ends = [e for e in end_events if e.event_type == "motion_end"]
        self.assertEqual(len(motion_ends), 1)
        self.assertGreater(motion_ends[0].duration_ms, 0)


if __name__ == "__main__":
    unittest.main()
