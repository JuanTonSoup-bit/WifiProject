"""Motion event logger: Python logging + JSONL file."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from pc.common.types import DSPFeatures, MotionEvent

logger = logging.getLogger(__name__)


class EventLogger:
    """
    Logs MotionEvents and DSPFeatures to:
    1. Python logging (human-readable)
    2. JSONL file (machine-readable, one JSON object per line)
    """

    def __init__(self, log_dir: str = "logs", enable_jsonl: bool = True) -> None:
        os.makedirs(log_dir, exist_ok=True)
        self._jsonl_fh: Optional[object] = None

        if enable_jsonl:
            path = os.path.join(log_dir, "events.jsonl")
            self._jsonl_fh = open(path, "a", encoding="utf-8", buffering=1)
            logger.info("Event JSONL logging to %s", path)

    def log_event(self, event: MotionEvent) -> None:
        logger.info(
            "EVENT %-14s score=%.3f confidence=%.2f duration=%.0f ms",
            event.event_type.upper(),
            event.motion_score,
            event.confidence,
            event.duration_ms,
        )
        if self._jsonl_fh is not None:
            record = event.to_dict()
            record["record_type"] = "event"
            self._jsonl_fh.write(json.dumps(record) + "\n")  # type: ignore[attr-defined]

    def log_features(self, features: DSPFeatures) -> None:
        if self._jsonl_fh is not None:
            record = {
                "record_type": "features",
                "timestamp_ns": features.timestamp_ns,
                "frame_seq": features.frame_seq,
                "motion_score": round(features.motion_score, 5),
                "is_baseline": features.is_baseline,
                "window_size": features.window_size,
            }
            self._jsonl_fh.write(json.dumps(record) + "\n")  # type: ignore[attr-defined]

    def flush(self) -> None:
        if self._jsonl_fh is not None:
            self._jsonl_fh.flush()  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._jsonl_fh is not None:
            self._jsonl_fh.close()  # type: ignore[attr-defined]
            self._jsonl_fh = None
