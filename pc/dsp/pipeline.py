"""DSP pipeline orchestrator: CSIFrame -> DSPFeatures."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Callable, List, Optional

from pc.common.types import CSIFrame, DSPFeatures
from pc.dsp.features import FeatureExtractor
from pc.dsp.window import SlidingWindow

logger = logging.getLogger(__name__)


@dataclass
class DSPStats:
    frames_processed: int = 0
    windows_emitted: int = 0
    processing_time_ms_ema: float = 0.0
    last_motion_score: float = 0.0
    baseline_established: bool = False
    queue_drops: int = 0

    def record_processing_time(self, elapsed_s: float) -> None:
        ms = elapsed_s * 1000.0
        if self.processing_time_ms_ema == 0.0:
            self.processing_time_ms_ema = ms
        else:
            self.processing_time_ms_ema = 0.1 * ms + 0.9 * self.processing_time_ms_ema

    def to_dict(self) -> dict:
        return {
            "frames_processed": self.frames_processed,
            "windows_emitted": self.windows_emitted,
            "processing_time_ms": round(self.processing_time_ms_ema, 3),
            "last_motion_score": round(self.last_motion_score, 4),
            "baseline_established": self.baseline_established,
            "queue_drops": self.queue_drops,
        }


class DSPPipeline:
    """
    Top-level DSP orchestrator.

    Receives CSIFrames, emits DSPFeatures whenever the sliding window fills
    and the stride is met.

    Can run in pull mode (push() returns features) or push mode (start_worker
    reads from a queue and dispatches via callbacks).
    """

    def __init__(self, config: dict, output_queue: Optional[Queue] = None) -> None:
        dsp = config.get("dsp", {})
        self._window = SlidingWindow(
            size=int(dsp.get("window_size", 50)),
            stride=int(dsp.get("window_stride", 5)),
        )
        self._extractor = FeatureExtractor(config)
        self._output_queue = output_queue
        self._callbacks: List[Callable[[DSPFeatures], None]] = []
        self._stats = DSPStats()
        self._stats_interval = float(dsp.get("stats_interval_s", 10.0))
        self._last_stats_time = 0.0
        self._start_time = time.monotonic()

    def push(self, frame: CSIFrame) -> Optional[DSPFeatures]:
        """
        Process one frame. Returns DSPFeatures if a window was emitted, else None.
        Thread-safe for single-producer use.
        """
        self._stats.frames_processed += 1

        window = self._window.push(frame)
        if window is None:
            return None

        t0 = time.monotonic()
        try:
            features = self._extractor.extract(window)
        except Exception:
            logger.exception("Feature extraction failed")
            return None

        self._stats.record_processing_time(time.monotonic() - t0)
        self._stats.windows_emitted += 1
        self._stats.last_motion_score = features.motion_score
        self._stats.baseline_established = self._extractor.baseline_established

        self._dispatch(features)
        self._maybe_log_stats()
        return features

    def subscribe(self, callback: Callable[[DSPFeatures], None]) -> None:
        self._callbacks.append(callback)

    def reset_baseline(self) -> None:
        self._extractor.reset_baseline()
        self._window.reset()
        logger.info("DSP baseline reset")

    def get_stats(self) -> dict:
        return self._stats.to_dict()

    def start_worker(self, input_queue: Queue) -> threading.Thread:
        """Start a background thread that consumes CSIFrames from input_queue."""
        t = threading.Thread(
            target=self._worker_loop,
            args=(input_queue,),
            name="dsp-worker",
            daemon=True,
        )
        t.start()
        return t

    def _worker_loop(self, input_queue: Queue) -> None:
        while True:
            try:
                frame = input_queue.get(timeout=1.0)
                if frame is None:  # sentinel
                    break
                self.push(frame)
            except Empty:
                continue
            except Exception:
                logger.exception("DSP worker error")

    def _dispatch(self, features: DSPFeatures) -> None:
        if self._output_queue is not None:
            if not self._output_queue.full():
                self._output_queue.put_nowait(features)
            else:
                self._stats.queue_drops += 1

        for cb in self._callbacks:
            try:
                cb(features)
            except Exception:
                logger.exception("DSP callback error")

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if self._stats_interval > 0 and now - self._last_stats_time >= self._stats_interval:
            s = self._stats
            logger.info(
                "DSP stats: frames=%d windows=%d score=%.3f proc=%.2fms baseline=%s",
                s.frames_processed, s.windows_emitted, s.last_motion_score,
                s.processing_time_ms_ema, s.baseline_established,
            )
            self._last_stats_time = now
