"""
Signal integrity diagnostics for live or recorded CSI streams.

Detects:
- Dead subcarriers (consistently zero/low amplitude)
- NaN/Inf values
- Sequence number gaps
- Timestamp jitter
- Per-subcarrier amplitude distribution
- Saturated subcarriers (always at max)

Usage:
    python -m tests.diagnostics.signal_check --input capture.csi
    python -m tests.diagnostics.signal_check --live --duration 30
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from pc.common.types import CSIFrame

logger = logging.getLogger(__name__)

DEAD_SUBCARRIER_THRESHOLD = 0.01  # below this amplitude = dead
SATURATION_PERCENTILE = 99.5      # samples above this are considered saturated
NOMINAL_FRAME_INTERVAL_S = 0.01    # 100 Hz


@dataclass
class SignalReport:
    n_frames: int = 0
    n_subcarriers: int = 0
    n_antennas: int = 0
    duration_s: float = 0.0

    # Subcarrier health
    dead_subcarriers: List[int] = field(default_factory=list)
    saturated_subcarriers: List[int] = field(default_factory=list)
    amplitude_mean_per_sub: Optional[np.ndarray] = None
    amplitude_std_per_sub: Optional[np.ndarray] = None

    # Data quality
    nan_inf_count: int = 0
    nan_inf_frames: List[int] = field(default_factory=list)

    # Timing
    timestamp_gaps_s: List[float] = field(default_factory=list)
    jitter_ms: float = 0.0
    mean_frame_interval_ms: float = 0.0

    # Sequencing
    seq_gaps: int = 0
    total_missing_packets: int = 0

    # Channel
    rssi_mean: float = 0.0
    rssi_std: float = 0.0

    # Aggregated assessment
    recommendations: List[str] = field(default_factory=list)


class SignalIntegrityChecker:
    """Analyzes CSI frame streams for hardware/signal quality issues."""

    def analyze_frames(self, frames: List[CSIFrame]) -> SignalReport:
        if not frames:
            return SignalReport()

        report = SignalReport(
            n_frames=len(frames),
            n_subcarriers=frames[0].n_subcarriers,
            n_antennas=frames[0].n_rx * frames[0].n_tx,
            duration_s=(frames[-1].timestamp_ns - frames[0].timestamp_ns) / 1e9
            if len(frames) > 1 else 0.0,
        )

        # Stack amplitudes: shape (n_frames, n_features) where n_features = n_rx*n_tx*n_sub
        amp_matrix = np.stack(
            [np.abs(f.csi_matrix).ravel() for f in frames]
        ).astype(np.float32)

        # NaN/Inf detection
        nan_mask = ~np.isfinite(amp_matrix)
        report.nan_inf_count = int(nan_mask.sum())
        if report.nan_inf_count > 0:
            bad_frames = np.where(nan_mask.any(axis=1))[0]
            report.nan_inf_frames = bad_frames.tolist()[:20]

        clean_amp = np.where(nan_mask, 0.0, amp_matrix)

        # Per-subcarrier (collapsed across antennas) statistics
        # Reshape: (n_frames, n_ant, n_sub) -> mean across (frames, ant) -> (n_sub,)
        n_sub = report.n_subcarriers
        n_ant = report.n_antennas
        reshaped = clean_amp.reshape(report.n_frames, n_ant, n_sub)
        amp_per_sub = reshaped.mean(axis=1)  # (n_frames, n_sub)

        report.amplitude_mean_per_sub = amp_per_sub.mean(axis=0)
        report.amplitude_std_per_sub = amp_per_sub.std(axis=0)

        # Dead subcarriers: amplitude near zero throughout
        max_per_sub = amp_per_sub.max(axis=0)
        report.dead_subcarriers = np.where(max_per_sub < DEAD_SUBCARRIER_THRESHOLD)[0].tolist()

        # Saturated subcarriers
        global_threshold = np.percentile(amp_per_sub, SATURATION_PERCENTILE)
        if global_threshold > 0:
            saturated_pct = (amp_per_sub > 0.9 * global_threshold).mean(axis=0)
            report.saturated_subcarriers = np.where(saturated_pct > 0.95)[0].tolist()

        # Timing
        if len(frames) > 1:
            ts_arr = np.array([f.timestamp_ns for f in frames], dtype=np.int64)
            intervals_ns = np.diff(ts_arr)
            intervals_s = intervals_ns / 1e9
            report.mean_frame_interval_ms = float(intervals_s.mean() * 1000)
            report.jitter_ms = float(intervals_s.std() * 1000)
            # Gaps > 2x nominal interval
            gap_threshold_s = 2 * NOMINAL_FRAME_INTERVAL_S
            gaps = intervals_s[intervals_s > gap_threshold_s]
            report.timestamp_gaps_s = sorted(gaps.tolist(), reverse=True)[:20]

        # Sequence number analysis
        seqs = np.array([f.seq_num for f in frames], dtype=np.int64)
        diffs = np.diff(seqs)
        # Handle uint32 rollover by ignoring negative diffs (wraps)
        positive_gaps = diffs[(diffs > 1) & (diffs < 100_000)]
        report.seq_gaps = int(len(positive_gaps))
        report.total_missing_packets = int(positive_gaps.sum() - len(positive_gaps))

        # RSSI
        rssi = np.array([f.rssi for f in frames], dtype=np.float32)
        report.rssi_mean = float(rssi.mean())
        report.rssi_std = float(rssi.std())

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    def _generate_recommendations(self, r: SignalReport) -> List[str]:
        recs: List[str] = []

        if r.dead_subcarriers:
            pct = len(r.dead_subcarriers) / max(r.n_subcarriers, 1) * 100
            recs.append(
                f"{len(r.dead_subcarriers)}/{r.n_subcarriers} dead subcarriers ({pct:.0f}%) — "
                f"check antenna connection or RF interference"
            )

        if r.nan_inf_count > 0:
            recs.append(
                f"{r.nan_inf_count} NaN/Inf values detected — possible numerical instability "
                f"in Nexmon firmware or driver"
            )

        if r.jitter_ms > 5.0:
            recs.append(
                f"High timing jitter ({r.jitter_ms:.1f} ms) — Pi CPU may be overloaded "
                f"or laptop transmit rate is unstable"
            )

        if r.timestamp_gaps_s:
            largest = r.timestamp_gaps_s[0] * 1000
            recs.append(
                f"{len(r.timestamp_gaps_s)} timing gaps detected (largest: {largest:.0f} ms) — "
                f"investigate packet drops or Pi load"
            )

        if r.seq_gaps > 0:
            loss_pct = r.total_missing_packets / max(r.n_frames + r.total_missing_packets, 1) * 100
            if loss_pct > 5.0:
                recs.append(
                    f"Packet loss: {r.total_missing_packets} packets in {r.seq_gaps} gaps "
                    f"({loss_pct:.1f}%) — increase socket buffer or check Ethernet quality"
                )

        if abs(r.rssi_mean) < 30 or abs(r.rssi_mean) > 80:
            recs.append(
                f"Unusual RSSI mean ({r.rssi_mean:.0f} dBm) — check Pi-laptop distance "
                f"and antenna orientation"
            )

        if r.mean_frame_interval_ms > 0:
            actual_hz = 1000.0 / r.mean_frame_interval_ms
            if abs(actual_hz - 100.0) > 10.0:
                recs.append(
                    f"Capture rate ({actual_hz:.1f} Hz) deviates from target 100 Hz — "
                    f"adjust transmitter rate"
                )

        if not recs:
            recs.append("Signal looks healthy. No issues detected.")

        return recs

    def print_report(self, r: SignalReport) -> None:
        print()
        print("=" * 70)
        print("  Signal Integrity Report")
        print("=" * 70)
        print(f"  Frames analyzed:    {r.n_frames}")
        print(f"  Duration:           {r.duration_s:.1f} s")
        print(f"  Subcarriers:        {r.n_subcarriers}")
        print(f"  Antennas:           {r.n_antennas}")
        print(f"  Mean interval:      {r.mean_frame_interval_ms:.2f} ms "
              f"(target: {NOMINAL_FRAME_INTERVAL_S * 1000:.0f} ms)")
        print(f"  Jitter:             {r.jitter_ms:.2f} ms")
        print(f"  RSSI:               {r.rssi_mean:.1f} +/- {r.rssi_std:.1f} dBm")
        print()
        print("  Data Quality")
        print(f"    NaN/Inf values:        {r.nan_inf_count}")
        print(f"    Dead subcarriers:      {len(r.dead_subcarriers)}")
        print(f"    Saturated subcarriers: {len(r.saturated_subcarriers)}")
        print(f"    Sequence gaps:         {r.seq_gaps}")
        print(f"    Missing packets:       {r.total_missing_packets}")
        if r.timestamp_gaps_s:
            print(f"    Largest gap:           {r.timestamp_gaps_s[0] * 1000:.0f} ms")
        print()
        if r.amplitude_mean_per_sub is not None:
            print("  Amplitude per-subcarrier")
            print(f"    Min mean:              {r.amplitude_mean_per_sub.min():.3f}")
            print(f"    Max mean:              {r.amplitude_mean_per_sub.max():.3f}")
            print(f"    Std range:             "
                  f"[{r.amplitude_std_per_sub.min():.3f}, {r.amplitude_std_per_sub.max():.3f}]")
        print()
        print("  Recommendations")
        for rec in r.recommendations:
            print(f"    * {rec}")
        print("=" * 70)
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal integrity diagnostics")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to recorded capture file")
    src.add_argument("--live", action="store_true",
                     help="Capture from live UDP stream for --duration seconds")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Live capture duration (live mode only)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    sys.path.insert(0, ".")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    checker = SignalIntegrityChecker()

    if args.input:
        from tests.replay.replayer import CaptureReplayer
        replayer = CaptureReplayer.from_file(args.input)
        frames = [replayer.get_frame(i) for i in range(replayer.n_frames)]
        print(f"Loaded {len(frames)} frames from {args.input}")
    else:
        from pc.common.config import load_config
        from pc.ingestion.orchestrator import IngestionPipeline

        cfg = load_config(args.config)
        ingestion = IngestionPipeline(cfg)
        frames: List[CSIFrame] = []
        ingestion.subscribe(frames.append)
        ingestion.start()
        print(f"Capturing live for {args.duration:.0f} s...")
        try:
            time.sleep(args.duration)
        finally:
            ingestion.stop()
        print(f"Captured {len(frames)} frames")

    if not frames:
        print("No frames to analyze.", file=sys.stderr)
        return 1

    report = checker.analyze_frames(frames)
    checker.print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
