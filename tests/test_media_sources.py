"""P2P media and ICE datagrams are accepted only from expected sources (C80).

Both P2P sockets listen on every interface.  The connection and continuation
loops must take KCP and ICE only from the TURN server, the camera's
advertised candidates (any port of a host/srflx candidate IP, by explicit
rule) and peers that proved they are the ICE peer; the TURN socket only from
the TURN server.  Production loops run against scripted datagrams on a
simulated clock; STUN messages are built independently of the production
encoder.
"""
import collections
import hashlib
import hmac
import logging
import os
import re
import socket
import struct
from types import SimpleNamespace

import pytest

from cloudedge.p2p import p2p_streamer, turn_client
from cloudedge.p2p.kcp_tunnel import parse_kcp_segments
from cloudedge.p2p.p2p_streamer import P2PStreamer

HOST_KEY = "0123456789abcdef0123456789abcdef"
CAMERA_HOST = ("192.168.1.30", 40000)
CAMERA_RELAY = ("198.51.100.9", 5555)
ATTACKER = ("192.168.1.66", 40000)
ANSWER = (
    "a=ice-ufrag:cam\na=ice-pwd:campwd\nm=audio 5555 RTP / AVP 0\n"
    "c=IN IP4 198.51.100.9\n"
    "a=candidate:H 1 UDP 1694498815 192.168.1.30 40000 typ host\n"
    "a=candidate:S 1 UDP 1862270975 203.0.113.30 40000 typ srflx\n"
)
MAGIC = 0x2112A442


def push(sn, payload):
    return struct.pack("<IBBHIIII", 12, 81, 0, 1024, 0, sn, 0, len(payload)) + payload


def video_frame(body=b"video-au"):
    frame = bytearray(60 + len(body))
    frame[:4] = b"\x00\x00\x01\xfc"
    struct.pack_into("<I", frame, 56, len(body))
    frame[60:] = body
    return bytes(frame)


def stun(msg_type, txn_id, attrs=b""):
    return struct.pack(">HHI", msg_type, len(attrs), MAGIC) + txn_id + attrs


def ice_check(remote_ufrag, key, txn_id=b"camera-check"):
    """A camera ICE check: USERNAME + MESSAGE-INTEGRITY keyed with ``key``."""
    username = f"{remote_ufrag}:cam".encode()
    attrs = struct.pack(">HH", 0x0006, len(username)) + username
    attrs += b"\x00" * (-len(username) % 4)
    header = struct.pack(">HHI", 0x0001, len(attrs) + 24, MAGIC) + txn_id
    mac = hmac.new(key, header + attrs, hashlib.sha1).digest()
    return header + attrs + struct.pack(">HH", 0x0008, 20) + mac


class ScriptedSig:
    sock = SimpleNamespace(settimeout=lambda seconds: None)
    pending_status = ()

    def __init__(self):
        self.offer = ""

    def send_offer(self, device_uuid, sdp):
        self.offer = sdp
        return {"sdp": ANSWER}

    def _recv_webrtc_content(self):
        raise socket.timeout()

    def send_candidate_complete(self, device_uuid):
        return {}

    def our_ice_credentials(self):
        ufrag = re.search(r"a=ice-ufrag:(\S+)", self.offer).group(1)
        pwd = re.search(r"a=ice-pwd:(\S+)", self.offer).group(1)
        return ufrag, pwd


class ScriptedTurn:
    server_ip = "198.51.100.1"
    server_port = 9100
    relay_ip = "198.51.100.5"
    relay_port = 9998
    peer_local_port = 41000
    peer_mapped_ip = peer_mapped_port = None

    def __init__(self, now):
        self.now = now
        self.sent = []  # (time, "direct" | "relay", address, datagram)
        self.channels = {}
        self.reverse_channels = {}
        self.peer_sock = SimpleNamespace(
            sendto=lambda data, addr: self.sent.append(
                (self.now[0], "direct", tuple(addr), data)
            )
        )

    def drain_socket(self):
        pass

    def create_permission(self, *args, **kwargs):
        return True

    def channel_bind(self, *args, **kwargs):
        return 0x4000

    def refresh(self, *args):
        return 600

    def renew_due(self):
        return 60

    def send_ice_binding(self, *args, **kwargs):
        pass

    def send_to_peer(self, ip, port, data):
        self.sent.append((self.now[0], "relay", (ip, port), data))

    def destinations(self):
        return {address for _, _, address, _ in self.sent}

    def kcp_pushes(self, path, address):
        return [
            at
            for at, sent_path, sent_to, data in self.sent
            if sent_path == path
            and sent_to == address
            and any(seg["cmd"] == 81 for seg in parse_kcp_segments(data[:-1] if path == "relay" else data) or [])
        ]

    def direct_check_txns(self):
        return [
            data[8:20]
            for _, path, _, data in self.sent
            if path == "direct" and data[:2] == b"\x00\x01"
        ]


