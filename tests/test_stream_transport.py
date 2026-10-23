"""Streaming transport contracts under loss, reordering and relayed control I/O.

Use production KCP and receiver loops with scripted datagrams and a monotonic
clock. Frames exercise VVP delivery; codec decoding belongs to the HA bridge.
"""
import collections
import socket
import struct
from types import SimpleNamespace

import pytest

from cloudedge.p2p import p2p_streamer, turn_client
from cloudedge.p2p.kcp_tunnel import KcpTunnel, parse_kcp_segments
from cloudedge.p2p.p2p_streamer import P2PStreamer


def push(sn, payload, *, frg=0, cmd=81, ts=0):
    # Independent KCP wire format, rather than the production encoder.
    header = struct.pack("<IBBHIIII", 12, cmd, frg, 1024, ts, sn, 0, len(payload))
    return header + payload


def video_frame():
    frame = bytearray(68)
    frame[:4] = b"\x00\x00\x01\xfc"
    struct.pack_into("<I", frame, 56, 8)
    frame[60:] = b"video-au"
    return bytes(frame)


class ScriptedMediaTurn:
    server_ip = "198.51.100.1"
    server_port = 9100
    peer_local_port = 9999
    peer_mapped_ip = peer_mapped_port = None
    relay_ip = "198.51.100.5"
    relay_port = 9998

    def __init__(self):
        self.sent = []
        self.channels = {}
        self.reverse_channels = {}
        self.peer_sock = SimpleNamespace(
            sendto=lambda data, addr: self.sent.append(data)
        )

    def drain_socket(self):
        pass
    def create_permission(self, *args):
        return True
    def channel_bind(self, *args):
        return 0x4000
    def refresh(self):
        return 600
    def renew_due(self):
        return 60
    def send_ice_binding(self, *args):
        pass
    def send_to_peer(self, ip, port, data):
        self.sent.append(data)


class ScriptedSignaling:
    sock = SimpleNamespace(settimeout=lambda seconds: None)

    def send_offer(self, device_uuid, sdp):
        return {"sdp": (
            "a=ice-ufrag:cam\na=ice-pwd:secret\nm=audio 5555 RTP / AVP 0\n"
            "c=IN IP4 203.0.113.10\n"
            "a=candidate:r 1 UDP 1 203.0.113.10 5555 typ relay\n"
        )}

    def _recv_webrtc_content(self):
        raise socket.timeout()

    def send_candidate_complete(self, device_uuid):
        return {}


