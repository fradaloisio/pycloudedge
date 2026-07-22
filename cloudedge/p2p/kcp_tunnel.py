#!/usr/bin/env python3
"""
Minimal KCP (KCP protocol) implementation for Meari P2P tunnels.

The Meari P2P SDK (libppsdk.so) uses KCP for reliable data delivery over UDP.
The KCP conversation ID is hardcoded to 0x0c (12).

Protocol layers:
  1. IVA handshake (0xFF 0x01 + session IDs + 0x7012 marker) - sent raw
  2. KCP framing (conv=0x0c) for all subsequent data
  3. IVA data frame (0xFF 0x01 + same IDs + 0x7010 + len + data) as KCP payload
  4. PRTP data (NV_MsgHead + JSON + binary) inside IVA data frame

KCP segment wire format (24-byte header):
  [0x00] conv    4B LE  conversation ID (always 0x0000000c)
  [0x04] cmd     1B     command: PUSH=81, ACK=82, WASK=83, WINS=84
  [0x05] frg     1B     fragment count (0 = last/only fragment)
  [0x06] wnd     2B LE  receive window size
  [0x08] ts      4B LE  timestamp (ms)
  [0x0C] sn      4B LE  sequence number
  [0x10] una     4B LE  unacknowledged sequence number
  [0x14] len     4B LE  payload length
  [0x18] data    ...    payload

Ref: https://github.com/skywind3000/kcp
"""

import logging
import os
import struct
import time

# KCP constants
KCP_CONV = 0x0000000c  # Meari hardcoded conversation ID
KCP_CMD_PUSH = 81   # 0x51 - data
KCP_CMD_ACK = 82    # 0x52 - acknowledgment
KCP_CMD_WASK = 83   # 0x53 - window probe request
KCP_CMD_WINS = 84   # 0x54 - window probe response
KCP_HEADER_SIZE = 24
KCP_MSS = 1176      # 0x498 - max segment size
KCP_WND = 1024      # Receive window advertised by the official Android client

_LOGGER = logging.getLogger(__name__)

# IVA frame constants
IVA_MAGIC = b'\xFF\x01'
IVA_FRAME_SIZE = 20
IVA_TYPE_HANDSHAKE = 0x7012
IVA_TYPE_DATA = 0x7010


def _build_iva_frame(type_marker, data, session_id1=None, session_id2=None):
    """Build an IVA frame with given type and optional data.

    The session IDs (at offsets 4 and 8) must be consistent within a session.
    """
    if session_id1 is None:
        session_id1 = int.from_bytes(os.urandom(4), "little") & 0x0FFFFFFF
    if session_id2 is None:
        session_id2 = int.from_bytes(os.urandom(4), "little") & 0x0FFFFFFF
    header = struct.pack("<BBHI I HH I",
        0xFF, 0x01, 0, session_id1,
        session_id2,
        0, type_marker,
        len(data),
    )
    return header + data


def build_iva_data_frame(data, session_id1=None, session_id2=None):
    """Build an IVA data frame wrapping application data."""
    return _build_iva_frame(IVA_TYPE_DATA, data, session_id1, session_id2)


def build_iva_handshake(session_id1=None, session_id2=None):
    """Build a 20-byte IVA handshake frame."""
    return _build_iva_frame(IVA_TYPE_HANDSHAKE, b"", session_id1, session_id2)


def parse_iva_frame(data):
    """Parse an IVA frame. Returns (type_marker, session_id1, session_id2, payload) or None."""
    if len(data) < IVA_FRAME_SIZE:
        return None
    if data[0] != 0xFF or data[1] != 0x01:
        return None
    session_id1 = struct.unpack_from("<I", data, 0x04)[0]
    session_id2 = struct.unpack_from("<I", data, 0x08)[0]
    type_marker = struct.unpack_from("<H", data, 0x0E)[0]
    data_len = struct.unpack_from("<I", data, 0x10)[0]
    if data_len > len(data) - IVA_FRAME_SIZE:
        return None
    payload = data[IVA_FRAME_SIZE:IVA_FRAME_SIZE + data_len] if data_len > 0 else b""
    return type_marker, session_id1, session_id2, payload


def build_kcp_segment(cmd, sn=0, una=0, wnd=KCP_WND, ts=0, frg=0, data=b""):
    """Build a KCP segment."""
    header = struct.pack("<IBBHIIII",
        KCP_CONV,
        cmd,
        frg,
        wnd,
        ts,
        sn,
        una,
        len(data),
    )
    return header + data


