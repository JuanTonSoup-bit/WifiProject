"""Motion state machine with hysteresis and debounce."""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Optional

from pc.common.types import DSPFeatures, MotionEvent

logger = logging.getLogger(__name__)


class MotionState(Enum):
    BASELINE_COLLECTION = auto()  # waiting for DSP baseline
    VACANT = auto()               # no motion
    MOTION_PENDING = auto()       # above threshold, counting confirm frames
    MOTION_ACTIVE = auto()        # confirmed motion
    CLEARING = auto()             # below clear threshold, counting clear frames


class MotionStateMachine:
    """
    Two-threshold hysteretic state machine with debounce.

    Transitions:
      BASELINE_COLLECTION -> VACANT            (when is_baseline becomes False)
      VACANT -> MOTION_PENDING                 (score > motion_threshold)
      MOTION_PENDING -> MOTION_ACTIVE          (confirm_frames consecutive above threshold)
      MOTION_PENDING -> VACANT                 (drops below threshold before confirm)
      MOTION_ACTIVE -> CLEARING               (score < clear_threshold)
      CLEARING -> VACANT                       (clear_frames consecutive below threshold)
      CLEARING -> MOTION_ACTIVE               (goes back above threshold)
    """

    def __init__(self, config: dict) -> None:
        det = config.get("detection", {})
        self._confirm_frames = int(det.get("motion_confirm_frames", 3))
        self._clear_frames = int(det.get("motion_clear_frames", 10))
        self._min_motion_ms = float(det.get("min_motion_duration_ms", 200))

        self.state = MotionState.BASELINE_COLLECTION
        self._pending_count = 0
        self._clearing_count = 0
        self._motion_start_ns: Optional[int] = None
        self._state_entered_ns: int = time.monotonic_ns()

    def update(
        self,
        features: DSPFeatures,
        threshold: float,
        clear_threshold: float,
    ) -> Optional[MotionEvent]:
        """
        Process one DSPFeatures sample. Returns a MotionEvent on state transition, else None.
        """
        score = features.motion_score
        ts = features.timestamp_ns

        if self.state == MotionState.BASELINE_COLLECTION:
            if not features.is_baseline:
                self._transition(MotionState.VACANT, ts)
                logger.info("Baseline established. Entering VACANT state.")
            return None

        if self.state == MotionState.VACANT:
            if score > threshold:
                self._pending_count = 1
                self._transition(MotionState.MOTION_PENDING, ts)
                logger.debug("VACANT -> MOTION_PENDING (score=%.3f, threshold=%.3f)", score, threshold)
            return None

        if self.state == MotionState.MOTION_PENDING:
            if score > threshold:
                self._pending_count += 1
                if self._pending_count >= self._confirm_frames:
                    self._motion_start_ns = ts
                    self._transition(MotionState.MOTION_ACTIVE, ts)
                    logger.info("Motion confirmed (score=%.3f)", score)
                    return MotionEvent(
                        timestamp_ns=ts,
                        event_type="motion_start",
                        confidence=min(1.0, score / max(threshold, 1e-6)),
                        motion_score=score,
                        metadata={"pending_count": self._pending_count},
                    )
            else:
                self._transition(MotionState.VACANT, ts)
                self._pending_count = 0
                logger.debug("MOTION_PENDING -> VACANT (score dropped, score=%.3f)", score)
            return None

        if self.state == MotionState.MOTION_ACTIVE:
            if score < clear_threshold:
                self._clearing_count = 1
                self._transition(MotionState.CLEARING, ts)
                logger.debug("MOTION_ACTIVE -> CLEARING (score=%.3f, clear_threshold=%.3f)", score, clear_threshold)
            return None

        if self.state == MotionState.CLEARING:
            if score >= threshold:
                self._clearing_count = 0
                self._transition(MotionState.MOTION_ACTIVE, ts)
                logger.debug("CLEARING -> MOTION_ACTIVE (score resurged, score=%.3f)", score)
                return None

            self._clearing_count += 1
            if self._clearing_count >= self._clear_frames:
                duration_ms = self._compute_duration_ms(ts)
                self._transition(MotionState.VACANT, ts)
                self._clearing_count = 0

                if duration_ms < self._min_motion_ms:
                    logger.debug(
                        "Suppressed short motion burst: %.1f ms < %.1f ms minimum",
                        duration_ms, self._min_motion_ms,
                    )
                    return None

                logger.info("Motion ended. Duration: %.0f ms", duration_ms)
                return MotionEvent(
                    timestamp_ns=ts,
                    event_type="motion_end",
                    confidence=1.0,
                    motion_score=score,
                    duration_ms=duration_ms,
                    metadata={"clear_count": self._clearing_count},
                )

        return None

    def _transition(self, new_state: MotionState, ts: int) -> None:
        self.state = new_state
        self._state_entered_ns = ts

    def _compute_duration_ms(self, end_ns: int) -> float:
        if self._motion_start_ns is None:
            return 0.0
        return (end_ns - self._motion_start_ns) / 1e6

    def force_state(self, state: MotionState) -> None:
        """Override state for testing or manual recovery."""
        self.state = state
        self._pending_count = 0
        self._clearing_count = 0
        self._motion_start_ns = None

    @property
    def time_in_state_ms(self) -> float:
        return (time.monotonic_ns() - self._state_entered_ns) / 1e6
