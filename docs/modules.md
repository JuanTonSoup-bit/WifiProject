# Module Specifications

Every module in the system, with purpose, I/O, runtime location, dependencies,
failure modes, and performance characteristics.

---

## Pi Modules

### `pi/capture/csi_streamer.py` — Nexmon CSI capture + UDP forwarding

| Attribute        | Value |
|------------------|-------|
| Runtime location | Raspberry Pi 4 (Raspbian, root) |
| Purpose          | Receive CSI packets from Nexmon (UDP localhost:5500), repack into shared wire format, forward to PC over Ethernet UDP |
| Inputs           | UDP datagrams from Nexmon firmware on `lo` |
| Outputs          | UDP datagrams in shared wire format to PC eth0:5500 |
| Dependencies     | PyYAML (config), stdlib socket/struct/logging |
| Failure modes    | Nexmon module unloaded → 0 incoming packets; bad chanspec → garbage CSI; PC unreachable → `OSError` on send (logged but not fatal) |
| Performance      | ~100 packets/sec, <0.5 ms latency added |

### `pi/capture/health_monitor.py` — `/health` HTTP endpoint

| Attribute        | Value |
|------------------|-------|
| Runtime location | Pi, port 8080 |
| Purpose          | Expose JSON health snapshot to PC for monitoring |
| Inputs           | Stats file written by `csi_streamer.py` |
| Outputs          | JSON: `{"status":"ok|degraded|down","capture_hz":98.5,"dropped_frames":0,"uptime_s":12.0}` |
| Dependencies     | stdlib http.server only |
| Failure modes    | Stats file missing → reports 0 Hz |

### `pi/scripts/setup_nexmon.sh` — One-time Nexmon installation

| Attribute        | Value |
|------------------|-------|
| Runtime location | Pi, run once as root |
| Purpose          | Install Nexmon CSI patch + nexutil + systemd services |
| Failure modes    | Kernel headers missing, no internet, incompatible Pi model |

---

## Laptop Modules

### `laptop/transmitter.py` — Traffic generator

| Attribute        | Value |
|------------------|-------|
| Runtime location | Laptop (any OS) |
| Purpose          | Emit ~100 UDP packets/sec to Pi WiFi IP so Nexmon extracts one CSI frame per packet |
| Inputs           | Config file or CLI args |
| Outputs          | UDP packets to Pi (64-byte payload: seq + timestamp + padding); status packets to PC (port 5501) |
| Dependencies     | PyYAML, stdlib only |
| Failure modes    | Pi WiFi IP wrong → packets sent into the void (no error); rate drift if CPU loaded |
| Performance      | Achievable rate: 100 ± 1 Hz on idle laptop; degrades to ~80 Hz under load |

### `laptop/latency_probe.py` — UDP RTT measurement

| Attribute        | Value |
|------------------|-------|
| Runtime location | Laptop, manual run |
| Purpose          | Measure round-trip latency to the PC's echo server |
| Inputs           | Host/port via CLI |
| Outputs          | Min/mean/P50/P95/P99/max in ms |
| Dependencies     | stdlib only |
| Failure modes    | Echo server not running → all probes timeout |

---

## PC — Common

### `pc/common/types.py` — Shared dataclasses

Defines `CSIFrame`, `DSPFeatures`, `MotionEvent`, `SystemStatus`. Pure data —
no logic. Imported by every other PC module.

### `pc/common/config.py` — YAML config loader

| Attribute     | Value |
|---------------|-------|
| Purpose       | Load and validate `config.yaml` |
| Failure modes | Missing required keys → `ConfigError` with helpful message |

---

## PC — Ingestion

### `pc/ingestion/parser.py` — Wire format parser

| Attribute        | Value |
|------------------|-------|
| Purpose          | Convert raw UDP bytes → `CSIFrame` |
| Inputs           | `bytes` (UDP datagram) |
| Outputs          | `CSIFrame` dataclass |
| Dependencies     | numpy, stdlib struct |
| Failure modes    | Bad magic / truncated → `InvalidPacketError`; tracked in `PacketStats` |
| Performance      | ~3 µs per packet (parse + np.frombuffer) |

