"""Synthetic CSI packet generator for testing without hardware."""

from __future__ import annotations

import random
import struct
import time
from typing import Callable, List, Optional

import numpy as np

# Mirror the shared wire format constants
MAGIC = b'\xC5\x49\x31\x00'
HEADER_FMT = ">4sQIHBBBbh2s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# Fixed seed for reproducibility in unit tests
_RNG = np.random.default_rng(42)


# Mode generators are now methods on SyntheticPacketGenerator so they can
# maintain persistent state across frames (a static scene needs a STABLE
# channel, not fresh randomness each call).


class SyntheticPacketGenerator:
    """
    Generates synthetic CSI packets in the shared wire format.

    Use a fixed seed for deterministic unit tests:
        gen = SyntheticPacketGenerator(seed=42)
    """

    def __init__(
        self,
        n_subcarriers: int = 64,
        n_rx: int = 1,
        n_tx: int = 1,
        bandwidth_mhz: int = 20,
        sample_rate_hz: float = 100.0,
        seed: int = 42,
    ) -> None:
        self.n_subcarriers = n_subcarriers
        self.n_rx = n_rx
        self.n_tx = n_tx
        self.bandwidth_mhz = bandwidth_mhz
        self.sample_rate_hz = sample_rate_hz
        self._rng = np.random.default_rng(seed)
        self.seq_num: int = 0

        # Persistent channel baseline — must remain stable across "static" frames.
        # Generated once at init using a dedicated RNG for reproducibility.
        base_rng = np.random.default_rng(seed)
        self._channel_base = (
            base_rng.standard_normal((n_rx, n_tx, n_subcarriers))
            + 1j * base_rng.standard_normal((n_rx, n_tx, n_subcarriers))
        ).astype(np.complex64)

    def _gen_static(self, t: float) -> np.ndarray:
        """Stable channel with small additive noise (~5%)."""
        noise = 0.05 * (
            self._rng.standard_normal(self._channel_base.shape)
            + 1j * self._rng.standard_normal(self._channel_base.shape)
        )
        return (self._channel_base + noise).astype(np.complex64)

    def _gen_motion(self, t: float) -> np.ndarray:
        """Stable channel modulated by a 2 Hz sinusoid + larger noise."""
        motion_amp = 0.5 * np.sin(2 * np.pi * 2.0 * t)
        modulated = self._channel_base * (1.0 + motion_amp)
        noise = 0.1 * (
            self._rng.standard_normal(self._channel_base.shape)
            + 1j * self._rng.standard_normal(self._channel_base.shape)
        )
        return (modulated + noise).astype(np.complex64)

    def _gen_noise(self, t: float) -> np.ndarray:
        """Random-magnitude bursts (no persistent channel)."""
        scale = 2.0 if (int(t * 5) % 3 == 0) else 0.1
        raw = scale * (
            self._rng.standard_normal(self._channel_base.shape)
            + 1j * self._rng.standard_normal(self._channel_base.shape)
        )
        return raw.astype(np.complex64)

    def _gen_fading(self, t: float) -> np.ndarray:
        """Slow channel fade (0.05 Hz)."""
        fade = 0.8 + 0.4 * np.sin(2 * np.pi * 0.05 * t)
        return (self._channel_base * fade).astype(np.complex64)

    def _gen(self, mode: str, t: float) -> np.ndarray:
        return {
            "static": self._gen_static,
            "motion": self._gen_motion,
            "noise": self._gen_noise,
            "fading": self._gen_fading,
        }.get(mode, self._gen_static)(t)

    def generate_packet(self, mode: str = "static", t: float = 0.0) -> bytes:
        """Returns raw UDP packet bytes in the shared wire format."""
        from pc.common.types import CSIFrame
        frame = self.generate_frame(mode, t)

        csi_flat = frame.csi_matrix.ravel().astype(np.complex64)
        interleaved = np.empty(len(csi_flat) * 2, dtype="<f4")
        interleaved[0::2] = csi_flat.real
        interleaved[1::2] = csi_flat.imag

        header = struct.pack(
            HEADER_FMT,
            MAGIC,
            frame.timestamp_ns,
            frame.seq_num,
            frame.n_subcarriers,
            frame.n_rx,
            frame.n_tx,
            frame.bandwidth_mhz,
            int(frame.rssi),
            int(frame.noise_floor),
            b'\x00\x00',
        )
        return header + interleaved.tobytes()

    def generate_frame(self, mode: str = "static", t: float = 0.0):
        """Returns a CSIFrame directly (no UDP parsing needed)."""
        from pc.common.types import CSIFrame

        csi = self._gen(mode, t)

        ts = int(t * 1e9) if t > 0 else time.monotonic_ns()
        frame = CSIFrame(
            timestamp_ns=ts,
            seq_num=self.seq_num,
            received_ns=ts + 100_000,
            csi_matrix=csi,
            n_subcarriers=self.n_subcarriers,
            n_rx=self.n_rx,
            n_tx=self.n_tx,
            bandwidth_mhz=self.bandwidth_mhz,
            rssi=-65.0,
            noise_floor=-95.0,
        )
        self.seq_num = (self.seq_num + 1) & 0xFFFFFFFF
        return frame

    def generate_stream(
        self,
        n_frames: int,
        mode: str = "static",
        add_gaps: bool = False,
        gap_rate: float = 0.01,
    ) -> List[bytes]:
        """Returns list of packet bytes."""
        pkts = []
        dt = 1.0 / self.sample_rate_hz
        for i in range(n_frames):
            t = i * dt
            pkt = self.generate_packet(mode, t)
            if add_gaps and self._rng.random() < gap_rate:
                # Simulate a gap: increment seq but don't include packet
                self.seq_num = (self.seq_num + 1) & 0xFFFFFFFF
                continue
            pkts.append(pkt)
        return pkts

    def inject_packet_loss(self, packets: List[bytes], loss_rate: float) -> List[bytes]:
        """Randomly drop packets to simulate network loss."""
        return [p for p in packets if self._rng.random() >= loss_rate]

    def inject_reorder(self, packets: List[bytes], reorder_rate: float = 0.01) -> List[bytes]:
        """Randomly swap adjacent packets."""
        pkts = list(packets)
        for i in range(len(pkts) - 1):
            if self._rng.random() < reorder_rate:
                pkts[i], pkts[i + 1] = pkts[i + 1], pkts[i]
        return pkts