@pytest.fixture
def media(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0], sleep=lambda s: None,
    ))
    monkeypatch.setattr(p2p_streamer, "_get_local_ips", lambda: ["192.168.1.2"])
    frames = []
    streamer = P2PStreamer(
        SimpleNamespace(session_data={"userID": "1"}, country_code="IT"),
        {"serial_number": "ppsl0123456789", "host_key": HOST_KEY},
        on_video=frames.append,
    )
    streamer._running = True
    turn = ScriptedTurn(now)
    sig = ScriptedSig()

    def run(script, *, duration=20.0, dev_nat=None):
        tick = [0]

        def receive_batch(turn_arg, timeout, limit=2000):
            tick[0] += 1
            batch = script(tick[0], sig) or []
            now[0] += 0.1 if batch else timeout
            if now[0] - 100.0 >= duration:
                streamer.request_stop()
            return batch

        monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", receive_batch)
        streamer._stream_with_turn(
            sig, turn, "device-uuid", HOST_KEY, "ppsl0123456789", dev_nat or {},
            turn.server_ip, False,
        )

    return SimpleNamespace(run=run, frames=frames, turn=turn, streamer=streamer)


def test_forged_media_from_a_foreign_host_is_dropped(media, caplog):
    def script(tick, sig):
        if tick == 1:
            return [(push(0, b"login-accepted"), CAMERA_HOST, False)]
        batch = [(push(tick - 1, video_frame()), CAMERA_HOST, False)]
        if tick == 5:
            # The attacker races the camera's next sequence number and tries
            # to nominate itself with an unsolicited Binding Response.
            batch.append((push(tick, video_frame(b"FORGED-VIDEO")), ATTACKER, False))
            batch.append((stun(0x0101, os.urandom(12)), ATTACKER, False))
        return batch

    with caplog.at_level(logging.DEBUG, logger="cloudedge.p2p.p2p_streamer"):
        media.run(script)

    assert len(media.frames) > 150
    assert not any(b"FORGED" in frame for frame in media.frames)
    assert ATTACKER not in media.turn.destinations(), "no KCP or ACK to the attacker"
    assert "unexpected source 192.168.1.66:40000" in caplog.text


def test_forged_media_is_dropped_after_the_hand_over_too(media, monkeypatch):
    phase = {"continuation": False}
    receive_stream = P2PStreamer._receive_stream

    def continuation(self, *args, **kwargs):
        phase["continuation"] = True
        return receive_stream(self, *args, **kwargs)

    monkeypatch.setattr(P2PStreamer, "_receive_stream", continuation)
    sn = [0]

    def script(tick, sig):
        if tick > 20 and not phase["continuation"]:
            return []  # the camera pauses until the continuation receiver runs
        payload = b"login-accepted" if sn[0] == 0 else video_frame()
        batch = [(push(sn[0], payload), CAMERA_HOST, False)]
        sn[0] += 1
        if phase["continuation"] and sn[0] == 25:
            batch.append((push(sn[0], video_frame(b"FORGED-VIDEO")), ATTACKER, False))
        return batch

    media.run(script, duration=45.0)

    assert phase["continuation"]
    assert len(media.frames) > 30
    assert not any(b"FORGED" in frame for frame in media.frames)
    assert ATTACKER not in media.turn.destinations()


