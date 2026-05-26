"""Unit tests for the UDP packet parser."""

import sys
import os
import struct
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np

from pc.ingestion.parser import (
    MAGIC,
    HEADER_SIZE,
    InvalidPacketError,
    PacketStats,
    build_packet,
    parse_packet,
)
from tests.generators.packet_gen import SyntheticPacketGenerator


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.gen = SyntheticPacketGenerator(n_subcarriers=64, n_rx=1, n_tx=1, seed=1)

    def test_round_trip_basic(self):
        frame = self.gen.generate_frame("static", t=1.0)
        packet = build_packet(frame)
        parsed = parse_packet(packet)

        self.assertEqual(parsed.seq_num, frame.seq_num)
        self.assertEqual(parsed.timestamp_ns, frame.timestamp_ns)
        self.assertEqual(parsed.n_subcarriers, frame.n_subcarriers)
        self.assertEqual(parsed.n_rx, frame.n_rx)
        self.assertEqual(parsed.n_tx, frame.n_tx)
        self.assertEqual(parsed.bandwidth_mhz, frame.bandwidth_mhz)
        np.testing.assert_allclose(
            np.abs(parsed.csi_matrix), np.abs(frame.csi_matrix), rtol=1e-5
        )

    def test_correct_csi_shape(self):
        gen = SyntheticPacketGenerator(n_subcarriers=128, n_rx=2, n_tx=1, seed=2)
        frame = gen.generate_frame()
        packet = build_packet(frame)
        parsed = parse_packet(packet)
        self.assertEqual(parsed.csi_matrix.shape, (2, 1, 128))

    def test_sequence_number_preservation(self):
        for expected_seq in [0, 1, 255, 65535, 0xFFFFFFFF]:
            frame = self.gen.generate_frame()
            frame_with_seq = type(frame)(
                timestamp_ns=frame.timestamp_ns,
                seq_num=expected_seq,
                received_ns=frame.received_ns,
                csi_matrix=frame.csi_matrix,
                n_subcarriers=frame.n_subcarriers,
                n_rx=frame.n_rx,
                n_tx=frame.n_tx,
                bandwidth_mhz=frame.bandwidth_mhz,
                rssi=frame.rssi,
                noise_floor=frame.noise_floor,
            )
            packet = build_packet(frame_with_seq)
            parsed = parse_packet(packet)
            self.assertEqual(parsed.seq_num, expected_seq)

    def test_timestamp_preservation(self):
        frame = self.gen.generate_frame(t=12345.678)
        packet = build_packet(frame)
        parsed = parse_packet(packet)
        self.assertEqual(parsed.timestamp_ns, frame.timestamp_ns)

    def test_correct_amplitude_known_input(self):
        """CSI matrix of all-ones should give amplitude of all-ones."""
        import time
        from pc.common.types import CSIFrame
        ones = np.ones((1, 1, 64), dtype=np.complex64)
        frame = CSIFrame(
            timestamp_ns=1000, seq_num=0, received_ns=1000,
            csi_matrix=ones, n_subcarriers=64, n_rx=1, n_tx=1,
            bandwidth_mhz=20, rssi=-50.0, noise_floor=-95.0,
        )
        packet = build_packet(frame)
        parsed = parse_packet(packet)
        np.testing.assert_allclose(parsed.amplitude.ravel(), 1.0, atol=1e-5)


class TestInvalidPackets(unittest.TestCase):
    def test_too_short(self):
        with self.assertRaises(InvalidPacketError):
            parse_packet(b'\x00' * 10)

    def test_invalid_magic(self):
        gen = SyntheticPacketGenerator(seed=3)
        pkt = bytearray(gen.generate_packet())
        pkt[0] = 0xFF  # corrupt magic
        with self.assertRaises(InvalidPacketError):
            parse_packet(bytes(pkt))

    def test_truncated_csi(self):
        gen = SyntheticPacketGenerator(seed=4)
        pkt = gen.generate_packet()
        truncated = pkt[:HEADER_SIZE + 10]  # keep header but truncate CSI
        with self.assertRaises(InvalidPacketError):
            parse_packet(truncated)

    def test_stats_tracking(self):
        gen = SyntheticPacketGenerator(seed=5)
        stats = PacketStats()
        pkt = gen.generate_packet()
        parse_packet(pkt, stats)
        self.assertEqual(stats.parsed_ok, 1)

        bad = b'\xFF' * 30
        try:
            parse_packet(bad, stats)
        except InvalidPacketError:
            pass
        self.assertEqual(stats.bad_magic, 1)


if __name__ == "__main__":
    unittest.main()
