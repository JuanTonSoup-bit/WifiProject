# WiFi CSI Motion Detection System

Real-time human motion detection using WiFi Channel State Information (CSI).
A Raspberry Pi 4 captures CSI via Nexmon from a laptop transmitter, streams it
over Ethernet to a desktop PC, which processes and visualizes motion in real-time.

```
Laptop  ──WiFi 100Hz──▶  Raspberry Pi 4  ──Ethernet UDP──▶  Desktop PC
(TX)                      (Nexmon CSI)                       (DSP + Detection + Viz)
```

---

## Quick Start (3 machines required)

```
1. Clone this repo on all three machines
2. Run the install script on each machine
3. Configure IPs in config.yaml
4. Launch in order: Pi → Laptop → PC
```

---

## Installation

### Desktop PC

**Linux / macOS:**
```bash
git clone <repo-url> wifi-csi-detector
cd wifi-csi-detector
bash install.sh
```

**Windows (PowerShell, run as Administrator):**
```powershell
git clone <repo-url> wifi-csi-detector
cd wifi-csi-detector
.\install.ps1
```

**What it does:**
- Creates a Python virtual environment in `venv/`
- Installs all PC dependencies (numpy, scipy, matplotlib, flask, colorlog, PyYAML)
- Installs the project as an editable package so `python -m pc.main` works
- Runs a self-check to confirm everything imported correctly

### Raspberry Pi

```bash
# SSH into Pi after cloning the repo there
cd wifi-csi-detector
pip3 install -r pi/requirements.txt

# Full Nexmon setup (run once, takes ~15 min):
sudo bash pi/scripts/setup_nexmon.sh
```

### Laptop

```bash
cd wifi-csi-detector
pip3 install -r laptop/requirements.txt
```

---

## Configuration

All configuration lives in a single file: **`config.yaml`** at the repo root.

The three critical values to set before running:

```yaml
network:
  pi_ip: "192.168.2.50"     # Pi's Ethernet IP
  pc_ip: "192.168.2.100"    # PC's Ethernet IP
  udp_port: 5500

transmitter:
  target_ip: "192.168.1.100"  # Pi's WIFI IP (different from Ethernet!)
```

> **Note:** The Pi has two IPs:
> - WiFi (`wlan0`): used by the laptop to send traffic (e.g. `192.168.1.x`)
> - Ethernet (`eth0`): used by the Pi to stream CSI to the PC (e.g. `192.168.2.50`)

Copy `config.yaml` to each machine and update the IPs accordingly.

---

## Running the System

### Launch Order

Always start in this order — the Pi must be streaming before the PC starts.

---

### Step 1 — Raspberry Pi

```bash
# Verify everything is ready
sudo wifi-csi-preflight

# Start the capture + streaming daemon
sudo systemctl start wifi-csi-capture wifi-csi-health

# Watch live logs
journalctl -u wifi-csi-capture -f

# Check health endpoint
curl http://localhost:8080/health
# Expected: {"status":"ok","capture_hz":98.5,"dropped_frames":0,"uptime_s":12.0}
```

If you haven't run `setup_nexmon.sh` yet, do that first (one-time setup):
```bash
sudo bash pi/scripts/setup_nexmon.sh
```

---

### Step 2 — Laptop (Traffic Generator)

```bash
# Activate venv (Linux/Mac)
source venv/bin/activate

# Windows
venv\Scripts\activate

# Start transmitting at 100 Hz
python laptop/transmitter.py --config config.yaml --verbose
```

**Expected output:**
```
TX stats: sent=1000 actual_rate=100.1 Hz target=100.0 Hz
TX stats: sent=2000 actual_rate=99.9 Hz target=100.0 Hz
```

**Options:**
```
--rate 100        Target Hz (default: 100)
--target <IP>     Pi WiFi IP
--method udp      Transport: udp or broadcast
--verbose         Show per-stat logs
```

---

### Step 3 — Desktop PC

```bash
# Activate venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows

# Run with matplotlib dashboard
python -m pc.main

# Run with web dashboard (open http://localhost:5503)
python -m pc.main --web-ui

# Run headless (no visualization, logs only)
python -m pc.main --no-viz

# Replay a recorded capture instead of live stream
python -m pc.main --mode replay --replay-file captures/my_capture.csi
```

**Startup output (normal):**
```
==================================================
  WiFi CSI Motion Detection System
==================================================
  [OK] Config loaded: config.yaml
  [OK] UDP socket bound: 0.0.0.0:5500
  [OK] DSP pipeline ready (window=50, stride=5)
  [OK] Detection engine ready (threshold=0.25, adaptive=True)
  [OK] Visualization ready (matplotlib)

  [*] Waiting for CSI frames from Pi (192.168.2.50)...
```

---

## Dashboard

The matplotlib dashboard shows four panels:

```
+------------------------+------------------------+
|  Motion Score          |  Amplitude Heatmap     |
|  (time series)         |  (subcarrier × time)   |
|  red = threshold       |                        |
|  orange = clear thresh |                        |
+------------------------+------------------------+
|  Raw Amplitude         |  System Status         |
|  (per subcarrier)      |  - State: VACANT       |
|                        |  - Score / Threshold   |
|                        |  - Recent events       |
+------------------------+------------------------+
```

The web dashboard at `http://localhost:5503` shows motion score history, current
state, and recent events — accessible from any browser on the same network.

---

## Verifying It Works

After all three are running:

1. **Check Pi is streaming:**
   ```bash
   curl http://192.168.2.50:8080/health
   # capture_hz should be ~100
   ```

2. **Check PC is receiving:**
   Look for `rate=~100Hz` in PC terminal output.

