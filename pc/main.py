#!/usr/bin/env python3
"""
WiFi CSI Motion Detection System - PC Entry Point

Wires ingestion -> DSP -> detection -> visualization.

Usage:
    python -m pc.main [--config config.yaml] [--mode live|replay]
                      [--replay-file path] [--no-viz] [--web-ui]
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import time
import threading
from typing import Optional

# ---------------------------------------------------------------------------
# Logging setup (before all other imports so modules log correctly)
# ---------------------------------------------------------------------------
def _setup_logging(log_dir: str = "logs", level: str = "INFO") -> None:
    import logging.handlers
    os.makedirs(log_dir, exist_ok=True)
    fmt = "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "csi_detector.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ))
    except OSError:
        pass

    try:
        import colorlog
        fmt_colored = "%(log_color)s" + fmt
        handlers[0] = colorlog.StreamHandler()
        handlers[0].setFormatter(colorlog.ColoredFormatter(fmt_colored, datefmt=datefmt))
    except ImportError:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                            format=fmt, datefmt=datefmt, handlers=handlers)
        return

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, datefmt=datefmt, handlers=handlers)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from pc.common.config import load_config, ConfigError
from pc.ingestion.orchestrator import IngestionPipeline
from pc.dsp.pipeline import DSPPipeline
from pc.detection.pipeline import DetectionPipeline
from pc.viz.data_buffer import VizDataBuffer

logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Session stats
# ---------------------------------------------------------------------------
class SessionStats:
    def __init__(self) -> None:
        self.start_time = time.monotonic()
        self.frames_received = 0
        self.windows_emitted = 0
        self.motion_events = 0

    def print_summary(self) -> None:
        elapsed = time.monotonic() - self.start_time
        print(f"\n=== Session Summary ===")
        print(f"  Duration:       {elapsed:.0f} s")
        print(f"  Frames received:{self.frames_received}")
        print(f"  Windows emitted:{self.windows_emitted}")
        print(f"  Motion events:  {self.motion_events}")
        print(f"======================")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="WiFi CSI Motion Detection System")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--mode", choices=["live", "replay"], default="live")
    parser.add_argument("--replay-file", help="Capture file for replay mode")
    parser.add_argument("--no-viz", action="store_true", help="Disable visualization")
    parser.add_argument("--web-ui", action="store_true", help="Use web UI instead of matplotlib")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[FAIL] Config error: {exc}", file=sys.stderr)
        sys.exit(1)

    log_dir = cfg.get("logging", {}).get("log_dir", "logs")
    _setup_logging(log_dir=log_dir, level=args.log_level)

    print(f"\n{'='*50}")
    print("  WiFi CSI Motion Detection System")
    print(f"{'='*50}")

    stats = SessionStats()
    stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------
    dsp_q: queue.Queue = queue.Queue(maxsize=200)
    det_q: queue.Queue = queue.Queue(maxsize=100)
    # DSP emits one feature frame every `window_stride` input frames.
    # Input rate is ~100 Hz, so output rate ≈ 100 / window_stride.
    input_hz = 100.0
    output_hz = input_hz / max(1.0, float(cfg.get("dsp", {}).get("window_stride", 5)))
    viz_buf = VizDataBuffer(
        history_seconds=float(cfg.get("visualization", {}).get("history_seconds", 30)),
        sample_rate_hz=output_hz,
    )

    dsp = DSPPipeline(cfg, output_queue=dsp_q)
    det = DetectionPipeline(cfg, log_dir=log_dir)

    # Subscribe detection output to viz buffer
    def on_motion_event(event):
        stats.motion_events += 1
        viz_buf.add_event(event)

    det.subscribe(on_motion_event)

    # Pipe DSP output to detection
    def on_dsp_features(features):
        stats.windows_emitted += 1
        viz_buf.add_features(features)
        if not det_q.full():
            det_q.put_nowait(features)

    dsp.subscribe(on_dsp_features)

    # ------------------------------------------------------------------
    # Control server (for diagnostics CLI)
    # ------------------------------------------------------------------
    from pc.tools.control_server import ControlServer
    control_cfg = cfg.get("control", {})
    control = ControlServer(
        host=control_cfg.get("host", "127.0.0.1"),
        port=int(control_cfg.get("port", 5599)),
    )

    def _status_handler(_req):
        return {
            "state": det.get_current_state().get("state"),
            "score": det.get_stats().get("score", 0.0),
            "threshold": det.get_stats().get("threshold", 0.0),
            "occupancy": det.get_stats().get("occupancy"),
            "ingestion_hz": ingestion.receive_rate_hz if args.mode == "live" else 0.0,
            "ingestion_drops": ingestion.get_stats()["receiver"].get("frames_dropped", 0)
                if args.mode == "live" else 0,
            "frames_received": stats.frames_received,
            "windows_emitted": stats.windows_emitted,
            "motion_events": stats.motion_events,
        }

    def _stats_handler(_req):
        out = {
            "session": {
                "frames_received": stats.frames_received,
                "windows_emitted": stats.windows_emitted,
                "motion_events": stats.motion_events,
            },
            "dsp": dsp.get_stats(),
            "detection": det.get_stats(),
        }
        if args.mode == "live":
            out["ingestion"] = ingestion.get_stats()
        return out

    def _threshold_handler(req):
        if "value" in req:
            new_t = float(req["value"])
            det._static_threshold = new_t  # set static fallback
            if det._threshold is not None:
                det._threshold._static = new_t
        return {"threshold": det._static_threshold}

    def _reset_handler(_req):
        dsp.reset_baseline()
        det.reset()
        return {"reset": True}

    control.register("status", _status_handler)
    control.register("stats", _stats_handler)
    control.register("threshold", _threshold_handler)
    control.register("reset", _reset_handler)
    control.start()

    # ------------------------------------------------------------------
    # Ingestion (live or replay)
    # ------------------------------------------------------------------
    if args.mode == "live":
        ingestion = IngestionPipeline(cfg)

        def on_frame(frame):
            stats.frames_received += 1
            dsp.push(frame)

        ingestion.subscribe(on_frame)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    try:
        if args.mode == "live":
            ingestion.start()
            net = cfg.get("network", {})
            print(f"  [OK] Config loaded: {args.config}")
            print(f"  [OK] UDP socket bound: 0.0.0.0:{net.get('udp_port', 5500)}")
            print(f"  [OK] DSP pipeline ready (window={cfg.get('dsp',{}).get('window_size',50)}, stride={cfg.get('dsp',{}).get('window_stride',5)})")
            print(f"  [OK] Detection engine ready (threshold={cfg.get('detection',{}).get('motion_threshold',0.25)}, adaptive={cfg.get('detection',{}).get('adaptive_threshold',True)})")
            print(f"  [OK] Control server: {control_cfg.get('host','127.0.0.1')}:{control_cfg.get('port',5599)}")

        det_thread = det.start_worker(det_q)

        # ------------------------------------------------------------------
        # Visualization
        # ------------------------------------------------------------------
        viz = None
        if not args.no_viz:
            if args.web_ui:
                from pc.viz.web_ui import WebUI
                viz = WebUI(cfg, viz_buf, port=int(cfg.get("visualization", {}).get("port", 5503)))
                viz.start()
                port = cfg.get("visualization", {}).get("port", 5503)
                print(f"  [OK] Web UI started: http://localhost:{port}")
            else:
                from pc.viz.app import CSIVisualizer
                viz = CSIVisualizer(cfg, viz_buf)
                print("  [OK] Visualization ready (matplotlib)")

        if args.mode == "live":
            print(f"\n  [*] Waiting for CSI frames from Pi ({cfg.get('network',{}).get('pi_ip','?')})...")
        elif args.mode == "replay":
            if not args.replay_file:
                print("[FAIL] --replay-file required for replay mode", file=sys.stderr)
                sys.exit(1)
            print(f"  [*] Replay mode from: {args.replay_file}")

        print()

        # ------------------------------------------------------------------
        # Graceful shutdown handler
        # ------------------------------------------------------------------
        def _stop(sig, frame):
            logger.info("Shutdown signal received (%d)", sig)
            stop_event.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        # ------------------------------------------------------------------
        # Replay mode
        # ------------------------------------------------------------------
        if args.mode == "replay" and args.replay_file:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from tests.replay.replayer import CaptureReplayer
            replayer = CaptureReplayer.from_file(args.replay_file)
            replay_q: queue.Queue = queue.Queue(maxsize=500)
            replay_thread = threading.Thread(
                target=replayer.replay_to_queue,
                args=(replay_q,),
                kwargs={"speed": 1.0},
                daemon=True,
            )
            replay_thread.start()
            while not stop_event.is_set():
                try:
                    frame = replay_q.get(timeout=0.5)
                    stats.frames_received += 1
                    dsp.push(frame)
                except queue.Empty:
                    if not replay_thread.is_alive():
                        logger.info("Replay finished.")
                        break

        # ------------------------------------------------------------------
        # Visualization (blocking) or idle loop
        # ------------------------------------------------------------------
        if viz and not args.web_ui:
            viz.run()
        else:
            while not stop_event.is_set():
                # Periodic stats update to viz buffer
                if args.mode == "live":
                    ing_stats = ingestion.get_stats()
                    all_stats = {
                        "ingestion_hz": ing_stats["receiver"]["rate_hz"],
                        "ingestion_drops": ing_stats["buffer_drops"],
                        **dsp.get_stats(),
                        **det.get_stats(),
                    }
                    viz_buf.update_stats(all_stats)
                time.sleep(1.0)

    finally:
        # ------------------------------------------------------------------
        # Shutdown in reverse order
        # ------------------------------------------------------------------
        logger.info("Shutting down...")
        stop_event.set()

        try:
            control.stop()
        except Exception:
            pass

        if args.mode == "live":
            ingestion.stop()

        det_q.put(None)  # sentinel
        det.shutdown()

        stats.print_summary()


if __name__ == "__main__":
    main()