@pytest.fixture
def media_session(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0], sleep=lambda seconds: None,
    ))
    monkeypatch.setattr(p2p_streamer, "_get_local_ips", lambda: ["192.168.1.2"])
    frames = []
    delivered = []
    api = SimpleNamespace(session_data={"userID": "123"}, country_code="IT")
    streamer = P2PStreamer(api, {
        "serial_number": "ppsl0123456789",
        "host_key": "0123456789abcdef0123456789abcdef",
    }, on_video=frames.append)
    streamer._running = True
    turn = ScriptedMediaTurn()

    def run(mode, *, continuation=False, duration=40.0, relay_format=None):
        tick = [0]
        acknowledged = set()
        # A packet every 100 ms always arrives before the 500 ms select timeout.
        def receive_batch(turn_arg, timeout, limit=2000):
            now[0] += 0.1
            tick[0] += 1
            if now[0] >= 100 + duration:
                streamer.request_stop()
            if not continuation and tick[0] == 1:
                datagram = push(0, b"login-accepted")
            elif mode == "gap":
                # ikcp stamps every transmission with the camera's own clock.
                datagram = push(3 + tick[0] % 10, video_frame(), ts=2337 + 100 * tick[0])
            elif mode == "no-video":
                datagram = push(tick[0] if continuation else tick[0] - 1, b"status-only")
            else:
                sn = tick[0] - 1
                datagram = push(sn, video_frame())
                if mode == "compound":
                    datagram = push(0, b"", cmd=82) + datagram
            if relay_format:
                # Android relay capture: one extra byte after the complete KCP
                # datagram, inside TURN's declared data length. ACKs can have a
                # nonzero byte, so this is not zero padding or a KCP segment.
                datagram += b"\xff" if tick[0] % 2 else b"\x00"
                if relay_format == "channel":
                    datagram = struct.pack(">HH", 0x4000, len(datagram)) + datagram
                    datagram += b"\x00" * (-len(datagram) % 4)
                else:
                    datagram = turn_client._build_stun(
                        turn_client.DATA_INDICATION,
                        turn_client._encode_attr(
                            turn_client.ATTR_XOR_PEER_ADDRESS,
                            turn_client._encode_xor_address("203.0.113.10", 5555),
                        ) + turn_client._encode_attr(turn_client.ATTR_DATA, datagram),
                    )[0]
                packets = [(datagram, (turn.server_ip, turn.server_port), True)]
            else:
                packets = [(datagram, ("203.0.113.10", 5555), False)]
            if mode == "loss":
                for sent in turn.sent:
                    for segment in parse_kcp_segments(sent) or []:
                        sn = segment["sn"]
                        if (
                            segment["cmd"] == 81
                            and sn != 2
                            and sn not in acknowledged
                        ):
                            acknowledged.add(sn)
                            packets.append((
                                push(sn, b"", cmd=82), ("203.0.113.10", 5555), False,
                            ))
            delivered.extend(packet for packet, _, _ in packets)
            return packets
        monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", receive_batch)
        if continuation:
            kcp = KcpTunnel(lambda data: turn.sent.append(data))
            # The continuation inherits an accepted login and an outbound
            # heartbeat whose acknowledgement was lost.
            for _ in range(3):
                kcp.send_iva_data(b"heartbeat")
            streamer._receive_stream(turn, kcp, "", "", None, 1, 0, 0, 0, 100.0)
        else:
            streamer._stream_with_turn(
                ScriptedSignaling(), turn, "device-uuid", streamer._host_key,
                streamer._sn_num, {}, turn.server_ip, False,
            )
        return now[0] - 100.0

    return SimpleNamespace(
        run=run, streamer=streamer, turn=turn, frames=frames, delivered=delivered,
    )


def test_gap_before_first_video_recovers_without_a_previous_frame(media_session):
    media_session.run("gap", duration=15)
    assert media_session.frames, "A persistent initial gap must not prevent recovery forever"


@pytest.mark.parametrize("continuation", [False, True])
def test_first_video_deadline_is_not_extended_by_non_video_packets(media_session, continuation):
    elapsed = media_session.run("no-video", continuation=continuation, duration=80)
    # The 30 s first-video timeout, not the 35 s stall limit (C72).
    assert 30 <= elapsed < 31, "Non-video traffic must not keep an empty stream alive"
    assert media_session.frames == []


@pytest.mark.parametrize("continuation", [False, True])
def test_lost_outbound_segment_retries_during_continuous_media(media_session, continuation):
    media_session.run("loss", continuation=continuation, duration=12)
    sends = collections.Counter()
    for packet in media_session.turn.sent:
        for segment in parse_kcp_segments(packet) or []:
            if segment["cmd"] == 81:
                sends[segment["sn"]] += 1
    assert sends[2] >= 2, "A lost heartbeat must retry even while inbound video continues"
    assert media_session.frames


@pytest.mark.parametrize("continuation", [False, True])
def test_compound_ack_and_push_keep_the_video_session_alive(media_session, continuation):
    elapsed = media_session.run("compound", continuation=continuation, duration=50)
    assert elapsed >= 49, "Delivered PUSH segments after an ACK must count as KCP activity"
    assert len(media_session.frames) > 400


