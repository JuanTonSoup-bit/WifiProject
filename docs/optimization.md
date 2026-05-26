# Optimization Pass — Bottleneck Analysis & Acceleration Opportunities

This document captures the performance characterization of the system as built,
identifies the dominant CPU costs, and lists concrete optimization paths in
priority order. All numbers reference the benchmark in
`tests/benchmarks/latency_bench.py` (run with `python -m tests.benchmarks.latency_bench`).

---

## 1. Measured per-stage latency (n_sub=64, 1×1 antenna)

Tested on Python 3.14, no scipy:

| Stage              | Mean   | P50    | P95    | P99    | Throughput     |
|--------------------|--------|--------|--------|--------|----------------|
| Parse UDP          | 3 µs   | 3 µs   | 3 µs   | 4 µs   | 300,000 f/s    |
| DSP push (per frm) | 0.34 ms| 0 ms   | 1.81 ms| 2.16 ms| 3,000 f/s      |
| Feature extract    | 1.80 ms| 1.77 ms| 2.06 ms| 2.06 ms| 555 windows/s  |
| Detection push    | 6 µs   | 4 µs   | 6 µs   | 53 µs  | 150,000 f/s    |
| **Full pipeline** | 0.34 ms| 4 µs   | 1.80 ms| 2.16 ms| 3,000 f/s      |

At the 100 Hz input rate, the per-frame budget is 10 ms — we have **~4.6× margin**
at P99 with 64 subcarriers. Headroom shrinks for 80 MHz (256 subcarriers) and
multi-antenna configurations.

---

## 2. CPU-heavy stages (in order)

### 2.1 Hampel filter — DOMINANT (~70% of feature-extraction time)
Location: `pc/dsp/filters.py:hampel_filter`

The current implementation loops over frames in Python:
```python
for i in range(n_frames):
    lo, hi = max(0, i - window), min(n_frames, i + window + 1)
    neighborhood = data[lo:hi, :]
    local_median = np.median(neighborhood, axis=0)
    ...
```
This is O(n_frames × window × n_features). For window=50, sub=64 it runs in ~1 ms.
For 80 MHz / 2×2 (n_features = 1024), it stretches to ~15 ms — exceeds budget.

### 2.2 Feature extract — variance, percentile, baseline normalize
`np.var`, `np.percentile`, and `BaselineEstimator.normalize` together account
for the remaining ~30% of feature-extraction time. These are already vectorized.

### 2.3 Per-frame amplitude EMA
`pc/dsp/amplitude.py:AmplitudeExtractor.process` runs 100 times/sec but is
trivial (~50 µs per frame). Not a bottleneck.

### 2.4 UDP parse + struct unpack
~3 µs per packet. Not a bottleneck. Will only become one with multi-antenna
80 MHz streams (each packet ~16 KB instead of 2 KB).

---

## 3. Vectorization opportunities (highest ROI first)

### 3.1 Vectorize Hampel filter
Replace the Python loop with stride-based windowing using `np.lib.stride_tricks.sliding_window_view`:
```python
from numpy.lib.stride_tricks import sliding_window_view
windows = sliding_window_view(padded, (2*window+1, n_features))
local_median = np.median(windows, axis=2)
```
Expected: **5–10× speedup** on the Hampel stage for large arrays.

### 3.2 Use scipy.signal.medfilt
If scipy is available, `scipy.signal.medfilt2d` is C-implemented and ~20× faster
than the Python loop. Already detected at import time — the fallback exists for
scipy-less Pi/embedded contexts.

### 3.3 Single-shot amplitude pre-stacking
`FeatureExtractor.extract` currently calls `self._amp.process(f)` in a list
comprehension (one Python call per frame). For window_size=50 this is 50 calls.
Better: stack `csi_matrix` for the whole window once, apply `np.abs` once, then
apply the EMA across the entire window in a single vectorized call.
Expected: ~2× speedup on the amplitude prep step.

### 3.4 Pre-allocate output buffers
DSP stages currently allocate new numpy arrays per call. Pre-allocating
reusable buffers (size known from config) eliminates allocator pressure and
GC churn during sustained operation.

---

## 4. Multiprocessing opportunities

### 4.1 Split DSP from ingestion (current: single-process threading)
The Python GIL doesn't bottleneck us today because the heavy lifting is in
numpy (releases GIL). But at 80 MHz with multi-antenna, we may want
`multiprocessing.Process` for the DSP pipeline:

- **Process A**: ingestion + parse (I/O bound; GIL fine)
- **Process B**: DSP feature extraction (CPU bound; benefits from own GIL)
- **Process C**: detection + viz (light; GIL fine)

Communication via `multiprocessing.Queue` with `pickle.HIGHEST_PROTOCOL` for
fast serialization of numpy arrays.

### 4.2 Parallel per-antenna DSP for multi-RX setups
For 2×2 or 4×4 configurations, the antenna pairs are independent during
feature extraction. Use `concurrent.futures.ProcessPoolExecutor` with one
worker per antenna pair. Aggregate motion scores in the parent.

### 4.3 Pinned threads for receiver
Linux: pin the `csi-receiver` thread to a dedicated CPU core
(`os.sched_setaffinity`) to eliminate jitter from kernel scheduler decisions.

---

## 5. GPU acceleration points (future)

These are not needed at current scale but become attractive for:
- 160 MHz / multi-AP scenarios (1000s of features per frame)
- ML-based detection (CNN/LSTM on amplitude spectrograms)
- Multi-stream concurrent processing

### 5.1 CuPy drop-in for filter chain
`cupy` provides numpy-compatible GPU arrays. Replace `np` with `cp` in
`pc/dsp/filters.py` and the Hampel filter would run on GPU with no code rewrite.
Worthwhile when n_features > 4096.

### 5.2 Torch-based motion classifier
Once data is collected, train a small 1D-CNN on amplitude windows. Inference
on an entry-level GPU (e.g. RTX 3050) handles 1000s of windows/sec — plenty
for 20 Hz output rate. PyTorch `compile()` adds another 1.5–2× speedup.

### 5.3 GPU-accelerated FFT for breathing detection
For respiratory-rate features (which we don't currently extract), per-subcarrier
FFTs would be done on GPU. `cufft` saturates a consumer GPU at ~50 GFlops.

---

## 6. Memory & GC

- `RingBuffer` uses `collections.deque(maxlen=N)` — O(1) append/popleft, bounded memory.
- `VizDataBuffer` similarly bounded.
- `BaselineEstimator` allocates one float32 array of shape (n_features,) — negligible.
- **No leak vectors identified** in steady-state operation.

GC pause spikes (>100ms) were not observed during the 1000-frame benchmark.
If they appear with longer runs, consider `gc.set_threshold(700,10,10)` to
tune the generational GC.

---

## 7. Network optimization (when scaling beyond one Pi)

### 7.1 Larger socket buffer
Already set to 4 MB in `pc/ingestion/receiver.py:RECV_BUFSIZE`. For multi-Pi
setups, tune `net.core.rmem_max` via sysctl.

### 7.2 SO_REUSEPORT for multi-receiver
Linux only. Spawn N receiver threads on the same UDP port; the kernel
load-balances incoming packets. Useful for >4 simultaneous Pi sources.

### 7.3 Move parse into the kernel via eBPF
Extreme optimization: parse the wire format in an eBPF program attached to
the socket, deliver pre-parsed structs via ring buffer to userspace. Eliminates
~3 µs/packet × 100 packets/sec = 300 µs/sec — only matters for massive scaling.

---

## 8. Priority ranking (where to spend the next hour of engineering)

1. **Vectorize `hampel_filter`** using `sliding_window_view` (1 file, ~10 LOC, 5–10× speedup)
2. **Install scipy on the PC** (one-line `pip install scipy`, ~3× speedup on filters)
3. **Pre-stack amplitudes in `FeatureExtractor`** (eliminate per-frame Python overhead)
4. **Profile under 80 MHz / multi-antenna** to confirm new bottlenecks
5. (Only if needed) split DSP into separate process

---

## 9. What we *did not* over-engineer

Per the original constraint to "avoid enterprise overengineering," we did NOT:
- Add a queue per pipeline stage (we use direct callbacks where the consumer is fast)
- Use Cython/Numba (no measurable benefit at current scale; adds toolchain complexity)
- Run on CUDA by default (would require optional dep + significant testing burden)
- Use shared-memory ringbuffers (`SharedMemory`) — not needed for in-process pipeline
- Add Redis/Kafka for inter-stage messaging — local queues are sufficient

These remain options if scale or feature scope changes.
