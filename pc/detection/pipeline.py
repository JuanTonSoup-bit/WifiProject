"""Detection pipeline orchestrator: DSPFeatures -> MotionEvents."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable, List, Optional

from pc.common.types import DSPFeatures, MotionEvent
from pc.detection.adaptive_threshold import AdaptiveThreshold
from pc.detection.event_logger import EventLogger
from pc.detection.occupancy import OccupancyTracker
from pc.detection.spike_filter import SpikeFilter
from pc.detection.state_machine import MotionState, MotionStateMachine

logger = logging.getLogger(__name__)


@dataclass
class DetectionStats:
    features_processed: int = 0
    motion_events_fired: int = 0
    spikes_suppressed: int = 0
    current_state: str = "BASELINE_COLLECTION"
    current_score: float = 0.0
    current_threshold: float = 0.0
    occupancy_state: str = "UNKNOWN"
    last_motion_event_ns: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "features_processed": self.features_processed,
            "motion_events": self.motion_events_fired,
            "spikes_suppressed": self.spikes_suppressed,
            "state": self.current_state,
            "score": round(self.current_score, 4),
            "threshold": round(self.current_threshold, 4),
            "occupancy": self.occupancy_state,
            "last_motion_event_ns": self.last_motion_event_ns,
        }


class DetectionPipeline:
    """
    Full detection chain:
    DSPFeatures -> SpikeFilter -> AdaptiveThreshold -> StateMachine
                -> OccupancyTracker -> MotionEvent callbacks
    """

    def __init__(self, config: dict, log_dir: str = "logs") -> None:
        det = config.get("detection", {})

        self._spike_filter = SpikeFilter(
            window=10,
            max_spike_ratio=float(det.get("max_score_spike_ratio", 5.0)),
        )
        self._threshold = AdaptiveThreshold(
            window_s=float(det.get("adaptive_window_s", 60.0)),
            k=float(det.get("adaptive_k", 3.0)),
            static_threshold=float(det.get("motion_threshold", 0.25)),
            min_threshold=float(det.get("adaptive_min_threshold", 0.10)),
            max_threshold=float(det.get("adaptive_max_threshold", 0.80)),
        ) if det.get("adaptive_threshold", True) else None

        self._static_threshold = float(det.get("motion_threshold", 0.25))
        self._static_clear = float(det.get("motion_clear_threshold", 0.10))
        self._adaptive_enabled = bool(det.get("adaptive_threshold", True))

        self._fsm = MotionStateMachine(config)
        self._occupancy = OccupancyTracker(
            timeout_s=float(det.get("occupancy_timeout_s", 30.0))
        )
        self._event_logger = EventLogger(log_dir=log_dir)
        self._callbacks: List[Callable[[MotionEvent], None]] = []
        self._stats = DetectionStats()
        self._stats_lock = threading.Lock()
        self._stats_interval = float(det.get("stats_interval_s", 10.0))
        self._last_stats_time = 0.0

    def push(self, features: DSPFeatures) -> List[MotionEvent]:
        """Process one DSPFeatures. Returns list of MotionEvents (0 or more)."""
        events: List[MotionEvent] = []

        raw_score = features.motion_score
        filtered_score = self._spike_filter.filter(raw_score)
        if filtered_score < raw_score - 1e-6:
            with self._stats_lock:
                self._stats.spikes_suppressed += 1

        features_filtered = DSPFeatures(
            timestamp_ns=features.timestamp_ns,
            frame_seq=features.frame_seq,
            amplitude_mean=features.amplitude_mean,
            amplitude_var=features.amplitude_var,
            phase_diff=features.phase_diff,
            motion_score=filtered_score,
            window_size=features.window_size,
            is_baseline=features.is_baseline,
        )

        threshold = self._get_threshold()
        clear_threshold = self._get_clear_threshold()

        fsm_event = self._fsm.update(features_filtered, threshold, clear_threshold)
        if fsm_event:
            events.append(fsm_event)
            occupancy_event = self._occupancy.update(fsm_event, features.timestamp_ns)
            if occupancy_event:
                events.append(occupancy_event)
        else:
            occupancy_event = self._occupancy.update(None, features.timestamp_ns)
            if occupancy_event:
                events.append(occupancy_event)

        is_vacant = self._fsm.state in (MotionState.VACANT, MotionState.BASELINE_COLLECTION)
        if self._threshold is not None:
            self._threshold.update(filtered_score, is_vacant)

        for event in events:
            self._event_logger.log_event(event)
            with self._stats_lock:
                self._stats.motion_events_fired += 1
                self._stats.last_motion_event_ns = event.timestamp_ns

        with self._stats_lock:
            self._stats.features_processed += 1
            self._stats.current_state = self._fsm.state.name
            self._stats.current_score = filtered_score
            self._stats.current_threshold = threshold
            self._stats.occupancy_state = self._occupancy.state.name

        for event in events:
            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception:
                    logger.exception("Detection callback error")

        self._maybe_log_stats()
        return events

    def subscribe(self, callback: Callable[[MotionEvent], None]) -> None:
        self._callbacks.append(callback)

    def reset(self) -> None:
        self._fsm.force_state(MotionState.BASELINE_COLLECTION)
        logger.info("Detection pipeline reset")

    def get_stats(self) -> dict:
        with self._stats_lock:
            return self._stats.to_dict()

    def get_current_state(self) -> dict:
        return {
            "state": self._fsm.state.name,
            "score": self._stats.current_score,
            "threshold": self._stats.current_threshold,
            "occupancy": self._occupancy.state.name,
            "time_in_state_ms": round(self._fsm.time_in_state_ms, 1),
            "threshold_stats": self._threshold.baseline_stats if self._threshold else {},
        }

    def start_worker(self, input_queue: Queue) -> threading.Thread:
        t = threading.Thread(
            target=self._worker_loop,
            args=(input_queue,),
            name="detection-worker",
            daemon=True,
        )
        t.start()
        return t

    def shutdown(self) -> None:
        self._event_logger.close()

    def _get_threshold(self) -> float:
        if self._adaptive_enabled and self._threshold is not None:
            return self._threshold.get_threshold()
        return self._static_threshold

    def _get_clear_threshold(self) -> float:
        if self._adaptive_enabled and self._threshold is not None:
            return self._threshold.get_clear_threshold()
        return self._static_clear

    def _worker_loop(self, input_queue: Queue) -> None:
        while True:
            try:
                feat = input_queue.get(timeout=1.0)
                if feat is None:
                    break
                self.push(feat)
            except Empty:
                continue
            except Exception:
                logger.exception("Detection worker error")

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if self._stats_interval > 0 and now - self._last_stats_time >= self._stats_interval:
            s = self.get_stats()
            logger.info(
                "Detection stats: processed=%d events=%d state=%s score=%.3f threshold=%.3f occ=%s",
                s["features_processed"], s["motion_events"], s["state"],
                s["score"], s["threshold"], s["occupancy"],
            )
            self._last_stats_time = now
