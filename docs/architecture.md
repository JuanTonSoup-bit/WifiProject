# System Architecture

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  LAPTOP (any OS)                                                │
│  laptop/transmitter.py                                          │
│  • UDP unicast/broadcast @ 100 Hz                               │
│  • 64-byte payload: seq + timestamp                             │
└───────────────────────┬─────────────────────────────────────────┘
                        │ 802.11 WiFi (100 packets/sec)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  RASPBERRY PI 4                                                 │
│  wlan0 (monitor mode, Nexmon CSI patch)                         │
│                                                                 │
│  Nexmon kernel module                                           │
│    └─▶ CSI UDP → localhost:5500                                 │
│           │                                                     │
│  pi/capture/csi_streamer.py                                     │
│    • Parse Nexmon packet (magic, rssi, csi_int16)               │
│    • Convert int16 → float32 complex                            │
│    • Pack shared wire format                                    │
│    • UDP send to PC eth0                                        │
│                                                                 │
│  eth0: 192.168.2.50                                             │
└───────────────────────┬─────────────────────────────────────────┘
                        │ Ethernet UDP (port 5500, ~2-4 KB/frame)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  DESKTOP PC (eth0: 192.168.2.100)                               │
│                                                                 │
│  pc/ingestion/receiver.py          [Thread: csi-receiver]       │
│    • socket.recvfrom(65535)                                     │
│    • parse_packet() → CSIFrame                                  │
│    • GapDetector (seq number tracking)                          │
│    └─▶ RingBuffer[CSIFrame] (maxsize=500)                       │
│              │                                                  │
│  pc/ingestion/orchestrator.py      [dispatch callback]          │
│    • Pull from RingBuffer                                       │
│    • Push to DSPPipeline                                        │
│              │                                                  │
│  pc/dsp/pipeline.py                [same thread or worker]      │
│    • SlidingWindow (50 frames, stride 5)                        │
│    • AmplitudeExtractor (EMA per subcarrier)                    │
│    • HampelFilter (outlier removal)                             │
│    • LinearDetrend                                              │
│    • FeatureExtractor → DSPFeatures                             │
│    • BaselineEstimator (normalization)                          │
│    • motion_score = tanh(p90(normalized_var) / 3)               │
│              │                                                  │
│  pc/detection/pipeline.py          [Thread: detection-worker]   │
│    • SpikeFilter (ratio-based clamp)                            │
│    • AdaptiveThreshold (mean + k*std of vacant scores)          │
│    • MotionStateMachine (hysteretic, debounced)                 │
│    • OccupancyTracker (timeout-based)                           │
│    └─▶ MotionEvent stream                                       │
│              │                                                  │
│  pc/viz/app.py (matplotlib) or pc/viz/web_ui.py (Flask)        │
│    • VizDataBuffer (thread-safe rolling buffers)                │
│    • 4-panel dashboard OR web SSE stream                        │
│                                                                 │
│  pc/detection/event_logger.py                                   │
│    • logs/events.jsonl (machine-readable)                       │
│    • Python logging (human-readable)                            │
└─────────────────────────────────────────────────────────────────┘
```

## Module Dependency Graph

```
pc/main.py
├── pc/common/config.py         (load_config)
├── pc/ingestion/orchestrator.py
│   ├── pc/ingestion/receiver.py
│   │   ├── pc/ingestion/parser.py  ← pc/common/types.CSIFrame
│   │   └── pc/ingestion/gap_detector.py
│   └── pc/ingestion/ring_buffer.py
├── pc/dsp/pipeline.py
│   ├── pc/dsp/window.py
│   ├── pc/dsp/features.py
│   │   ├── pc/dsp/amplitude.py
│   │   ├── pc/dsp/phase.py
│   │   ├── pc/dsp/filters.py
│   │   └── pc/dsp/baseline.py
│   └── pc/common/types.DSPFeatures
├── pc/detection/pipeline.py
│   ├── pc/detection/state_machine.py
│   ├── pc/detection/adaptive_threshold.py
│   ├── pc/detection/spike_filter.py
│   ├── pc/detection/occupancy.py
│   └── pc/detection/event_logger.py
└── pc/viz/app.py | pc/viz/web_ui.py
    └── pc/viz/data_buffer.py
```

## Threading Model

```
Main Thread:
  matplotlib FuncAnimation (if enabled)
  OR
  idle loop + status update

csi-receiver (daemon):
  socket.recvfrom → parse_packet → RingBuffer.put

ingestion-dispatch (daemon, if subscribers):
  RingBuffer.get → callback(frame) → dsp.push(frame)

dsp-worker (daemon, optional):
  Queue.get → dsp.push → dsp_q.put

detection-worker (daemon):
  dsp_q.get → det.push → callbacks

web-ui (daemon, if enabled):
  Flask/SSE server on port 5503
```

## Performance Budget (100 Hz input, 20 Hz DSP output)

| Stage            | Budget   | Typical  | Notes                              |
|------------------|----------|----------|------------------------------------|
| UDP recv+parse   | <1 ms    | ~0.05 ms | struct.unpack + np.frombuffer      |
| RingBuffer put   | <0.1 ms  | ~0.01 ms | deque + lock                       |
| AmplitudeExtract | <0.5 ms  | ~0.1 ms  | EMA per frame (called 100x/sec)    |
| SlidingWindow    | <0.1 ms  | <0.01 ms | deque append                       |
| FeatureExtract   | <5 ms    | ~1-3 ms  | HampelFilter is dominant           |
| Detection push   | <1 ms    | ~0.1 ms  | State machine + threshold          |
| Viz update       | <50 ms   | ~5-15 ms | FuncAnimation at 20 Hz             |

HampelFilter is O(n_frames × n_features). For 50×64 = trivial. For 50×512 (80MHz 2×2)
consider scipy.signal or reduce window_size.
