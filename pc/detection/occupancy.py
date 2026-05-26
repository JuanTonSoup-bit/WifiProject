"""Higher-level room occupancy tracking."""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Optional

from pc.common.types import MotionEvent

logger = logging.getLogger(__name__)


class OccupancyState(Enum):
    UNKNOWN = auto()
    OCCUPIED = auto()
    VACANT = auto()


class OccupancyTracker:
    """
    Infers room occupancy from the motion event stream.

    Logic:
    - Any motion_start event -> OCCUPIED
    - No motion for timeout_s -> VACANT (check_timeout must be called regularly)
    - Emits 'occupied' and 'vacant' MotionEvents on transitions.
    """

    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout_ns = int(timeout_s * 1e9)
        self.state = OccupancyState.UNKNOWN
        self._last_motion_ns: Optional[int] = None

    def update(
        self, event: Optional[MotionEvent], current_ns: int
    ) -> Optional[MotionEvent]:
        """
        Process a motion event (or None for a heartbeat tick).
        Returns an occupancy event if the occupancy state changed.
        """
        if event is not None and event.event_type == "motion_start":
            self._last_motion_ns = event.timestamp_ns
            if self.state != OccupancyState.OCCUPIED:
                self.state = OccupancyState.OCCUPIED
                logger.info("Room OCCUPIED")
                return MotionEvent(
                    timestamp_ns=event.timestamp_ns,
                    event_type="occupied",
                    confidence=event.confidence,
                    motion_score=event.motion_score,
                )

        return self.check_timeout(current_ns)

    def check_timeout(self, current_ns: int) -> Optional[MotionEvent]:
        """
        Call periodically to detect timeout-based vacant transitions.
        Returns a 'vacant' event if timeout expired, else None.
        """
        if self.state != OccupancyState.OCCUPIED:
            return None
        if self._last_motion_ns is None:
            return None
        if current_ns - self._last_motion_ns >= self._timeout_ns:
            self.state = OccupancyState.VACANT
            logger.info(
                "Room VACANT (no motion for %.0f s)",
                self._timeout_ns / 1e9,
            )
            return MotionEvent(
                timestamp_ns=current_ns,
                event_type="vacant",
                confidence=1.0,
                motion_score=0.0,
                duration_ms=(current_ns - self._last_motion_ns) / 1e6,
            )
        return None

    @property
    def seconds_since_motion(self) -> Optional[float]:
        if self._last_motion_ns is None:
            return None
        return (time.monotonic_ns() - self._last_motion_ns) / 1e9