@pytest.mark.parametrize(
    "camera_other_port",
    [(CAMERA_HOST[0], 40001), ("203.0.113.30", 61000), ("203.0.113.31", 62000)],
    ids=["host-ip", "srflx-ip", "msgsvr-wan-ip"],
)
def test_camera_media_from_another_port_of_its_candidate_ip_is_accepted(
    media, camera_other_port
):
    # Explicit rule: a camera behind a NAT (or sending from another socket)
    # may use a port that its host/srflx candidate does not name; its IP is
    # still one the camera advertised, or its WAN IP reported by msgsvr.
    def script(tick, sig):
        payload = b"login-accepted" if tick == 1 else video_frame()
        return [(push(tick - 1, payload), camera_other_port, False)]

    media.run(script, dev_nat={"wan_ip": "203.0.113.31"})

    assert len(media.frames) > 150
    assert media.turn.kcp_pushes("direct", camera_other_port), "KCP follows the camera"


def test_peer_reflexive_camera_is_trusted_after_an_authenticated_check(media):
    reflexive = ("198.51.100.77", 50000)
    impostor = ("198.51.100.78", 50000)

    def script(tick, sig):
        ufrag, pwd = sig.our_ice_credentials()
        if tick == 1:
            return [
                (ice_check(ufrag, pwd.encode()), reflexive, False),
                (ice_check(ufrag, b"not-our-ice-pwd"), impostor, False),
            ]
        payload = b"login-accepted" if tick == 2 else video_frame()
        batch = [(push(tick - 2, payload), reflexive, False)]
        if tick == 6:
            batch.append((push(tick - 1, video_frame(b"FORGED-VIDEO")), impostor, False))
        return batch

    media.run(script)

    assert len(media.frames) > 150
    assert not any(b"FORGED" in frame for frame in media.frames)
    assert reflexive in media.turn.destinations()
    assert impostor not in media.turn.destinations(), "no ICE answer nor KCP"


def test_only_an_answer_to_our_own_check_nominates_the_direct_path(media):
    def script(tick, sig):
        if tick == 1:
            return [(stun(0x0101, b"not-our-txn!"), CAMERA_HOST, False)]
        if tick == 20:
            ours = media.turn.direct_check_txns()[-1]
            return [(stun(0x0101, ours), CAMERA_HOST, False)]
        return []

    media.run(script, duration=15.0)

    direct = media.turn.kcp_pushes("direct", CAMERA_HOST)
    assert direct, "the answer to our check nominates the direct path"
    nominated_at = direct[0]
    # Until then the KCP bootstrap stays on the negotiated TURN candidate.
    assert media.turn.kcp_pushes("relay", CAMERA_RELAY)
    assert nominated_at > 100.0 + 5.0


# ----------------------------------------------------------------------
# C80 (a) - TURN socket in the streaming loops
# ----------------------------------------------------------------------


def _channel_data(channel, payload):
    return struct.pack(">HH", channel, len(payload)) + payload


def _data_indication(peer, data):
    """A TURN Data Indication: XOR-PEER-ADDRESS ``peer`` and DATA ``data``."""
    ip, port = peer
    xaddr = bytes(a ^ b for a, b in zip(socket.inet_aton(ip), struct.pack(">I", MAGIC)))
    attrs = struct.pack(">HHBBH", 0x0012, 8, 0, 1, port ^ (MAGIC >> 16)) + xaddr
    attrs += struct.pack(">HH", 0x0013, len(data)) + data + b"\x00" * (-len(data) % 4)
    return stun(0x0017, b"forged-indic", attrs)


def test_forged_channel_data_on_the_turn_socket_is_dropped(media, caplog):
    # The camera streams on its host candidate; a LAN host sends ChannelData
    # for the camera's relay channel to the TURN socket (bound to every
    # interface too) and races the camera's next sequence number.
    def script(tick, sig):
        if tick == 1:
            return [(push(0, b"login-accepted"), CAMERA_HOST, False)]
        batch = [(push(tick - 1, video_frame()), CAMERA_HOST, False)]
        if tick == 5:
            forged = push(tick, video_frame(b"FORGED-VIDEO"))
            batch.append((_channel_data(0x4000, forged), ATTACKER, True))
        return batch

    media.turn.reverse_channels[0x4000] = CAMERA_RELAY
    with caplog.at_level(logging.DEBUG, logger="cloudedge.p2p.p2p_streamer"):
        media.run(script)

    assert len(media.frames) > 150
    assert not any(b"FORGED" in frame for frame in media.frames)
    assert "unexpected source 192.168.1.66:40000" in caplog.text