### `pc/ingestion/ring_buffer.py` — Thread-safe bounded buffer

| Attribute        | Value |
|------------------|-------|
| Purpose          | Decouple receive thread from consumer thread |
| Inputs           | `CSIFrame` (any type T) via `put()` |
| Outputs          | `CSIFrame` via `get()` / `get_batch()` |
| Failure modes    | When full, new items dropped (intentional); `drop_count` tracked |
| Performance      | O(1) put/get; sub-microsecond under contention |

### `pc/ingestion/receiver.py` — UDP receive thread

| Attribute        | Value |
|------------------|-------|
| Purpose          | Background thread: bind UDP, parse packets, put into RingBuffer |
| Inputs           | UDP socket bound to 0.0.0.0:5500 |
| Outputs          | `CSIFrame`s appended to ring buffer |
| Failure modes    | Socket error → `_fatal_error` set, thread exits, surfaced via `get_stats()` |
| Performance      | ≥10k packets/sec sustainable; 4 MB socket buffer absorbs bursts |

### `pc/ingestion/gap_detector.py` — Sequence-number gap tracking

Handles uint32 rollover. Reports gap size (number of missed packets) when
sequence numbers skip forward by more than 1.

### `pc/ingestion/orchestrator.py` — Top-level ingestion coordinator

| Attribute        | Value |
|------------------|-------|
| Purpose          | Wire receiver + ring buffer + dispatch thread |
| Outputs          | Pull (`get_frame`/`get_frames_batch`) or push (`subscribe(callback)`) |
| Failure modes    | Dispatch callback throws → logged, dispatch loop continues |

### `pc/ingestion/echo_server.py` — UDP echo for latency probe

Trivially echoes datagrams back to sender. Used by `laptop/latency_probe.py`.

---

## PC — DSP

### `pc/dsp/amplitude.py` — Per-subcarrier amplitude + EMA

| Inputs        | `CSIFrame` |
| Outputs       | Flattened (n_features,) float32 array |
| Performance   | ~50 µs per frame |

### `pc/dsp/phase.py` — Phase extraction + sanitization

| Inputs        | `CSIFrame` |
| Outputs       | Inter-subcarrier phase differences (n_features - n_ant,) |
| Notes         | SFO/PDD cancellation via adjacent-subcarrier differencing |

### `pc/dsp/filters.py` — Hampel, EMA, Savitzky-Golay, bandpass-variance, linear detrend

| Dependencies  | numpy required; scipy preferred but optional (graceful fallback) |
| Failure modes | None: pure functions |
| Performance   | Hampel dominates: ~1 ms for 50×64. See `docs/optimization.md` |

### `pc/dsp/window.py` — Sliding window emitter

Maintains a `deque(maxlen=size)` of recent frames; emits the whole window every
`stride` new frames.

### `pc/dsp/features.py` — Feature extraction orchestrator

Stack amplitudes → Hampel → detrend → variance + mean → phase diff → baseline normalize → motion score.
**Hot path**: ~1.8 ms per window for 50 frames × 64 subcarriers.

### `pc/dsp/baseline.py` — Ambient amplitude baseline

Two-phase: collect first N frames, then slow EMA update. Used to normalize variance.
Failure mode: if reset mid-motion, baseline includes motion → false threshold elevation
until next reset.

### `pc/dsp/pipeline.py` — DSP top-level

| Inputs        | `CSIFrame` via `push()` or queue |
| Outputs       | `DSPFeatures` via callback or queue |
| Performance   | <5 ms per window on commodity hardware |

---

## PC — Detection

### `pc/detection/state_machine.py` — Hysteretic motion state machine

States: BASELINE_COLLECTION → VACANT → MOTION_PENDING → MOTION_ACTIVE → CLEARING → VACANT.
Two thresholds (`motion_threshold` and `motion_clear_threshold`) with debounce counters.

### `pc/detection/adaptive_threshold.py` — Auto threshold

