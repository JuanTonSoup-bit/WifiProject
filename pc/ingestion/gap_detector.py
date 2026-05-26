"""Sequence number gap detector with uint32 rollover handling."""

from __future__ import annotations

from typing import Optional

UINT32_MAX = 0xFFFF_FFFF
ROLLOVER_THRESHOLD = UINT32_MAX // 2  # half-window for rollover detection


class GapDetector:
    """
    Tracks packet sequence numbers and detects gaps.

    Handles uint32 rollover (0xFFFFFFFF -> 0x00000000).
    """

    def __init__(self, max_gap: int = 5) -> None:
        self._max_gap = max_gap
        self._last_seq: Optional[int] = None
        self._total_gaps = 0
        self._total_missing = 0

    def check(self, seq_num: int) -> Optional[int]:
        """
        Update state with new sequence number.
        Returns gap size if a gap was detected, else None.
        A gap of 1 means one packet was dropped.
        """
        if self._last_seq is None:
            self._last_seq = seq_num
            return None

        gap = self._compute_gap(self._last_seq, seq_num)
        self._last_seq = seq_num

        if gap <= 0:
            # Duplicate or reorder — ignore
            return None

        missing = gap - 1
        if missing > 0:
            self._total_gaps += 1
            self._total_missing += missing
            return missing

        return None

    def _compute_gap(self, prev: int, curr: int) -> int:
        """Compute forward distance from prev to curr accounting for rollover."""
        if curr >= prev:
            return curr - prev
        # Rollover: curr wrapped around
        return (UINT32_MAX - prev) + curr + 1

    def reset(self) -> None:
        self._last_seq = None

    @property
    def total_gaps(self) -> int:
        return self._total_gaps

    @property
    def total_missing_packets(self) -> int:
        return self._total_missing
