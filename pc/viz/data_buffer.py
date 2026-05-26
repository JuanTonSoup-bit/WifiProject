"""Thread-safe rolling data buffers for visualization."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import List, Optional, Tuple

import numpy as np

from pc.common.types import DSPFeatures, MotionEvent


class VizDataBuffer:
    """
    Thread-safe rolling buffers that decouple data production from rendering.

    Both the matplotlib app and the web UI read from this shared buffer.
    """

    def __init__(
        self,
        history_seconds: float = 30.0,
        sample_rate_hz: float = 20.0,
    ) -> None:
        capacity = max(10, int(history_seconds * sample_rate_hz))
        self._motion_scores: deque[Tuple[float, float]] = deque(maxlen=capacity)  # (t, score)
        self._amplitudes: deque[Tuple[float, np.ndarray]] = deque(maxlen=capacity)  # (t, amp)
        self._events: deque[MotionEvent] = deque(maxlen=100)
        self._event_times: deque[Tuple[float, str]] = deque(maxlen=100)  # (relative_t, event_type)
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

        self.current_state: str = "UNKNOWN"
        self.current_score: float = 0.0
        self.current_threshold: float = 0.25
        self.current_clear_threshold: float = 0.10
        self.pi_connected: bool = False
        self.ingestion_hz: float = 0.0
        self.buffer_drops: int = 0
        self.stats: dict = {}

    def add_features(self, features: DSPFeatures) -> None:
        t = time.monotonic() - self._start_time
        with self._lock:
            self.current_score = features.motion_score
            self._motion_scores.append((t, features.motion_score))
            self._amplitudes.append((t, features.amplitude_mean.copy()))
            if features.is_baseline:
                self.current_state = "BASELINE"

    def add_event(self, event: MotionEvent) -> None:
        with self._lock:
            self._events.append(event)
            # Store relative time at moment of receipt — independent of Pi/PC clock skew
            relative_t = time.monotonic() - self._start_time
            self._event_times.append((relative_t, event.event_type))
            if event.event_type == "motion_start":
                self.current_state = "MOTION"
            elif event.event_type in ("motion_end", "vacant"):
                self.current_state = "VACANT"
            elif event.event_type == "occupied":
                self.current_state = "OCCUPIED"

    def get_event_markers(self) -> List[Tuple[float, str]]:
        """Returns list of (relative_t_seconds, event_type) tuples."""
        with self._lock:
            return list(self._event_times)

    def update_stats(self, stats: dict) -> None:
        with self._lock:
            self.stats = stats.copy()
            self.ingestion_hz = float(stats.get("ingestion_hz", 0.0))
            self.pi_connected = self.ingestion_hz > 1.0

    def update_threshold(self, threshold: float, clear_threshold: float) -> None:
        with self._lock:
            self.current_threshold = threshold
            self.current_clear_threshold = clear_threshold

    def get_score_series(self) -> Tuple[np.ndarray, np.ndarray]:
        """Returns (timestamps_s, scores) arrays."""
        with self._lock:
            if not self._motion_scores:
                return np.array([]), np.array([])
            ts, scores = zip(*self._motion_scores)
            return np.array(ts, dtype=np.float32), np.array(scores, dtype=np.float32)

    def get_amplitude_matrix(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Returns (timestamps_s, amplitude_matrix) for heatmap rendering.
        amplitude_matrix shape: (n_frames, n_subcarriers)
        """
        with self._lock:
            if not self._amplitudes:
                return None, None
            ts, amps = zip(*self._amplitudes)
            try:
                matrix = np.array(amps, dtype=np.float32)
                return np.array(ts, dtype=np.float32), matrix
            except ValueError:
                return None, None

    def get_recent_events(self, n: int = 10) -> List[MotionEvent]:
        with self._lock:
            return list(self._events)[-n:]

    def get_latest_amplitude(self) -> Optional[np.ndarray]:
        with self._lock:
            if not self._amplitudes:
                return None
            return self._amplitudes[-1][1].copy()

    def get_snapshot(self) -> dict:
        """Thread-safe snapshot for SSE/web API."""
        with self._lock:
            ts, scores = ([], [])
            if self._motion_scores:
                ts, scores = zip(*self._motion_scores)
            events = [e.to_dict() for e in list(self._events)[-10:]]
            return {
                "state": self.current_state,
                "score": round(self.current_score, 4),
                "threshold": round(self.current_threshold, 4),
                "pi_connected": self.pi_connected,
                "ingestion_hz": round(self.ingestion_hz, 1),
                "score_history": list(scores[-200:]),
                "time_history": list(ts[-200:]),
                "events": events,
                "stats": self.stats,
            }