@pytest.mark.parametrize("consumer", ["batch", "public"])
@pytest.mark.parametrize("channel_count", [1, 80])
def test_turn_control_preserves_interleaved_media(monkeypatch, consumer, channel_count):
    peer = ("203.0.113.10", 5555)
    kcp_packet = push(0, b"media-payload")
    channel = struct.pack(">HH", 0x4000, len(kcp_packet)) + kcp_packet
    data_indication = turn_client._build_stun(
        turn_client.DATA_INDICATION,
        turn_client._encode_attr(
            turn_client.ATTR_XOR_PEER_ADDRESS,
            turn_client._encode_xor_address(*peer),
        )
        + turn_client._encode_attr(turn_client.ATTR_DATA, kcp_packet),
    )[0]
    pending = collections.deque()
    class ControlSocket:
        timeout = 5.0
        def settimeout(self, seconds):
            self.timeout = seconds
        def gettimeout(self):
            return self.timeout
        def setblocking(self, value):
            pass
        def sendto(self, packet, address):
            response = turn_client._build_stun(
                turn_client.REFRESH_REQUEST | 0x0100,
                turn_client._encode_attr(
                    turn_client.ATTR_LIFETIME, struct.pack(">I", 600)
                ),
                packet[8:20],
            )[0]
            pending.extend([channel] * channel_count + [data_indication, response, channel])
        def recvfrom(self, size):
            if not pending:
                raise socket.timeout()
            return pending.popleft(), ("198.51.100.1", 9100)
    turn = turn_client.TurnClient("198.51.100.1", 9100, "user", "password")
    turn.sock = ControlSocket()
    turn.reverse_channels[0x4000] = peer
    assert turn.refresh() == 600
    if consumer == "batch":
        monkeypatch.setattr(
            p2p_streamer.select, "select", lambda *args: ([turn.sock], [], [])
        )
        packets = []
        while len(packets) < channel_count + 2:
            batch = p2p_streamer._recv_udp_batch(turn, 0, limit=2)
            assert 0 < len(batch) <= 2
            packets.extend(batch)
        expected = [channel] * channel_count + [data_indication, channel]
        assert [packet[0] for packet in packets] == expected
        assert all(
            packet[1:] == (("198.51.100.1", 9100), True) for packet in packets
        )
    else:
        for _ in range(channel_count + 2):
            assert turn.recv_data() == (kcp_packet, *peer)


def test_turn_control_yields_a_large_burst_without_losing_consumed_media():
    # Legal large UDP datagrams make the queued receive bytes exceed 4 MiB.
    # The control transaction must yield to its consumer, preserving the
    # prefix it consumed and leaving the rest available on the socket.
    payload = push(0, b"media" * 12800)
    channel = struct.pack(">HH", 0x4000, len(payload)) + payload
    pending = collections.deque()
    sends = [0]

    class ControlSocket:
        def settimeout(self, seconds):
            pass

        def sendto(self, packet, address):
            sends[0] += 1
            if sends[0] == 1:
                pending.extend([channel] * 80)
            response = turn_client._build_stun(
                turn_client.REFRESH_REQUEST | 0x0100,
                turn_client._encode_attr(
                    turn_client.ATTR_LIFETIME, struct.pack(">I", 600)
                ),
                packet[8:20],
            )[0]
            pending.append(response)

        def recvfrom(self, size):
            if not pending:
                raise socket.timeout()
            return pending.popleft(), ("198.51.100.1", 9100)

    turn = turn_client.TurnClient(
        "198.51.100.1", 9100, "user", "password", clock=lambda: 100.0
    )
    turn.sock = ControlSocket()
    assert turn.refresh() is None
    received = turn.drain_pending_datagrams()
    assert 0 < len(received) < 80
    assert turn.refresh() == 600
    received.extend(turn.drain_pending_datagrams())
    assert [packet for packet, _ in received] == [channel] * 80