Rolling buffer of motion scores from VACANT periods only. Threshold = mean + k·std,
clamped. Falls back to static threshold until buffer ≥30% full.

### `pc/detection/spike_filter.py` — Single-frame spike suppression

Clamps scores that exceed `max_spike_ratio × recent_mean`.

### `pc/detection/occupancy.py` — Room-occupancy inference

motion_start → OCCUPIED; `occupancy_timeout_s` of no motion → VACANT.

### `pc/detection/event_logger.py` — Event sink

Writes to Python logging + `logs/events.jsonl`.

### `pc/detection/pipeline.py` — Detection orchestrator

| Inputs        | `DSPFeatures` |
| Outputs       | `MotionEvent`s via callback |
| Failure modes | Callback throws → logged, pipeline continues |

---

## PC — Visualization

### `pc/viz/data_buffer.py` — Thread-safe rolling buffers

Decouples data production (DSP/detection callbacks) from rendering (matplotlib or Flask).

### `pc/viz/app.py` — matplotlib FuncAnimation dashboard

Four panels: motion score time-series, amplitude heatmap, current spectrum, status text.
20 Hz update rate. Must run on the main thread.

### `pc/viz/web_ui.py` — Flask + SSE dashboard

Single-page HTML with Chart.js, served at `http://localhost:5503`. Pushes data via
Server-Sent Events at 5 Hz. Alternative to matplotlib for headless servers.

---

## PC — Tools

### `pc/tools/control_server.py` — TCP control socket

Embedded in `pc/main.py`. Accepts JSON commands from `pc/tools/diagnostics.py`.
Default 127.0.0.1:5599.

### `pc/tools/diagnostics.py` — Interactive CLI

Connects to control server. Commands: `status`, `stats`, `threshold`, `reset`.

### `pc/main.py` — System entry point

Wires all stages together. Supports `--mode live|replay`, `--no-viz`, `--web-ui`.

---

## Tests

### `tests/generators/packet_gen.py` — Synthetic CSI generator

Modes: `static`, `motion` (2 Hz amplitude modulation), `noise`, `fading`. Deterministic
via seed.

### `tests/replay/recorder.py` — Live capture to disk

| Format        | Per-record: [8B length][4B json_len][JSON header][raw csi bytes] |
| Usage         | Wrap `IngestionPipeline.subscribe()` for record-then-replay |

### `tests/replay/replayer.py` — Capture playback

Load a `.csi` file and replay to a queue, callback, or live UDP socket at controllable
speed (real-time or accelerated).

### `tests/benchmarks/latency_bench.py` — Per-stage latency measurement

Synthetic data through each stage. Reports mean/P50/P95/P99/max + frames/sec.
Compares against 100 Hz budget (10 ms per frame).

### `tests/simulators/loss_simulator.py` — Network loss models

Random, Gilbert-Elliott bursty, and periodic. For pipeline robustness testing.

### `tests/diagnostics/signal_check.py` — Signal-quality CLI

Analyzes a live or recorded stream for dead subcarriers, NaN/Inf, timing jitter,
sequence gaps, RSSI distribution.

### `tests/unit/*.py` — Unit tests (parser, DSP, state machine)

### `tests/integration/test_pipeline.py` — End-to-end synthetic-data tests

Verifies no false positives on static signal, motion is detected, 5% packet loss
is tolerated, throughput meets budget.

---

## Cross-cutting

| Concern              | Where handled |
|----------------------|---------------|
| Configuration        | `config.yaml` + `pc/common/config.py` |
| Logging              | `pc/main.py:_setup_logging` (colorlog + rotating file) |
| Graceful shutdown    | SIGTERM/SIGINT → `stop_event` → reverse-order cleanup |
| Stats / monitoring   | `get_stats()` on every pipeline stage + control server endpoints |
| Thread safety        | `threading.Lock` on mutable shared state (ring buffer, stats, viz buffer) |
| Backpressure         | Bounded queues + ring buffer (newest dropped on overrun, tracked) |
| Reconnection         | UDP is connectionless — receiver auto-resumes when Pi resumes; gap detector logs missing packets |
