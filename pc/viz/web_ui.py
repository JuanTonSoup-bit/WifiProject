"""Flask-based web dashboard with Server-Sent Events."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from pc.common.types import DSPFeatures, MotionEvent
from pc.viz.data_buffer import VizDataBuffer

logger = logging.getLogger(__name__)

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WiFi CSI Motion Detector</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  body { background: #1a1a2e; color: #eaeaea; font-family: monospace; margin: 0; padding: 16px; }
  h1 { color: #00d4ff; font-size: 1.2rem; margin-bottom: 12px; }
  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }
  .card { background: #16213e; border-radius: 8px; padding: 12px; }
  .state { font-size: 2rem; font-weight: bold; margin: 8px 0; }
  .state.MOTION { color: #ff4757; }
  .state.OCCUPIED { color: #ffa502; }
  .state.VACANT { color: #2ed573; }
  .state.BASELINE { color: #eccc68; }
  .state.UNKNOWN { color: #747d8c; }
  .metric { font-size: 0.85rem; color: #a4b0be; margin: 4px 0; }
  .metric span { color: #eccc68; }
  canvas { width: 100% !important; }
  #events { font-size: 0.75rem; list-style: none; padding: 0; }
  #events li { padding: 3px 0; border-bottom: 1px solid #2d3561; }
  #events .motion_start { color: #ff4757; }
  #events .motion_end { color: #2ed573; }
  #events .occupied { color: #ffa502; }
  #events .vacant { color: #2ed573; }
</style>
</head>
<body>
<h1>&#9780; WiFi CSI Motion Detector</h1>
<div class="grid">
  <div class="card">
    <div style="font-size:0.8rem;color:#a4b0be">Motion Score (last 30s)</div>
    <canvas id="scoreChart" height="120"></canvas>
  </div>
  <div class="card">
    <div style="font-size:0.8rem;color:#a4b0be">Status</div>
    <div id="stateText" class="state UNKNOWN">UNKNOWN</div>
    <div class="metric">Score: <span id="scoreVal">—</span></div>
    <div class="metric">Threshold: <span id="threshVal">—</span></div>
    <div class="metric">Pi Hz: <span id="hzVal">—</span></div>
    <hr style="border-color:#2d3561; margin:8px 0">
    <div style="font-size:0.8rem;color:#a4b0be">Recent Events</div>
    <ul id="events"></ul>
  </div>
</div>
<script>
const ctx = document.getElementById('scoreChart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      { label: 'Score', data: [], borderColor: '#00d4ff', borderWidth: 1.5,
        pointRadius: 0, fill: false, tension: 0.2 },
      { label: 'Threshold', data: [], borderColor: 'rgba(255,71,87,0.7)',
        borderWidth: 1, borderDash: [4,4], pointRadius: 0, fill: false },
    ]
  },
  options: {
    animation: false,
    scales: {
      x: { ticks: { color: '#a4b0be', maxTicksLimit: 6 }, grid: { color: '#2d3561' } },
      y: { min: 0, max: 1.2, ticks: { color: '#a4b0be' }, grid: { color: '#2d3561' } }
    },
    plugins: { legend: { labels: { color: '#a4b0be', boxWidth: 12, font: { size: 10 } } } }
  }
});

const evSource = new EventSource('/stream');
evSource.onmessage = (e) => {
  const d = JSON.parse(e.data);

  // Update state display
  const stateEl = document.getElementById('stateText');
  stateEl.textContent = d.state;
  stateEl.className = 'state ' + d.state;

  // Metrics
  document.getElementById('scoreVal').textContent = d.score.toFixed(4);
  document.getElementById('threshVal').textContent = d.threshold.toFixed(3);
  document.getElementById('hzVal').textContent = d.ingestion_hz.toFixed(1);

  // Chart
  const maxPts = 200;
  chart.data.labels = d.time_history.map(t => t.toFixed(1));
  chart.data.datasets[0].data = d.score_history;
  chart.data.datasets[1].data = new Array(d.score_history.length).fill(d.threshold);
  chart.update('none');

  // Events
  const ul = document.getElementById('events');
  ul.innerHTML = '';
  (d.events || []).reverse().slice(0, 8).forEach(ev => {
    const li = document.createElement('li');
    li.className = ev.event_type;
    const ts = new Date(ev.timestamp_s * 1000).toLocaleTimeString();
    li.textContent = `[${ts}] ${ev.event_type.toUpperCase()} score=${ev.motion_score.toFixed(3)}`;
    ul.appendChild(li);
  });
};
</script>
</body>
</html>"""


class WebUI:
    """Flask web dashboard with SSE stream for real-time updates."""

    def __init__(
        self,
        config: dict,
        data_buffer: VizDataBuffer,
        host: str = "0.0.0.0",
        port: int = 5503,
    ) -> None:
        self._buf = data_buffer
        self._host = host
        self._port = port
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start Flask in a daemon thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_flask, name="web-ui", daemon=True)
        self._thread.start()
        logger.info("Web UI starting on http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        self._stop_event.set()

    def _run_flask(self) -> None:
        try:
            from flask import Flask, Response, jsonify
        except ImportError:
            logger.error("Flask not installed. Install with: pip install flask")
            return

        app = Flask(__name__)
        buf = self._buf
        stop = self._stop_event

        @app.route("/")
        def index():
            return Response(_DASHBOARD_HTML, mimetype="text/html")

        @app.route("/stream")
        def stream():
            def generate():
                while not stop.is_set():
                    try:
                        snapshot = buf.get_snapshot()
                        yield f"data: {json.dumps(snapshot)}\n\n"
                    except Exception:
                        pass
                    time.sleep(0.2)

            return Response(generate(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        @app.route("/api/state")
        def api_state():
            return jsonify(buf.get_snapshot())

        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.ERROR)

        app.run(host=self._host, port=self._port, threaded=True, use_reloader=False)