3. **Trigger motion detection:**
   Walk slowly in front of the Raspberry Pi.
   The dashboard should show motion score spike and log:
   ```
   EVENT MOTION_START   score=0.412 confidence=0.87
   ```

4. **Check events log:**
   ```bash
   cat logs/events.jsonl | tail -5
   ```

---

## Tuning

Key parameters in `config.yaml`:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `detection.motion_threshold` | 0.25 | Lower = more sensitive |
| `detection.motion_confirm_frames` | 3 | Higher = fewer false positives |
| `detection.adaptive_threshold` | true | Auto-adjusts to environment |
| `dsp.window_size` | 50 | Larger = smoother but more latency |
| `dsp.baseline_frames` | 300 | Frames before detection activates |
| `transmitter.rate_hz` | 100 | Higher = smoother CSI, more traffic |

If you get false positives from HVAC/fans, increase `motion_confirm_frames` to 5-10
and let the adaptive threshold run for 1-2 minutes before walking past.

---

## Recording and Replay

Record a live session:
```python
# Add to your pipeline or run standalone
from tests.replay.recorder import CaptureRecorder
from pc.ingestion.orchestrator import IngestionPipeline

with CaptureRecorder("captures/session1.csi", max_duration_s=60) as rec:
    pipeline = IngestionPipeline(config)
    pipeline.subscribe(rec.record)
    pipeline.start()
    time.sleep(60)
```

Replay it later (no hardware needed):
```bash
python -m pc.main --mode replay --replay-file captures/session1.csi
```

---

## Running Tests

```bash
# All unit tests
python -m unittest discover -s tests/unit -p "test_*.py" -v
python -m unittest discover -s pc/detection/tests -p "test_*.py" -v

# Integration tests (no hardware required — uses synthetic data)
python -m unittest tests.integration.test_pipeline -v

# Or use the test runner script
bash tests/run_tests.sh
```

---

## Diagnostics & Performance Tools

### Live diagnostics CLI
While `pc.main` is running, connect to the embedded control server:
```bash
python -m pc.tools.diagnostics            # interactive REPL
python -m pc.tools.diagnostics status     # one-shot status query
python -m pc.tools.diagnostics threshold 0.35   # change threshold at runtime
python -m pc.tools.diagnostics reset      # reset DSP baseline
```

### Latency benchmark
Measure per-stage processing time using synthetic packets (no hardware needed):
```bash
python -m tests.benchmarks.latency_bench --n-frames 1000
```
Sample output:
```
Stage                | Mean (ms) |  P50  |  P95  |  P99  | Throughput
Parse UDP            |    0.003  | 0.003 | 0.003 | 0.005 | 305,027 f/s
DSP push             |    0.287  | 0.000 | 1.545 | 1.721 |   3,490 f/s
Feature extract      |    1.562  | 1.528 | 1.864 | 1.864 |     640 f/s
Detection push       |    0.005  | 0.004 | 0.005 | 0.015 | 216,553 f/s
Full pipeline        |    0.293  | 0.004 | 1.554 | 1.894 |   3,414 f/s

100 Hz budget margin (10 ms / P99): 5.3x  -> PASS
```

### Signal integrity check
Diagnose dead subcarriers, timing jitter, RSSI distribution, packet loss:
```bash
# Analyze a recorded capture
python -m tests.diagnostics.signal_check --input captures/session1.csi

# Analyze a live stream for 30 seconds
python -m tests.diagnostics.signal_check --live --duration 30
```

---

## Project Structure

```
wifi-csi-detector/
├── config.yaml              ← Master config (edit IPs here)
├── requirements.txt         ← PC Python dependencies
├── setup.py                 ← Installable package definition
├── install.sh               ← Linux/Mac one-command setup
├── install.ps1              ← Windows one-command setup
│
├── pi/                      ← Runs on Raspberry Pi
│   ├── capture/
│   │   ├── csi_streamer.py  ← Nexmon CSI → UDP stream
│   │   └── health_monitor.py← HTTP /health endpoint
│   ├── scripts/
│   │   ├── setup_nexmon.sh  ← One-time Nexmon installation
│   │   ├── configure_monitor.sh
│   │   └── preflight_check.sh
│   └── systemd/             ← Service files
│
├── laptop/                  ← Runs on laptop
│   └── transmitter.py       ← 100 Hz UDP traffic generator
│
├── pc/                      ← Runs on desktop PC
│   ├── main.py              ← Entry point
│   ├── common/              ← Shared types + config loader
│   ├── ingestion/           ← UDP receive + parse + buffer
│   ├── dsp/                 ← Signal processing pipeline
│   ├── detection/           ← Motion state machine
│   └── viz/                 ← Dashboard (matplotlib + web)
│
├── tests/                   ← All tests (no hardware needed)
│   ├── unit/
│   ├── integration/
│   ├── generators/          ← Synthetic CSI packet generator
│   └── replay/              ← Record + replay captures
│
└── docs/
    ├── deployment.md        ← Full step-by-step setup guide
    ├── troubleshooting.md   ← Common issues + fixes
    └── architecture.md      ← System design + data flow diagrams
```

---

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for solutions to:
- Pi not streaming
- PC receiving 0 frames
- High packet loss
- Motion not detected / false positives
- Nexmon module failing to load

---

## Requirements

| Component | Requirement |
|-----------|-------------|
| Raspberry Pi | Pi 4 (any RAM), Raspberry Pi OS Lite 64-bit |
| PC | Python 3.9+, Linux or Windows, Ethernet port |
| Laptop | Python 3.9+, WiFi, same network as Pi |
| Network | Dedicated Ethernet cable between Pi and PC |
