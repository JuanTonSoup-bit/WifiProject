"""Thread-safe ring buffer for CSIFrames."""

from __future__ import annotations

import threading
from collections import deque
from typing import Generic, List, Optional, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """
    Fixed-capacity thread-safe ring buffer backed by collections.deque.

    When full, new items are dropped (not oldest). Tracks drop count.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._buf: deque[T] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._drop_count = 0

    def put(self, item: T) -> bool:
        """
        Add item. Returns True on success, False if buffer full (item dropped).
        Non-blocking.
        """
        with self._not_empty:
            if len(self._buf) >= self._maxsize:
                self._drop_count += 1
                return False
            self._buf.append(item)
            self._not_empty.notify()
            return True

    def get(self, timeout: float = 1.0) -> Optional[T]:
        """Remove and return oldest item. Returns None on timeout."""
        with self._not_empty:
            deadline = threading.Event()  # used only to track elapsed
            waited = 0.0
            while len(self._buf) == 0:
                remaining = timeout - waited
                if remaining <= 0:
                    return None
                notified = self._not_empty.wait(timeout=min(remaining, 0.1))
                waited += 0.1
                if not notified and waited >= timeout:
                    return None
            return self._buf.popleft()

    def get_batch(self, n: int, timeout: float = 0.1) -> List[T]:
        """Return up to n items. Waits up to timeout for at least 1 item."""
        with self._not_empty:
            if len(self._buf) == 0:
                self._not_empty.wait(timeout=timeout)
            count = min(n, len(self._buf))
            return [self._buf.popleft() for _ in range(count)]

    def qsize(self) -> int:
        with self._lock:
            return len(self._buf)

    def is_full(self) -> bool:
        with self._lock:
            return len(self._buf) >= self._maxsize

    def drain(self) -> List[T]:
        """Remove and return all items."""
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
            return items

    @property
    def drop_count(self) -> int:
        with self._lock:
            return self._drop_count

    @property
    def maxsize(self) -> int:
        return self._maxsize
