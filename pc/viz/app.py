"""Real-time matplotlib dashboard for WiFi CSI motion detection."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import List, Optional

import matplotlib
matplotlib.use("TkAgg")  # explicit backend; user can override via MPLBACKEND env var
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np

from pc.common.types import DSPFeatures, MotionEvent
from pc.viz.data_buffer import VizDataBuffer

logger = logging.getLogger(__name__)


class CSIVisualizer:
    """
    Four-panel real-time matplotlib dashboard.

    Layout:
    ┌─────────────────────┬─────────────────────┐
    │   Motion Score      │   CSI Amplitude     │
    │   Time Series       │   Heatmap           │
    ├─────────────────────┼─────────────────────┤
    │   Raw Amplitude     │   Stats + Events    │
    │   per subcarrier    │   Panel             │
    └─────────────────────┴─────────────────────┘
    """

    def __init__(self, config: dict, data_buffer: VizDataBuffer) -> None:
        viz = config.get("visualization", {})
        self._update_interval_ms = int(1000 / max(1, int(viz.get("update_rate_hz", 20))))
        self._history_s = float(viz.get("history_seconds", 30))
        self._title = str(viz.get("window_title", "WiFi CSI Motion Detector"))
        self._heatmap_n = int(viz.get("heatmap_n_subcarriers", 64))
        self._colormap = str(viz.get("heatmap_colormap", "viridis"))
        self._theme = str(viz.get("theme", "dark"))

        self._buf = data_buffer
        self._fig: Optional[plt.Figure] = None
        self._axes: dict = {}
        self._artists: dict = {}
        self._anim: Optional[animation.FuncAnimation] = None
        self._status_text = None

    def run(self) -> None:
        """Start the animation loop (blocking — call from main thread)."""
        if self._theme == "dark":
            plt.style.use("dark_background")

        self._fig, axes = plt.subplots(2, 2, figsize=(14, 8))
        self._fig.suptitle(self._title, fontsize=12, color="white" if self._theme == "dark" else "black")
        self._fig.subplots_adjust(hspace=0.35, wspace=0.3)

        ax_score = axes[0, 0]
        ax_heatmap = axes[0, 1]
        ax_raw = axes[1, 0]
        ax_stats = axes[1, 1]

        self._setup_score_panel(ax_score)
        self._setup_heatmap_panel(ax_heatmap)
        self._setup_raw_panel(ax_raw)
        self._setup_stats_panel(ax_stats)

        self._anim = animation.FuncAnimation(
            self._fig,
            self._update,
            interval=self._update_interval_ms,
            blit=False,
            cache_frame_data=False,
        )

        plt.tight_layout()
        try:
            plt.show()
        except KeyboardInterrupt:
            pass

    def _setup_score_panel(self, ax) -> None:
        ax.set_title("Motion Score", fontsize=9)
        ax.set_xlim(0, self._history_s)
        ax.set_ylim(-0.05, 1.5)
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.set_ylabel("Score", fontsize=7)
        ax.tick_params(labelsize=6)

        (line_score,) = ax.plot([], [], lw=1.5, color="cyan", label="score")
        line_thresh = ax.axhline(y=0.25, color="red", linestyle="--", lw=1, label="threshold")
        line_clear = ax.axhline(y=0.10, color="orange", linestyle="--", lw=1, label="clear")
        ax.legend(fontsize=6, loc="upper right")

        self._axes["score"] = ax
        self._artists["score_line"] = line_score
        self._artists["thresh_line"] = line_thresh
        self._artists["clear_line"] = line_clear
        self._artists["event_vlines"] = []

    def _setup_heatmap_panel(self, ax) -> None:
        ax.set_title("Amplitude Heatmap (subcarrier × time)", fontsize=9)
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.set_ylabel("Subcarrier", fontsize=7)
        ax.tick_params(labelsize=6)

        placeholder = np.zeros((self._heatmap_n, 10))
        im = ax.imshow(
            placeholder,
            aspect="auto",
            origin="lower",
            cmap=self._colormap,
            extent=[0, self._history_s, 0, self._heatmap_n],
        )
        self._fig.colorbar(im, ax=ax, shrink=0.8)
        self._axes["heatmap"] = ax
        self._artists["heatmap_im"] = im

    def _setup_raw_panel(self, ax) -> None:
        ax.set_title("Current Amplitude Spectrum", fontsize=9)
        ax.set_xlabel("Subcarrier", fontsize=7)
        ax.set_ylabel("Amplitude", fontsize=7)
        ax.tick_params(labelsize=6)

        (raw_line,) = ax.plot([], [], lw=1, color="lime", label="current")
        (smooth_line,) = ax.plot([], [], lw=1.5, color="yellow", alpha=0.7, label="smoothed")
        ax.legend(fontsize=6, loc="upper right")
        ax.set_xlim(0, self._heatmap_n)
        ax.set_ylim(0, 1)

        self._axes["raw"] = ax
        self._artists["raw_line"] = raw_line
        self._artists["smooth_line"] = smooth_line
        self._artists["raw_amp_smoothed"] = deque(maxlen=20)

    def _setup_stats_panel(self, ax) -> None:
        ax.set_title("System Status", fontsize=9)
        ax.axis("off")
        txt = ax.text(
            0.05, 0.95, "Initializing...",
            transform=ax.transAxes,
            fontsize=8,
            verticalalignment="top",
            family="monospace",
            color="white" if self._theme == "dark" else "black",
        )
        self._axes["stats"] = ax
        self._artists["stats_text"] = txt

    def _update(self, frame_num: int) -> list:
        """FuncAnimation callback. Runs on main thread."""
        self._update_score_panel()
        self._update_heatmap_panel()
        self._update_raw_panel()
        self._update_stats_panel()
        return []

    def _update_score_panel(self) -> None:
        ax = self._axes["score"]
        ts, scores = self._buf.get_score_series()
        if len(ts) < 2:
            return

        # Slide window so most recent is at right edge
        t_max = float(ts[-1])
        t_min = t_max - self._history_s
        ax.set_xlim(t_min, t_max)

        self._artists["score_line"].set_data(ts, scores)
        self._artists["thresh_line"].set_ydata([self._buf.current_threshold] * 2)
        self._artists["clear_line"].set_ydata([self._buf.current_clear_threshold] * 2)

        # Remove old event lines and redraw at their actual relative timestamps
        for vl in self._artists["event_vlines"]:
            vl.remove()
        self._artists["event_vlines"] = []

        for event_t, event_type in self._buf.get_event_markers():
            if event_t < t_min or event_t > t_max:
                continue
            color = {"motion_start": "red", "motion_end": "green",
                     "occupied": "orange", "vacant": "lime"}.get(event_type, "white")
            vl = ax.axvline(x=event_t, color=color, alpha=0.6, lw=1.5)
            self._artists["event_vlines"].append(vl)

    def _update_heatmap_panel(self) -> None:
        ts, matrix = self._buf.get_amplitude_matrix()
        if matrix is None or ts is None or matrix.shape[0] < 2:
            return

        n_sub = min(self._heatmap_n, matrix.shape[1])
        mat_slice = matrix[:, :n_sub].T  # (n_sub, n_frames)
        vmax = np.percentile(mat_slice, 98)
        im = self._artists["heatmap_im"]
        im.set_data(mat_slice)
        im.set_clim(vmin=0, vmax=max(vmax, 1e-3))
        t_min = float(ts[0])
        t_max = float(ts[-1])
        im.set_extent([t_min, t_max, 0, n_sub])
        self._axes["heatmap"].set_xlim(t_min, t_max)

    def _update_raw_panel(self) -> None:
        amp = self._buf.get_latest_amplitude()
        if amp is None:
            return

        n = min(self._heatmap_n, len(amp))
        x = np.arange(n)
        current = amp[:n]

        smoothed_buf: deque = self._artists["raw_amp_smoothed"]
        smoothed_buf.append(current)
        smoothed = np.mean(list(smoothed_buf), axis=0)

        ax = self._axes["raw"]
        ax.set_xlim(0, n)
        vmax = max(float(np.percentile(current, 98)), 0.1)
        ax.set_ylim(0, vmax * 1.1)

        self._artists["raw_line"].set_data(x, current)
        self._artists["smooth_line"].set_data(x, smoothed)

    def _update_stats_panel(self) -> None:
        buf = self._buf
        snap = buf.get_snapshot()
        events = buf.get_recent_events(5)

        pi_status = "Connected" if buf.pi_connected else "Waiting..."
        state_color = {"MOTION": "red", "OCCUPIED": "orange", "VACANT": "lime",
                       "BASELINE": "yellow", "UNKNOWN": "gray"}.get(buf.current_state, "white")

        event_lines = "\n".join(
            f"  {e.event_type.upper():<14} score={e.motion_score:.3f}"
            for e in reversed(events)
        )

        text = (
            f"Pi: {pi_status} | Rate: {buf.ingestion_hz:.1f} Hz\n"
            f"State: {buf.current_state}\n"
            f"Score: {buf.current_score:.4f}  Thresh: {buf.current_threshold:.3f}\n"
            f"Events (last {len(events)}):\n{event_lines}"
        )

        self._artists["stats_text"].set_text(text)
