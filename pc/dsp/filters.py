"""DSP filter implementations for CSI signal conditioning."""

from __future__ import annotations

import numpy as np

try:
    from scipy import signal as sp_signal
    _SCIPY = True
except ImportError:
    _SCIPY = False


def hampel_filter(data: np.ndarray, window: int = 5, n_sigma: float = 3.0) -> np.ndarray:
    """
    Vectorized Hampel identifier: replaces outliers with local median.

    data shape: (n_frames, n_features)
    Returns cleaned array, same shape.

    For each frame position i and each feature independently:
      local_median = median(data[i-window:i+window+1, :], axis=0)
      local_mad = median(|data[...] - local_median|, axis=0)
      outlier if |data[i,:] - local_median| > n_sigma * 1.4826 * local_mad
    """
    if data.ndim != 2:
        raise ValueError(f"Expected 2D array (n_frames, n_features), got shape {data.shape}")

    n_frames, n_features = data.shape
    out = data.copy()

    for i in range(n_frames):
        lo = max(0, i - window)
        hi = min(n_frames, i + window + 1)
        neighborhood = data[lo:hi, :]  # (2*window+1, n_features)

        local_median = np.median(neighborhood, axis=0)
        local_mad = np.median(np.abs(neighborhood - local_median), axis=0)
        threshold = n_sigma * 1.4826 * local_mad

        outlier_mask = np.abs(data[i, :] - local_median) > threshold
        out[i, outlier_mask] = local_median[outlier_mask]

    return out


def exponential_moving_average(data: np.ndarray, alpha: float) -> np.ndarray:
    """
    EMA along axis 0 (time axis).

    data shape: (n_frames, n_features)
    Returns smoothed array, same shape.
    Uses manual loop since scipy.signal.lfilter IIR requires b=[alpha], a=[1, -(1-alpha)].
    """
    if _SCIPY:
        b = [alpha]
        a = [1.0, -(1.0 - alpha)]
        zi_shape = (1, data.shape[1]) if data.ndim > 1 else (1,)
        zi = sp_signal.lfilter_zi(b, a)
        if data.ndim == 2:
            out = np.empty_like(data)
            for col in range(data.shape[1]):
                out[:, col], _ = sp_signal.lfilter(b, a, data[:, col], zi=zi * data[0, col])
            return out
        else:
            result, _ = sp_signal.lfilter(b, a, data, zi=zi * data[0])
            return result

    # Manual fallback
    out = np.empty_like(data, dtype=np.float32)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1.0 - alpha) * out[i - 1]
    return out


def savitzky_golay_smooth(data: np.ndarray, window: int = 11, polyorder: int = 3) -> np.ndarray:
    """
    Savitzky-Golay smoothing along time axis (axis 0).

    data shape: (n_frames, n_features) or (n_frames,)
    Falls back to uniform_filter1d if scipy not available.
    """
    window = min(window, len(data) if data.ndim == 1 else data.shape[0])
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return data.copy()

    if _SCIPY:
        return sp_signal.savgol_filter(data, window_length=window, polyorder=polyorder, axis=0)

    from scipy.ndimage import uniform_filter1d  # type: ignore[import]
    try:
        return uniform_filter1d(data.astype(np.float32), size=window, axis=0)
    except ImportError:
        pass

    # Pure numpy fallback: simple moving average via cumsum
    if data.ndim == 1:
        kernel = np.ones(window) / window
        pad = window // 2
        padded = np.pad(data, (pad, pad), mode="edge")
        return np.convolve(padded, kernel, mode="valid").astype(data.dtype)
    else:
        out = np.empty_like(data, dtype=np.float32)
        kernel = np.ones(window) / window
        pad = window // 2
        for col in range(data.shape[1]):
            padded = np.pad(data[:, col], (pad, pad), mode="edge")
            out[:, col] = np.convolve(padded, kernel, mode="valid")
        return out


def bandpass_variance(
    window_data: np.ndarray,
    low_hz: float = 0.5,
    high_hz: float = 10.0,
    fs: float = 100.0,
) -> np.ndarray:
    """
    Compute variance of bandpass-filtered signal per feature.

    Isolates motion/breathing frequency band (0.5–10 Hz typical).

    window_data shape: (n_frames, n_features)
    Returns per-feature variance, shape (n_features,)
    Falls back to raw variance if scipy not available.
    """
    if _SCIPY and window_data.shape[0] >= 9:
        nyq = fs / 2.0
        low = max(low_hz / nyq, 1e-6)
        high = min(high_hz / nyq, 1.0 - 1e-6)
        if low >= high:
            return np.var(window_data, axis=0)
        try:
            b, a = sp_signal.butter(4, [low, high], btype="band")
            filtered = sp_signal.filtfilt(b, a, window_data, axis=0)
            return np.var(filtered, axis=0, ddof=1)
        except Exception:
            pass

    return np.var(window_data, axis=0, ddof=1 if window_data.shape[0] > 1 else 0)


def linear_detrend(data: np.ndarray) -> np.ndarray:
    """Remove linear trend from each feature column independently (axis 0)."""
    if _SCIPY:
        return sp_signal.detrend(data, axis=0, type="linear")

    n = data.shape[0]
    k = np.arange(n, dtype=np.float32)
    k -= k.mean()
    k_var = np.dot(k, k)
    if k_var == 0:
        return data.copy()

    if data.ndim == 1:
        slope = np.dot(k, data) / k_var
        intercept = data.mean() - slope * (n - 1) / 2.0
        trend = slope * np.arange(n) + intercept
        return data - trend

    slopes = k @ data / k_var  # (n_features,)
    intercepts = data.mean(axis=0) - slopes * (n - 1) / 2.0
    trend = np.outer(np.arange(n), slopes) + intercepts
    return (data - trend).astype(np.float32)
