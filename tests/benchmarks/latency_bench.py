"""
End-to-end latency benchmark for the CSI pipeline.

Measures per-stage processing time using synthetic packets — no hardware required.

Usage:
    python -m tests.benchmarks.latency_bench [--n-frames 1000] [--report]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Dict, List

import numpy as np


@dataclass
class BenchmarkResult:
    stage: str
    n_samples: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    frames_per_sec: float

    def __str__(self) -> str:
        return (
            f"{self.stage:<20} | "
            f"mean={self.mean_ms:7.3f} | "
            f"p50={self.p50_ms:7.3f} | "
            f"p95={self.p95_ms:7.3f} | "
            f"p99={self.p99_ms:7.3f} | "
            f"max={self.max_ms:7.3f} | "
            f"{self.frames_per_sec:>10,.0f} f/s"
        )


def _stats(samples_ms: List[float], stage: str) -> BenchmarkResult:
    samples_sorted = sorted(samples_ms)
    n = len(samples_sorted)
    mean_ms = statistics.fmean(samples_ms)
    return BenchmarkResult(
        stage=stage,
        n_samples=n,
        mean_ms=mean_ms,
        p50_ms=samples_sorted[int(0.50 * n)],
        p95_ms=samples_sorted[int(0.95 * n)],
        p99_ms=samples_sorted[min(int(0.99 * n), n - 1)],
        max_ms=samples_sorted[-1],
        frames_per_sec=1000.0 / mean_ms if mean_ms > 0 else float("inf"),
    )


class LatencyBenchmark:
    def __init__(self, n_frames: int = 1000, n_subcarriers: int = 64,
                 n_rx: int = 1, n_tx: int = 1) -> None:
        self._n = n_frames
        self._n_sub = n_subcarriers
        self._n_rx = n_rx
        self._n_tx = n_tx

    def benchmark_parse(self) -> BenchmarkResult:
        """Time to parse one UDP packet -> CSIFrame."""
        from tests.generators.packet_gen import SyntheticPacketGenerator
        from pc.ingestion.parser import parse_packet

        gen = SyntheticPacketGenerator(
            n_subcarriers=self._n_sub, n_rx=self._n_rx, n_tx=self._n_tx, seed=42
        )
        packets = [gen.generate_packet("static", t=i * 0.01) for i in range(self._n)]

        samples = []
        for pkt in packets:
            t0 = time.perf_counter()
            parse_packet(pkt)
            samples.append((time.perf_counter() - t0) * 1000)
        return _stats(samples, "Parse UDP")

    def benchmark_dsp(self) -> BenchmarkResult:
        """Time to process one CSIFrame through DSP (per-frame; window emits every stride)."""
        from tests.generators.packet_gen import SyntheticPacketGenerator
        from pc.dsp.pipeline import DSPPipeline

        cfg = self._default_config()
        gen = SyntheticPacketGenerator(
            n_subcarriers=self._n_sub, n_rx=self._n_rx, n_tx=self._n_tx, seed=43
        )
        dsp = DSPPipeline(cfg)

        samples = []
        for i in range(self._n):
            frame = gen.generate_frame("motion", t=i * 0.01)
            t0 = time.perf_counter()
            dsp.push(frame)
            samples.append((time.perf_counter() - t0) * 1000)
        return _stats(samples, "DSP push")

    def benchmark_dsp_window(self) -> BenchmarkResult:
        """Time to process a single window (extract features only — heavy work)."""
        from tests.generators.packet_gen import SyntheticPacketGenerator
        from pc.dsp.features import FeatureExtractor

        cfg = self._default_config()
        gen = SyntheticPacketGenerator(
            n_subcarriers=self._n_sub, n_rx=self._n_rx, n_tx=self._n_tx, seed=44
        )
        extractor = FeatureExtractor(cfg)

        window_size = int(cfg["dsp"]["window_size"])
        windows = []
        for w in range(max(20, self._n // window_size)):
            window = [gen.generate_frame("motion", t=(w * window_size + i) * 0.01)
                      for i in range(window_size)]
            windows.append(window)

        samples = []
        for window in windows:
            t0 = time.perf_counter()
            extractor.extract(window)
            samples.append((time.perf_counter() - t0) * 1000)
        return _stats(samples, "Feature extract")

    def benchmark_detection(self) -> BenchmarkResult:
        """Time to process one DSPFeatures through detection."""
        from pc.common.types import DSPFeatures
        from pc.detection.pipeline import DetectionPipeline

        cfg = self._default_config()
        det = DetectionPipeline(cfg, log_dir="logs")

        rng = np.random.default_rng(45)
        n_features = self._n_sub * self._n_rx * self._n_tx
        samples = []
        for i in range(self._n):
            f = DSPFeatures(
                timestamp_ns=int(i * 1e7),
                frame_seq=i,
                amplitude_mean=rng.standard_normal(n_features).astype(np.float32),
                amplitude_var=rng.standard_normal(n_features).astype(np.float32),
                phase_diff=rng.standard_normal(n_features - 1).astype(np.float32),
                motion_score=float(rng.random()),
                window_size=50,
                is_baseline=(i < 30),
            )
            t0 = time.perf_counter()
            det.push(f)
            samples.append((time.perf_counter() - t0) * 1000)
        det.shutdown()
        return _stats(samples, "Detection push")

    def benchmark_full_pipeline(self) -> BenchmarkResult:
        """End-to-end: parse -> DSP -> detection on synthetic packets."""
        from tests.generators.packet_gen import SyntheticPacketGenerator
        from pc.ingestion.parser import parse_packet
        from pc.dsp.pipeline import DSPPipeline
        from pc.detection.pipeline import DetectionPipeline

        cfg = self._default_config()
        gen = SyntheticPacketGenerator(
            n_subcarriers=self._n_sub, n_rx=self._n_rx, n_tx=self._n_tx, seed=46
        )
        dsp = DSPPipeline(cfg)
        det = DetectionPipeline(cfg, log_dir="logs")

        packets = [gen.generate_packet("motion", t=i * 0.01) for i in range(self._n)]

        samples = []
        for pkt in packets:
            t0 = time.perf_counter()
            frame = parse_packet(pkt)
            feat = dsp.push(frame)
            if feat is not None:
                det.push(feat)
            samples.append((time.perf_counter() - t0) * 1000)
        det.shutdown()
        return _stats(samples, "Full pipeline")

    def run_all(self) -> Dict[str, BenchmarkResult]:
        results: Dict[str, BenchmarkResult] = {}
        results["parse"] = self.benchmark_parse()
        results["dsp_push"] = self.benchmark_dsp()
        results["feature_extract"] = self.benchmark_dsp_window()
        results["detection"] = self.benchmark_detection()
        results["full_pipeline"] = self.benchmark_full_pipeline()
        return results

    def print_report(self, results: Dict[str, BenchmarkResult]) -> None:
        print()
        print(f"{'='*110}")
        print(f"  Pipeline Latency Benchmark — n_frames={self._n} "
              f"(n_sub={self._n_sub}, n_rx={self._n_rx}, n_tx={self._n_tx})")
        print(f"{'='*110}")
        print(f"{'Stage':<20} | {'Mean (ms)':>9} | {'P50 (ms)':>9} | "
              f"{'P95 (ms)':>9} | {'P99 (ms)':>9} | {'Max (ms)':>9} | {'Throughput':>12}")
        print(f"{'-'*110}")
        for key in ("parse", "dsp_push", "feature_extract", "detection", "full_pipeline"):
            if key in results:
                r = results[key]
                print(f"{r.stage:<20} |  {r.mean_ms:7.3f}  |  {r.p50_ms:7.3f}  | "
                      f" {r.p95_ms:7.3f}  |  {r.p99_ms:7.3f}  |  {r.max_ms:7.3f}  | "
                      f"{r.frames_per_sec:>9,.0f} f/s")
        print(f"{'='*110}")

        # Budget evaluation: at 100 Hz, per-frame budget is 10 ms.
        full = results.get("full_pipeline")
        if full is not None:
            margin = 10.0 / full.p99_ms if full.p99_ms > 0 else float("inf")
            verdict = "PASS" if margin >= 2.0 else ("OK" if margin >= 1.0 else "FAIL")
            print(f"\n100 Hz budget margin (10 ms / P99): {margin:.1f}x  -> {verdict}")
            if margin < 1.0:
                print("  WARNING: P99 latency exceeds 100 Hz frame budget. Reduce")
                print("  window_size, install scipy, or use a faster CPU.")
        print()

    def _default_config(self) -> dict:
        return {
            "dsp": {
                "window_size": 50,
                "window_stride": 5,
                "amplitude_smoothing_alpha": 0.15,
                "hampel_window": 5,
                "hampel_n_sigma": 3.0,
                "baseline_frames": 100,
                "baseline_update_alpha": 0.001,
                "variance_percentile": 90,
                "detrend": True,
                "stats_interval_s": 0,
            },
            "detection": {
                "motion_threshold": 0.25,
                "motion_clear_threshold": 0.10,
                "motion_confirm_frames": 3,
                "motion_clear_frames": 5,
                "adaptive_threshold": True,
                "adaptive_window_s": 60,
                "adaptive_k": 3.0,
                "adaptive_min_threshold": 0.10,
                "adaptive_max_threshold": 0.80,
                "min_motion_duration_ms": 0,
                "max_score_spike_ratio": 10.0,
                "occupancy_timeout_s": 30,
                "stats_interval_s": 0,
            },
            "logging": {"log_dir": "logs"},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="CSI pipeline latency benchmark")
    parser.add_argument("--n-frames", type=int, default=1000)
    parser.add_argument("--n-sub", type=int, default=64,
                        help="Subcarrier count (20MHz=64, 40MHz=128, 80MHz=256)")
    parser.add_argument("--n-rx", type=int, default=1)
    parser.add_argument("--n-tx", type=int, default=1)
    parser.add_argument("--report", action="store_true",
                        help="Print formatted report after running")
    args = parser.parse_args()

    sys.path.insert(0, ".")
    bench = LatencyBenchmark(n_frames=args.n_frames, n_subcarriers=args.n_sub,
                             n_rx=args.n_rx, n_tx=args.n_tx)
    results = bench.run_all()
    bench.print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
