"""UDP receiver thread: binds socket, parses packets, feeds RingBuffer."""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional

from pc.ingestion.gap_detector import GapDetector
from pc.ingestion.parser import InvalidPacketError, PacketStats, parse_packet
from pc.ingestion.ring_buffer import RingBuffer

logger = logging.getLogger(__name__)

RECV_BUFSIZE = 4 * 1024 * 1024  # 4 MB socket buffer


class CSIReceiver:
    """
    Background UDP receiver that parses CSI packets and puts them into a RingBuffer.

    Thread-safe. Call start() to begin receiving, stop() for graceful shutdown.
    """

    def __init__(self, config: dict, output_buffer: RingBuffer) -> None:
        net = config.get("network", {})
        ing = config.get("ingestion", {})

        self._host = "0.0.0.0"
        self._port = int(net.get("udp_port", 5500))
        self._socket_timeout = float(net.get("socket_timeout_s", 5.0))
        self._stats_interval = float(ing.get("stats_interval_s", 10.0))
        self._max_gap_warn = int(ing.get("max_gap_warn", 5))

        self._output = output_buffer
        self._packet_stats = PacketStats()
        self._gap_detector = GapDetector(max_gap=self._max_gap_warn)

        self._frames_received = 0
        self._frames_dropped = 0
        self._last_stats_time = 0.0
        self._start_time = 0.0

        self._rate_ema = 0.0
        self._rate_alpha = 0.1
        self._last_frame_time: Optional[float] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._fatal_error: Optional[Exception] = None

    def start(self) -> None:
        """Bind socket and start background receive thread."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFSIZE)
        self._sock.settimeout(self._socket_timeout)
        self._sock.bind((self._host, self._port))

        actual_buf = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        logger.info("UDP socket bound to %s:%d (recv buf=%d bytes)", self._host, self._port, actual_buf)

        self._start_time = time.monotonic()
        self._last_stats_time = self._start_time
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="csi-receiver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the receiver thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._socket_timeout + 1.0)
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        logger.info("CSIReceiver stopped. Frames received: %d, dropped: %d, parse errors: %d",
                    self._frames_received, self._frames_dropped, self._packet_stats.total_errors)

    def _run(self) -> None:
        """Main receive loop. Runs in background thread."""
        assert self._sock is not None

        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop_event.is_set():
                    logger.error("Socket error: %s", exc)
                    self._fatal_error = exc
                break

            try:
                frame = parse_packet(data, self._packet_stats)
            except InvalidPacketError as exc:
                logger.debug("Parse error from %s: %s", addr, exc)
                continue

            self._frames_received += 1
            self._update_rate()

            gap = self._gap_detector.check(frame.seq_num)
            if gap and gap > 0:
                logger.warning("Sequence gap of %d packets (seq=%d)", gap, frame.seq_num)

            if not self._output.put(frame):
                self._frames_dropped += 1
                if self._frames_dropped % 100 == 0:
                    logger.warning("Ring buffer full: %d frames dropped total", self._frames_dropped)

            self._maybe_log_stats()

    def _update_rate(self) -> None:
        now = time.monotonic()
        if self._last_frame_time is not None:
            dt = now - self._last_frame_time
            if dt > 0:
                inst_hz = 1.0 / dt
                if self._rate_ema == 0.0:
                    self._rate_ema = inst_hz
                else:
                    self._rate_ema = self._rate_alpha * inst_hz + (1 - self._rate_alpha) * self._rate_ema
        self._last_frame_time = now

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats_time >= self._stats_interval:
            elapsed = now - self._start_time
            logger.info(
                "Ingestion stats: frames=%d rate=%.1fHz dropped=%d parse_errors=%d gaps=%d uptime=%.0fs",
                self._frames_received, self._rate_ema, self._frames_dropped,
                self._packet_stats.total_errors, self._gap_detector.total_gaps, elapsed,
            )
            self._last_stats_time = now

    def get_stats(self) -> dict:
        return {
            "frames_received": self._frames_received,
            "frames_dropped": self._frames_dropped,
            "rate_hz": round(self._rate_ema, 2),
            "parse_errors": self._packet_stats.total_errors,
            "seq_gaps": self._gap_detector.total_gaps,
            "missing_packets": self._gap_detector.total_missing_packets,
            "buffer_qsize": self._output.qsize(),
            "buffer_drops": self._output.drop_count,
            "fatal_error": str(self._fatal_error) if self._fatal_error else None,
        }

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def receive_rate_hz(self) -> float:
        return self._rate_ema