def test_forged_channel_data_on_the_turn_socket_is_dropped_after_the_hand_over_too(
    media, monkeypatch
):
    phase = {"continuation": False}
    receive_stream = P2PStreamer._receive_stream

    def continuation(self, *args, **kwargs):
        phase["continuation"] = True
        return receive_stream(self, *args, **kwargs)

    monkeypatch.setattr(P2PStreamer, "_receive_stream", continuation)
    media.turn.reverse_channels[0x4000] = CAMERA_RELAY
    sn = [0]

    def script(tick, sig):
        if tick > 20 and not phase["continuation"]:
            return []  # the camera pauses until the continuation receiver runs
        payload = b"login-accepted" if sn[0] == 0 else video_frame()
        batch = [(push(sn[0], payload), CAMERA_HOST, False)]
        sn[0] += 1
        if phase["continuation"] and sn[0] == 25:
            forged = push(sn[0], video_frame(b"FORGED-VIDEO"))
            batch.append((_channel_data(0x4000, forged), ATTACKER, True))
        return batch

    media.run(script, duration=45.0)

    assert phase["continuation"]
    assert len(media.frames) > 30
    assert not any(b"FORGED" in frame for frame in media.frames)


def test_a_forged_data_indication_cannot_redirect_the_bootstrap(media, caplog):
    # Before login, a Data Indication on the TURN socket carrying a Binding
    # Request would move the KCP bootstrap (IVA handshake + VVP START) to its
    # XOR-PEER-ADDRESS, an address of the sender's choice.
    redirect = ("198.51.100.66", 4444)

    def script(tick, sig):
        if tick == 1:
            forged = _data_indication(redirect, stun(0x0001, b"forged-check"))
            return [(forged, ATTACKER, True)]
        return []

    with caplog.at_level(logging.DEBUG, logger="cloudedge.p2p.p2p_streamer"):
        media.run(script, duration=8.0)

    assert redirect not in media.turn.destinations(), "no ICE answer nor KCP"
    # The bootstrap is still retried on the negotiated TURN candidate.
    assert [at for at in media.turn.kcp_pushes("relay", CAMERA_RELAY) if at > 100.0]
    assert "unexpected source 192.168.1.66:40000" in caplog.text


# ----------------------------------------------------------------------
# C80 (b) - TURN control transactions on the shared relay socket
# ----------------------------------------------------------------------

SERVER = ("198.51.100.1", 9100)


def _refresh_success(request):
    return turn_client._build_stun(
        turn_client.REFRESH_REQUEST | 0x0100,
        turn_client._encode_attr(turn_client.ATTR_LIFETIME, struct.pack(">I", 600)),
        request[8:20],
    )[0]


class _ControlSocket:
    """Relay socket double: each request releases the scripted datagrams."""

    def __init__(self, before_response):
        self.before_response = before_response
        self.pending = collections.deque()

    def settimeout(self, seconds):
        pass

    def sendto(self, request, address):
        self.pending.extend(self.before_response)
        self.pending.append((_refresh_success(request), SERVER))

    def recvfrom(self, size):
        if not self.pending:
            raise socket.timeout()
        return self.pending.popleft()


def test_turn_transaction_ignores_datagrams_from_other_hosts():
    forged = struct.pack(">HH", 0x4000, 4) + b"KCP!"
    turn = turn_client.TurnClient(*SERVER, "user", "password", clock=lambda: 100.0)
    turn.sock = _ControlSocket([(forged, ATTACKER)] * 3)
    turn.reverse_channels[0x4000] = CAMERA_RELAY

    assert turn.refresh() == 600
    assert turn.drain_pending_datagrams() == [], "nothing from the attacker is kept"


def test_turn_transaction_bounds_preserved_datagrams_by_count():
    # Empty datagrams never reach the byte cap; the entry count is bounded.
    turn = turn_client.TurnClient(*SERVER, "user", "password", clock=lambda: 100.0)
    turn.sock = _ControlSocket([(b"", SERVER)] * 5000)

    assert turn.refresh() is None, "the transaction yields to the media owner"
    assert len(turn.drain_pending_datagrams(limit=10000)) == 4096
