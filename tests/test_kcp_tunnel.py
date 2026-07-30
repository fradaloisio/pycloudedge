#!/usr/bin/env python3
"""
KCP tunnel receive-path tests
=============================

A KCP datagram can carry several segments back to back: the sender fills a
buffer up to the MTU and only then puts it on the wire, so a short segment is
routinely followed by another one.

Parsing only the first segment and dropping the rest is indistinguishable from
packet loss on the path, except that it cannot be repaired: the sender
retransmits the same pair and the trailing segment lands in the same unread
position every time, so the delivery hole never closes.

These tests exercise the public receive behaviour only - the return value of
process_input(), poll_data() and the delivery cursor - not internal counters.
"""

import os
import sys
import unittest

# Add the parent directory to the path so we can import cloudedge
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudedge.p2p.kcp_tunnel import (
    KCP_CMD_ACK,
    KCP_CMD_PUSH,
    KcpTunnel,
    build_kcp_segment,
)


def push(sn, data=b"payload!", frg=0):
    """One PUSH segment. frg=0 terminates a message."""
    return build_kcp_segment(cmd=KCP_CMD_PUSH, sn=sn, una=0, ts=sn, frg=frg, data=data)


def ack(sn):
    return build_kcp_segment(cmd=KCP_CMD_ACK, sn=sn, una=0, ts=sn, frg=0, data=b"")


class TestKcpReceivePath(unittest.TestCase):
    """Ordered delivery out of datagrams holding one or more segments."""

    def setUp(self):
        self.tunnel = KcpTunnel(lambda _data: None)

    def drain(self):
        """Everything the tunnel queued behind the returned message."""
        out = []
        while True:
            msg = self.tunnel.poll_data()
            if msg is None:
                return out
            out.append(msg)

    def test_single_segment_datagrams(self):
        for sn in (10, 11, 12):
            self.assertEqual(self.tunnel.process_input(push(sn))[0], "data")
        self.assertEqual(self.tunnel.next_recv_sn, 13)
        self.assertEqual(self.drain(), [])

    def test_two_data_segments_in_one_datagram(self):
        """Both segments must be delivered, and in order."""
        result = self.tunnel.process_input(push(31, b"A" * 8) + push(32, b"B" * 8))
        self.assertEqual(result, ("data", b"A" * 8))
        self.assertEqual(self.drain(), [b"B" * 8])
        # no hole was left behind: the cursor moved past both segments
        self.assertEqual(self.tunnel.next_recv_sn, 33)
        self.assertEqual(self.tunnel.recv_buf, {})
        # and the next segment keeps flowing instead of blocking
        self.assertEqual(
            self.tunnel.process_input(push(33, b"C" * 8)), ("data", b"C" * 8)
        )

    def test_data_segment_behind_an_ack(self):
        self.tunnel.process_input(push(100))
        result = self.tunnel.process_input(ack(7) + push(101, b"Z" * 8))
        self.assertEqual(result, ("data", b"Z" * 8))
        self.assertEqual(self.tunnel.next_recv_sn, 102)

    def test_genuine_gap_blocks_until_retransmission(self):
        """Real loss must still hold delivery back, then heal in order."""
        self.tunnel.process_input(push(1, b"a" * 8))
        result = self.tunnel.process_input(push(3, b"c" * 8))  # 2 is missing
        self.assertEqual(result, ("fragment", b"c" * 8))
        self.assertEqual(self.tunnel.next_recv_sn, 2)

        result = self.tunnel.process_input(push(2, b"b" * 8))  # retransmission
        self.assertEqual(result, ("data", b"b" * 8))
        self.assertEqual(self.drain(), [b"c" * 8])
        self.assertEqual(self.tunnel.next_recv_sn, 4)

    def test_fragmented_message_inside_one_datagram(self):
        """A message split across two segments of the same datagram."""
        self.tunnel.process_input(push(20, b"h" * 8))
        result = self.tunnel.process_input(
            push(21, b"XX", frg=1) + push(22, b"YY", frg=0)
        )
        self.assertEqual(result, ("data", b"XXYY"))
        self.assertEqual(self.tunnel.next_recv_sn, 23)

    def test_truncated_trailing_segment_is_ignored(self):
        self.tunnel.process_input(push(1))
        result = self.tunnel.process_input(push(2, b"ok" * 4) + push(3)[:15])
        self.assertEqual(result, ("data", b"ok" * 4))
        self.assertEqual(self.tunnel.next_recv_sn, 3)

    def test_trailing_non_segment_bytes_are_ignored(self):
        """Meari cameras append a constant byte after the last segment."""
        result = self.tunnel.process_input(
            push(1, b"p" * 8) + push(2, b"q" * 8) + b"\x00"
        )
        self.assertEqual(result, ("data", b"p" * 8))
        self.assertEqual(self.drain(), [b"q" * 8])
        self.assertEqual(self.tunnel.next_recv_sn, 3)

    def test_non_kcp_datagrams_are_rejected(self):
        self.assertIsNone(self.tunnel.process_input(b"\x01\x02\x03"))
        self.assertIsNone(self.tunnel.process_input(b"\x00" * 40))

    def test_duplicate_below_cursor_is_reported_as_dup(self):
        self.tunnel.process_input(push(5))
        self.tunnel.process_input(push(6))
        self.assertEqual(self.tunnel.process_input(push(5)), ("dup", 5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
