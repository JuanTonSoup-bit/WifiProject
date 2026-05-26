#!/usr/bin/env python3
"""
UDP round-trip latency probe.

Sends timestamped packets to pc/ingestion/echo_server.py on port 5502,
measures RTT, and reports statistics.
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from typing import List

PROBE_PORT = 5502
PAYLOAD_SIZE = 16  # 8B seq + 8B timestamp


def run_probe(host: str, port: int, n: int = 1000, timeout: float = 2.0) -> List[float]:
    """Returns list of RTT values in milliseconds."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    rtts: List[float] = []
    lost = 0

    for seq in range(n):
        ts_ns = time.monotonic_ns()
        payload = struct.pack(">QQ", seq, ts_ns)
        try:
            sock.sendto(payload, (host, port))
            echo, _ = sock.recvfrom(64)
            recv_ns = time.monotonic_ns()

            if len(echo) >= 16:
                rtt_ms = (recv_ns - ts_ns) / 1e6
                rtts.append(rtt_ms)
            else:
                lost += 1

        except socket.timeout:
            lost += 1

        # ~100 Hz probe rate
        time.sleep(0.01)

    sock.close()

    if not rtts:
        print(f"All {n} probes lost (lost={lost})")
        return []

    rtts_sorted = sorted(rtts)
    n_recv = len(rtts)
    p50 = rtts_sorted[int(0.50 * n_recv)]
    p95 = rtts_sorted[int(0.95 * n_recv)]
    p99 = rtts_sorted[int(0.99 * n_recv)]

    print(f"\nLatency Probe Results ({n_recv}/{n} received, {lost} lost)")
    print(f"  Min:  {min(rtts):.3f} ms")
    print(f"  Mean: {sum(rtts)/len(rtts):.3f} ms")
    print(f"  P50:  {p50:.3f} ms")
    print(f"  P95:  {p95:.3f} ms")
    print(f"  P99:  {p99:.3f} ms")
    print(f"  Max:  {max(rtts):.3f} ms")
    print(f"  Loss: {lost/n*100:.1f}%")
    return rtts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UDP round-trip latency probe")
    parser.add_argument("--host", default="192.168.2.100", help="Echo server host")
    parser.add_argument("--port", type=int, default=PROBE_PORT)
    parser.add_argument("--n", type=int, default=1000, help="Number of probes")
    args = parser.parse_args()

    print(f"Probing {args.host}:{args.port} with {args.n} packets...")
    run_probe(args.host, args.port, args.n)
