"""
Packet loss simulator for stress-testing the pipeline.

Supports three loss models:
- random:   each packet dropped independently at probability `loss_rate`
- burst:    Gilbert-Elliott two-state model (bursty losses)
- periodic: drop every N-th packet (deterministic, for repeatable tests)
"""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LossStats:
    sent: int = 0
    received: int = 0
    dropped: int = 0
    actual_loss_rate: float = 0.0
    max_consecutive_drops: int = 0

    def to_dict(self) -> dict:
        return {
            "sent": self.sent,
            "received": self.received,
            "dropped": self.dropped,
            "actual_loss_rate": round(self.actual_loss_rate, 4),
            "max_consecutive_drops": self.max_consecutive_drops,
        }


class PacketLossSimulator:
    """
    Drops packets according to one of three statistical models.

    For Gilbert-Elliott "burst" mode:
      - Good state: probability `loss_rate` of dropping each packet
      - Bad state:  probability 0.9 of dropping each packet
      - Transitions: Good->Bad p_gb=0.02, Bad->Good p_bg=0.30
      These default rates produce ~5-10% loss with realistic burst lengths.
    """

    def __init__(
        self,
        loss_model: str = "random",
        loss_rate: float = 0.01,
        period: int = 100,
        seed: int = 42,
    ) -> None:
        if loss_model not in ("random", "burst", "periodic", "none"):
            raise ValueError(f"Unknown loss_model: {loss_model}")
        self._model = loss_model
        self._rate = float(loss_rate)
        self._period = int(period)
        self._rng = np.random.default_rng(seed)

        # Gilbert-Elliott state
        self._bad_state = False
        self._p_gb = 0.02
        self._p_bg = 0.30
        self._bad_loss_rate = 0.9

        # Counters
        self._packet_index = 0
        self._consecutive_drops = 0
        self._max_consecutive = 0
        self._dropped = 0

    def should_drop(self) -> bool:
        """Decide whether the current packet should be dropped."""
        self._packet_index += 1
        dropped = False

        if self._model == "none":
            dropped = False
        elif self._model == "random":
            dropped = self._rng.random() < self._rate
        elif self._model == "periodic":
            dropped = (self._packet_index % self._period) == 0
        elif self._model == "burst":
            if self._bad_state:
                dropped = self._rng.random() < self._bad_loss_rate
                if self._rng.random() < self._p_bg:
                    self._bad_state = False
            else:
                dropped = self._rng.random() < self._rate
                if self._rng.random() < self._p_gb:
                    self._bad_state = True

        if dropped:
            self._dropped += 1
            self._consecutive_drops += 1
            if self._consecutive_drops > self._max_consecutive:
                self._max_consecutive = self._consecutive_drops
        else:
            self._consecutive_drops = 0

        return dropped

    def filter_list(self, packets: list) -> list:
        """Drop packets from a list according to the model. Returns surviving packets."""
        return [p for p in packets if not self.should_drop()]

    def process_queue(
        self,
        input_q: queue.Queue,
        output_q: queue.Queue,
        duration_s: float,
    ) -> LossStats:
        """
        Forward packets from input_q to output_q, dropping per the model.
        Stops after duration_s. Returns LossStats.
        """
        sent = 0
        received = 0
        deadline = time.monotonic() + duration_s

        while time.monotonic() < deadline:
            try:
                pkt = input_q.get(timeout=0.1)
            except queue.Empty:
                continue
            sent += 1
            if self.should_drop():
                continue
            try:
                output_q.put(pkt, timeout=0.5)
                received += 1
            except queue.Full:
                pass

        return LossStats(
            sent=sent,
            received=received,
            dropped=sent - received,
            actual_loss_rate=(sent - received) / max(sent, 1),
            max_consecutive_drops=self._max_consecutive,
        )

    def stats(self) -> LossStats:
        return LossStats(
            sent=self._packet_index,
            received=self._packet_index - self._dropped,
            dropped=self._dropped,
            actual_loss_rate=self._dropped / max(self._packet_index, 1),
            max_consecutive_drops=self._max_consecutive,
        )

    def reset(self) -> None:
        self._packet_index = 0
        self._consecutive_drops = 0
        self._max_consecutive = 0
        self._dropped = 0
        self._bad_state = False
