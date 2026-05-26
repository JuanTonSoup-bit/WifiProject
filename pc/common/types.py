"""Shared data types used across all PC-side pipeline modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class CSIFrame:
    """One parsed CSI measurement from the Pi."""

    timestamp_ns: int       # monotonic ns from Pi (not wall clock)
    seq_num: int            # packet sequence number for gap detection
    received_ns: int        # local PC monotonic time on receipt
    csi_matrix: np.ndarray  # shape: (n_rx, n_tx, n_subcarriers), dtype=complex64
    n_subcarriers: int
    n_rx: int
    n_tx: int
    bandwidth_mhz: int
    rssi: float
    noise_floor: float

    @property
    def amplitude(self) -> np.ndarray:
        """Per-element amplitude |H|, shape (n_rx, n_tx, n_subcarriers)."""
        return np.abs(self.csi_matrix)

    @property
    def amplitude_flat(self) -> np.ndarray:
        """Amplitude flattened to 1D, length n_rx*n_tx*n_subcarriers."""
        return np.abs(self.csi_matrix).ravel()

    @property
    def latency_ns(self) -> int:
        """Approximate one-way latency. Note: Pi and PC clocks are not synchronized."""
        return self.received_ns - self.timestamp_ns

    @property
    def n_features(self) -> int:
        return self.n_rx * self.n_tx * self.n_subcarriers


@dataclass
class DSPFeatures:
    """Extracted features from a sliding window of CSI frames."""

    timestamp_ns: int               # timestamp of the last frame in the window
    frame_seq: int                  # seq_num of the last frame in the window
    amplitude_mean: np.ndarray      # mean amplitude per feature, shape (n_features,)
    amplitude_var: np.ndarray       # variance per feature over the window, shape (n_features,)
    phase_diff: np.ndarray          # inter-subcarrier phase differences, shape (n_features - n_ant,)
    motion_score: float             # scalar 0.0–1.0; primary detection signal
    window_size: int                # number of frames in this window
    is_baseline: bool               # True while initial baseline is being collected


@dataclass
class MotionEvent:
    """A discrete motion or occupancy state-change event."""

    timestamp_ns: int
    event_type: str         # 'motion_start' | 'motion_end' | 'occupied' | 'vacant'
    confidence: float       # 0.0–1.0
    motion_score: float     # score that triggered the event
    duration_ms: float = 0.0   # for motion_end: how long motion lasted
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp_ns": self.timestamp_ns,
            "timestamp_s": self.timestamp_ns / 1e9,
            "event_type": self.event_type,
            "confidence": self.confidence,
            "motion_score": self.motion_score,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


@dataclass
class SystemStatus:
    """Snapshot of overall pipeline health for monitoring."""

    timestamp_s: float = field(default_factory=time.monotonic)
    ingestion_hz: float = 0.0
    ingestion_drops: int = 0
    ingestion_parse_errors: int = 0
    dsp_hz: float = 0.0
    dsp_latency_ms: float = 0.0
    detection_state: str = "UNKNOWN"
    current_score: float = 0.0
    current_threshold: float = 0.0
    occupancy: str = "UNKNOWN"
    total_motion_events: int = 0
    pi_connected: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()