def parse_kcp_segment(raw):
    """Parse the first segment in a valid KCP datagram."""
    segments = parse_kcp_segments(raw)
    return segments[0] if segments else None


def parse_kcp_segments(raw):
    """Parse every concatenated KCP segment in one UDP datagram.

    KCP deliberately allows several segments to share a datagram. Reject the
    whole datagram if any declared payload is truncated so partial media is
    never passed to the frame parser.
    """
    segments = []
    offset = 0

    while offset < len(raw):
        if len(raw) - offset < KCP_HEADER_SIZE:
            return None

        conv, cmd, frg, wnd, ts, sn, una, length = struct.unpack_from(
            "<IBBHIIII", raw, offset
        )
        if conv != KCP_CONV:
            return None

        payload_start = offset + KCP_HEADER_SIZE
        payload_end = payload_start + length
        if payload_end > len(raw):
            return None

        segments.append(
            {
                "conv": conv,
                "cmd": cmd,
                "frg": frg,
                "wnd": wnd,
                "ts": ts,
                "sn": sn,
                "una": una,
                "len": length,
                "data": raw[payload_start:payload_end],
            }
        )
        offset = payload_end

    return segments or None


class KcpTunnel:
    """Minimal KCP tunnel for sending/receiving data over UDP P2P.

    Handles:
      - IVA handshake with consistent session IDs
      - KCP data segments (PUSH/ACK)
      - IVA data framing around application data
    """

    def __init__(self, send_func):
        """
        Args:
            send_func: callable(data: bytes) -> None
                       Function to send raw UDP data to peer.
        """
        self.send_func = send_func
        self.sn_send = 0           # Next outgoing sequence number
        self.una_recv = 0          # Highest SN seen + 1 (internal tracking)
        self.ts_base = int(time.time() * 1000) & 0xFFFFFFFF
        self.handshake_done = False

        # Session IDs - consistent across all frames in this session
        self.session_id1 = int.from_bytes(os.urandom(4), "little") & 0x0FFFFFFF
        self.session_id2 = int.from_bytes(os.urandom(4), "little") & 0x0FFFFFFF

        # Peer's session IDs (from handshake response)
        self.peer_id1 = None
        self.peer_id2 = None

        # Sent segment buffer for retransmission (sn -> raw_segment_bytes)
        self.sent_segments = {}
        # Track which sn have been ACK'd
        self.acked_sns = set()

        # Receive-side fragment reassembly buffer
        # Accumulates KCP fragment data for multi-fragment messages.
        # frg counts DOWN: N-1, N-2, ..., 0.  frg==0 means last fragment.
        self.recv_frag_buf = []

        # Receive buffer for out-of-order segments: sn -> (frg, data)
        self.recv_buf = {}
        # Next expected receive sequence number for ordered delivery
        # Initialized to -1 (sentinel); set to peer's first sn on first PUSH
        self.next_recv_sn = -1

        # Queue of fully reassembled messages ready for delivery
        self.recv_queue = []

        # A persistent gap can split a fragmented KCP message. When the
        # safety-net gap skip is used, discard through the next frg=0 boundary
        # instead of delivering a truncated media frame.
        self._discard_until_message_end = False

        # Deferred ACK queue: list of (sn, ts) to send in batch
        self.pending_acks = []

    def _ts(self):
        """Current KCP timestamp."""
        return (int(time.time() * 1000) - self.ts_base) & 0xFFFFFFFF

    def send_handshake(self):
        """Send IVA handshake frame wrapped in KCP PUSH segment."""
        frame = build_iva_handshake(self.session_id1, self.session_id2)
        self.send_data(frame)

    def wrap_iva(self, data):
        """Wrap data in IVA data frame with session IDs."""
        return build_iva_data_frame(data, self.session_id1, self.session_id2)

    def send_data(self, data):
        """Send data through KCP. Handles fragmentation if needed."""
        offset = 0
        fragments = []
        while offset < len(data):
            chunk = data[offset:offset + KCP_MSS]
            fragments.append(chunk)
            offset += KCP_MSS

        if not fragments:
            fragments = [b""]

        # Send fragments (frg counts down: n-1, n-2, ..., 0)
        n = len(fragments)
        for i, chunk in enumerate(fragments):
            frg = n - 1 - i
            seg = build_kcp_segment(
                cmd=KCP_CMD_PUSH,
                sn=self.sn_send,
                una=max(self.next_recv_sn, 0),
                wnd=KCP_WND,
                ts=self._ts(),
                frg=frg,
                data=chunk,
            )
            self.sent_segments[self.sn_send] = (chunk, frg)
            self.send_func(seg)
            self.sn_send += 1

    def retransmit_unacked(self):
        """Retransmit all sent segments that haven't been ACK'd yet."""
        for sn in sorted(self.sent_segments.keys()):
            if sn not in self.acked_sns:
                chunk, frg = self.sent_segments[sn]
                seg = build_kcp_segment(
                    cmd=KCP_CMD_PUSH,
                    sn=sn,
                    una=max(self.next_recv_sn, 0),
                    wnd=KCP_WND,
                    ts=self._ts(),
                    frg=frg,
                    data=chunk,
                )
                self.send_func(seg)

    def send_iva_data(self, data):
        """Wrap data in IVA frame, then send through KCP."""
        iva_frame = self.wrap_iva(data)
        self.send_data(iva_frame)

    def poll_data(self):
        """Return the next queued reassembled message, or None."""
        if self.recv_queue:
            return self.recv_queue.pop(0)
        return None

    def flush_acks(self):
        """Send all pending ACKs as compound KCP packet(s).

        Called after processing a batch of incoming packets to reduce
        sendto() syscall overhead.  Each compound packet contains multiple
        concatenated 24-byte KCP ACK segments with the latest cumulative UNA.
        """
        if not self.pending_acks:
            return

        # Deduplicate: keep last ts per sn
        by_sn = {}
        for sn, ts in self.pending_acks:
            by_sn[sn] = ts
        self.pending_acks.clear()

        # Use cumulative UNA (next_recv_sn) so the camera knows which
        # segments we're still missing and retransmits them (fast retransmit
        # triggers after fastack >= 2).  The camera's 128-segment send window
        # fills in ~1.2s; fast retransmit recovers gaps in ~200ms.
        # Gap skip at 2s is the safety net for persistent losses.
        una = max(self.next_recv_sn, 0)

        # Build compound ACK packet(s), splitting at ~1200 bytes to stay
        # under typical MTU (each ACK segment is 24 bytes → ~50 per packet)
        buf = bytearray()
        for sn in sorted(by_sn):
            buf += build_kcp_segment(
                cmd=KCP_CMD_ACK,
                sn=sn,
                una=una,
                wnd=KCP_WND,
                ts=by_sn[sn],
            )
            if len(buf) >= 1200:
                self.send_func(bytes(buf))
                buf.clear()
        if buf:
            self.send_func(bytes(buf))

    def send_gap_nudge(self):
        """Send ACKs for segments above current gap to trigger fast retransmit.

        When next_recv_sn is missing from recv_buf but higher segments exist,
        the camera's KCP should retransmit the missing segment upon receiving
        these "skip" ACKs.  Call periodically when video stalls.
        """
        if self.next_recv_sn < 0 or not self.recv_buf:
            return False
        # Check if there's actually a gap at next_recv_sn
        if self.next_recv_sn in self.recv_buf:
            return False  # No gap — will be assembled on next process_input
        above_gap = sorted(sn for sn in self.recv_buf if sn > self.next_recv_sn)[:20]
        if not above_gap:
            return False
        una = max(self.next_recv_sn, 0)
        buf = bytearray()
        for sn in above_gap:
            buf += build_kcp_segment(
                cmd=KCP_CMD_ACK,
                sn=sn,
                una=una,
                wnd=KCP_WND,
                ts=self._ts(),
            )
        self.send_func(bytes(buf))
        return True

    def skip_gap(self):
        """Skip past ALL persistent gaps to unblock delivery.

        When next_recv_sn is missing but higher segments are buffered,
        advance next_recv_sn past EVERY gap (not just the first one).
        This loses the missing segment(s) but unblocks delivery of all
        buffered data immediately.
        Returns True if any gap was skipped and messages may be available.
        """
        if self.next_recv_sn < 0 or not self.recv_buf:
            return False
        if self.next_recv_sn in self.recv_buf:
            return False  # No gap

        any_skipped = False
        total_gaps = 0
        first_skip_sn = self.next_recv_sn

        # Loop: skip gap, assemble contiguous run, check for next gap, repeat
        while True:
            # Find the lowest buffered SN above the current gap
            above = [sn for sn in self.recv_buf if sn > self.next_recv_sn]
            if not above:
                break
            new_sn = min(above)
            gap_size = new_sn - self.next_recv_sn
            total_gaps += gap_size
            # Both the partial message before the gap and the first message
            # after it may be incomplete. Discard through the next frg=0
            # boundary; _drain_receive_buffer keeps this state if the boundary
            # has not arrived yet.
            self.recv_frag_buf = []
            self.next_recv_sn = new_sn
            self._discard_until_message_end = True
            any_skipped = True

            self.recv_queue.extend(self._drain_receive_buffer())

            # If next_recv_sn is now in recv_buf (no gap), we're done
            if self.next_recv_sn in self.recv_buf or not self.recv_buf:
                break
            # Otherwise there's another gap — loop and skip it too

        if any_skipped:
            _LOGGER.debug(
                "KCP skipped gaps: sn %s→%s (%s missing), buf=%s, queued=%s",
                first_skip_sn,
                self.next_recv_sn,
                total_gaps,
                len(self.recv_buf),
                len(self.recv_queue),
            )
        return any_skipped

    def _drain_receive_buffer(self):
        """Assemble all contiguous receive segments into complete messages."""
        messages = []
        while self.next_recv_sn in self.recv_buf:
            frg, data = self.recv_buf.pop(self.next_recv_sn)
            self.next_recv_sn += 1

            if self._discard_until_message_end:
                if frg == 0:
                    self._discard_until_message_end = False
                continue

            self.recv_frag_buf.append(data)
            if frg == 0:
                messages.append(b"".join(self.recv_frag_buf))
                self.recv_frag_buf = []

        return messages

    def _process_kcp_segment(self, seg):
        """Process one already validated KCP segment."""
        cmd = seg["cmd"]

        if cmd == KCP_CMD_PUSH:
            if seg["sn"] >= self.una_recv:
                self.una_recv = seg["sn"] + 1

            sn = seg["sn"]
            if self.next_recv_sn >= 0 and sn < self.next_recv_sn:
                self.pending_acks.append((sn, seg["ts"]))
                return ("dup", sn)

            self.recv_buf[sn] = (seg["frg"], seg["data"])
            if self.next_recv_sn < 0:
                self.next_recv_sn = sn

            messages = self._drain_receive_buffer()
            self.pending_acks.append((sn, seg["ts"]))

            if not messages:
                return ("fragment", seg["data"])

            for message in messages[1:]:
                self.recv_queue.append(message)

            data = messages[0]
            if len(data) >= IVA_FRAME_SIZE and data.startswith(IVA_MAGIC):
                iva = parse_iva_frame(data)
                if not iva:
                    return None
                type_marker, id1, id2, payload = iva
                if type_marker == IVA_TYPE_HANDSHAKE:
                    self.handshake_done = True
                    self.peer_id1 = id1
                    self.peer_id2 = id2
                    return ("handshake", (id1, id2))
                if type_marker == IVA_TYPE_DATA:
                    return ("data", payload)

            return ("data", data)

        if cmd == KCP_CMD_ACK:
            self.acked_sns.add(seg["sn"])
            self.sent_segments.pop(seg["sn"], None)
            return ("ack", seg["sn"])

        if cmd == KCP_CMD_WASK:
            wins = build_kcp_segment(
                cmd=KCP_CMD_WINS,
                sn=self.sn_send,
                una=max(self.next_recv_sn, 0),
                wnd=KCP_WND,
                ts=self._ts(),
            )
            self.send_func(wins)
            return ("wask", None)

        if cmd == KCP_CMD_WINS:
            return ("wins", seg["wnd"])

        return None

    def process_input(self, raw):
        """Process incoming UDP data.

        Returns:
            - ("handshake", (id1, id2)) for IVA handshake
            - ("data", bytes) for complete KCP data
            - ("ack", sn) for ACK
            - None for unrecognized data
        """
        if len(raw) < 4:
            return None

        # Check for IVA frame (handshake/heartbeat)
        if raw[0] == 0xFF and raw[1] == 0x01:
            iva = parse_iva_frame(raw)
            if iva:
                type_marker, id1, id2, payload = iva
                if type_marker == IVA_TYPE_HANDSHAKE:
                    self.handshake_done = True
                    self.peer_id1 = id1
                    self.peer_id2 = id2
                    return ("handshake", (id1, id2))
                if type_marker == IVA_TYPE_DATA:
                    return ("iva_data", payload)
                return ("iva", payload)
            return None

        # A UDP datagram may contain multiple concatenated KCP segments.
        segments = parse_kcp_segments(raw)
        if not segments:
            return None

        primary_result = None
        control_result = None
        fragment_result = None
        for seg in segments:
            result = self._process_kcp_segment(seg)
            if not result:
                continue
            kind = result[0]
            if kind in ("data", "handshake"):
                if primary_result is None:
                    primary_result = result
                elif kind == "data":
                    self.recv_queue.append(result[1])
            elif kind == "fragment":
                fragment_result = result
            elif control_result is None:
                control_result = result

        return primary_result or control_result or fragment_result
