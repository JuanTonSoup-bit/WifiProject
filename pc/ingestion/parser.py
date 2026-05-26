"""UDP packet parser: raw bytes -> CSIFrame."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

import numpy as np

from pc.common.types import CSIFrame

MAGIC = b'\xC5\x49\x31\x00'
HEADER_FORMAT = ">4sQIHBBBbh2s"   # big-endian: magic, ts_ns, seq, n_sub, n_rx, n_tx, bw, rssi, noise, pad
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 26 bytes


class InvalidPacketError(ValueError):
    pass


@dataclass
class PacketStats:
    parsed_ok: int = 0
    bad_magic: int = 0
    too_short: int = 0
    bad_shape: int = 0

    @property
    def total_errors(self) -> int:
        return self.bad_magic + self.too_short + self.bad_shape

    def to_dict(self) -> dict:
        return {
            "parsed_ok": self.parsed_ok,
            "bad_magic": self.bad_magic,
            "too_short": self.too_short,
            "bad_shape": self.bad_shape,
        }


def parse_packet(data: bytes, stats: PacketStats | None = None) -> CSIFrame:
    """
    Parse a raw UDP datagram into a CSIFrame.

    Packet layout (big-endian header, LE CSI payload):
      [4B magic][8B timestamp_ns][4B seq_num][2B n_subcarriers]
      [1B n_rx][1B n_tx][1B bandwidth_mhz][1B rssi(int8)]
      [2B noise_floor(int16)][2B pad]
      [N bytes complex64 LE: n_rx * n_tx * n_subcarriers * 8 bytes]
    """
    received_ns = time.monotonic_ns()

    if len(data) < HEADER_SIZE:
        if stats:
            stats.too_short += 1
        raise InvalidPacketError(
            f"Packet too short: {len(data)} < {HEADER_SIZE}"
        )

    (magic, timestamp_ns, seq_num, n_subcarriers,
     n_rx, n_tx, bandwidth_mhz, rssi, noise_floor, _pad) = struct.unpack_from(
        HEADER_FORMAT, data, 0
    )

    if magic != MAGIC:
        if stats:
            stats.bad_magic += 1
        raise InvalidPacketError(
            f"Bad magic: {magic!r} (expected {MAGIC!r})"
        )

    expected_csi_bytes = int(n_rx) * int(n_tx) * int(n_subcarriers) * 8  # 8 = 2 * float32
    expected_total = HEADER_SIZE + expected_csi_bytes

    if len(data) < expected_total:
        if stats:
            stats.bad_shape += 1
        raise InvalidPacketError(
            f"Packet truncated: {len(data)} < {expected_total} "
            f"(n_rx={n_rx}, n_tx={n_tx}, n_sub={n_subcarriers})"
        )

    csi_raw = np.frombuffer(data, dtype="<f4", count=int(n_rx) * int(n_tx) * int(n_subcarriers) * 2,
                            offset=HEADER_SIZE)
    csi_complex = csi_raw[0::2] + 1j * csi_raw[1::2]
    csi_matrix = csi_complex.astype(np.complex64).reshape(int(n_rx), int(n_tx), int(n_subcarriers))

    if stats:
        stats.parsed_ok += 1

    return CSIFrame(
        timestamp_ns=int(timestamp_ns),
        seq_num=int(seq_num),
        received_ns=received_ns,
        csi_matrix=csi_matrix,
        n_subcarriers=int(n_subcarriers),
        n_rx=int(n_rx),
        n_tx=int(n_tx),
        bandwidth_mhz=int(bandwidth_mhz),
        rssi=float(rssi),
        noise_floor=float(noise_floor),
    )


def build_packet(frame: CSIFrame) -> bytes:
    """Serialize a CSIFrame back to UDP packet bytes (for testing/replay)."""
    header = struct.pack(
        HEADER_FORMAT,
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
    # Interleave real/imag as float32 LE
    csi_flat = frame.csi_matrix.ravel().astype(np.complex64)
    interleaved = np.empty(len(csi_flat) * 2, dtype="<f4")
    interleaved[0::2] = csi_flat.real
    interleaved[1::2] = csi_flat.imag
    return header + interleaved.tobytes()