@pytest.mark.parametrize("continuation", [False, True])
def test_acks_during_a_receive_gap_only_echo_received_push_timestamps(
    media_session, monkeypatch, continuation
):
    # The camera's stock ikcp derives an RTT sample (current - ts) from every
    # ACK segment. The Android client only ever echoes the ts of the PUSH it
    # acknowledges; any other ts (e.g. the receiver's own clock) inflates the
    # camera's RTO and delays the retransmission of the very hole we wait for.
    from cloudedge.p2p import kcp_tunnel

    monkeypatch.setattr(kcp_tunnel, "time", SimpleNamespace(
        time=lambda: p2p_streamer.time.time(),
    ))
    media_session.run("gap", continuation=continuation, duration=15)

    pushed = collections.defaultdict(list)
    for packet in media_session.delivered:
        for segment in parse_kcp_segments(packet) or []:
            if segment["cmd"] == 81:
                pushed[segment["sn"]].append(segment["ts"])
    acks = [
        (segment["sn"], segment["ts"])
        for packet in media_session.turn.sent
        for segment in parse_kcp_segments(packet) or []
        if segment["cmd"] == 82
    ]
    assert acks, "the receiver must acknowledge the PUSH segments it got"
    assert all(ts in pushed[sn] for sn, ts in acks), "ACK ts must echo that PUSH"
    per_sn = collections.Counter(sn for sn, _ in acks)
    assert all(per_sn[sn] <= len(pushed[sn]) for sn in per_sn), "no unsolicited ACKs"
    assert media_session.frames, "the 5 s gap skip must still recover delivery"


# The whole TURN control transaction is bounded to 3 s (C82), counted on the
# injected clock, however much unrelated media keeps arriving meanwhile.


def test_turn_transaction_gives_up_after_3s_of_unrelated_media():
    now = [100.0]
    channel = struct.pack(">HH", 0x4000, 4) + b"KCP!"

    class MediaFlood:
        def settimeout(self, seconds):
            pass

        def sendto(self, packet, address):
            pass

        def recvfrom(self, size):
            now[0] += 0.25
            if now[0] > 110.0:
                raise AssertionError("the transaction never gave up")
            return channel, ("198.51.100.1", 9100)

    turn = turn_client.TurnClient(
        "198.51.100.1", 9100, "user", "password", clock=lambda: now[0]
    )
    turn.sock = MediaFlood()

    assert turn.refresh() is None
    assert now[0] - 100.0 == pytest.approx(3.0)
    assert [packet for packet, _ in turn.drain_pending_datagrams()] == [channel] * 12


def test_turn_transaction_waits_only_for_the_time_left():
    now = [100.0]
    timeouts = []

    class OneStrayDatagram:
        def settimeout(self, seconds):
            timeouts.append(seconds)

        def sendto(self, packet, address):
            pass

        def recvfrom(self, size):
            if len(timeouts) == 1:
                now[0] += 1.0
                return struct.pack(">HH", 0x4000, 0), ("198.51.100.1", 9100)
            now[0] += timeouts[-1]
            raise socket.timeout()

    turn = turn_client.TurnClient(
        "198.51.100.1", 9100, "user", "password", clock=lambda: now[0]
    )
    turn.sock = OneStrayDatagram()

    assert turn.refresh() is None
    assert timeouts == pytest.approx([3.0, 2.0])
    assert now[0] - 100.0 == pytest.approx(3.0)


@pytest.mark.parametrize("relay_format", ["channel", "indication"])
@pytest.mark.parametrize("continuation", [False, True])
def test_relay_kcp_trailer_does_not_block_video(media_session, relay_format, continuation):
    media_session.run(
        "compound", relay_format=relay_format, continuation=continuation, duration=1,
    )
    assert len(media_session.frames) >= 8
    assert all(frame == b"video-au" for frame in media_session.frames)
    if not continuation:
        # The bootstrap and later ACKs must include the byte too. This is an
        # independent wire-length contract from the app capture, not the
        # production parser accepting its own output.
        for packet in media_session.turn.sent:
            assert packet[-1:] == b"\x00"
            offset = 0
            while offset + 24 <= len(packet) - 1:
                size = struct.unpack_from("<I", packet, offset + 20)[0]
                offset += 24 + size
            assert offset == len(packet) - 1
