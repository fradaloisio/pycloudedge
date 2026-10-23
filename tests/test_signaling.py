"""msgsvr signaling: frames, routing and the dormancy race (C95, C99, C93).

The vendor app's msgsvr connection carries unsolicited status pushes (cmd
0xC7) interleaved with request replies, its frames end with an additive
checksum, and while streaming it sends a heartbeat and a routed hello whose
replies must be read without blocking.  A scripted TCP socket serves server
frames built from the documented wire format, independently of the
production encoder, and releases each reply only after the request it
answers has been sent.
"""
import base64
import json
import socket
from types import SimpleNamespace

import pytest
from Crypto.Cipher import DES3

from cloudedge.p2p import p2p_streamer
from cloudedge.p2p.meari_signaling import MsgSvrClient
from cloudedge.p2p.p2p_streamer import P2PStreamer

KEY = b"__#!HIRCloud5.0!#__" + b"\x00" * 5
DEVICE_UUID = "0000000123456789"
OTHER_CAMERA_UUID = "0000000999999999"
HOST_KEY = "0123456789abcdef0123456789abcdef"


def _encrypt(plain):
    pad = 8 - len(plain) % 8
    return DES3.new(KEY, DES3.MODE_ECB).encrypt(plain + bytes([pad]) * pad)


def _decrypt(data):
    plain = DES3.new(KEY, DES3.MODE_ECB).decrypt(data)
    return plain[: -plain[-1]]


def server_frame(cmd, obj, *, node=0xA4, method=0xB2):
    payload = _encrypt(json.dumps(obj, separators=(",", ":")).encode())
    length = len(payload)
    header = bytes(
        [0xE6, node, method, cmd, 0xD3, 0xE6, length & 0xFF, (length >> 8) | 0x80]
    )
    return header + payload + bytes([sum(payload) & 0xFF, 0x9D])


def webrtc_reply(inner):
    content = base64.b64encode(json.dumps(inner).encode()).decode()
    return server_frame(
        0xC6, {"from": {"node": "webrtcsvr"}, "content": content}, node=0xAD
    )


def status_push(status, uuid=DEVICE_UUID):
    return server_frame(
        0xC7, {"action": "status", "uuid": uuid, "status": status, "node": "dev"}
    )


def client_frames(data):
    """Split what the client sent into (header, payload, checksum, tail, json)."""
    frames = []
    pos = 0
    while pos < len(data):
        header = bytes(data[pos : pos + 8])
        length = header[6] | ((header[7] & 0x7F) << 8)
        payload = bytes(data[pos + 8 : pos + 8 + length])
        checksum, tail = data[pos + 8 + length], data[pos + 9 + length]
        frames.append((header, payload, checksum, tail, json.loads(_decrypt(payload))))
        pos += 8 + length + 2
    return frames


class ScriptedTcp:
    """msgsvr TCP double: replies[n] is released after the n-th client frame."""

    def __init__(self, replies=None, initial=b""):
        self.replies = dict(replies or {})
        self.inbound = bytearray(initial)
        self.sent = bytearray()
        self.sends = 0
        self.timeout = 10.0
        self.recv_timeouts = []

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return self.timeout

    def setblocking(self, flag):
        self.timeout = None if flag else 0.0

    def sendall(self, data):
        self.sent += data
        self.sends += 1
        self.inbound += b"".join(self.replies.pop(self.sends, []))

    def recv(self, size):
        self.recv_timeouts.append(self.timeout)
        if not self.inbound:
            if self.timeout == 0.0:
                raise BlockingIOError()
            raise socket.timeout("no scripted data")
        chunk = bytes(self.inbound[:size])
        del self.inbound[:size]
        return chunk

    def close(self):
        pass


def connected_client(replies=None, initial=b""):
    sig = MsgSvrClient("198.51.100.20", 31001)
    sig.sock = ScriptedTcp(replies, initial)
    sig.uuid = "client-uuid-0001"
    sig.token = "tok123"
    sig.webrtcsvr = {
        "ip": "198.51.100.21",
        "port": 18836,
        "domain": "webrtcsvr.eu",
        "tag": "abcd",
    }
    return sig


def test_frames_carry_the_additive_checksum_used_by_the_app():
    sig = connected_client()
    sig.send_logout(DEVICE_UUID)

    (header, payload, checksum, tail, body), = client_frames(sig.sock.sent)
    xor = 0
    for byte in payload:
        xor ^= byte
    assert xor != sum(payload) & 0xFF, "payload must tell the two checksums apart"
    assert checksum == sum(payload) & 0xFF
    assert tail == 0x9D
    assert body["content"]


def test_status_push_before_a_reply_is_queued_for_the_next_status_waiter():
    coturn = {
        "cmd": "option",
        "coturn_ip": "198.51.100.7",
        "coturn_port": 9100,
        "username": "user",
        "pwd": "password",
    }
    sig = connected_client({1: [status_push("online"), webrtc_reply(coturn)]})

    assert sig.request_coturn(DEVICE_UUID)["coturn_ip"] == "198.51.100.7"
    # The push that overtook the reply is not lost: the next status wait
    # consumes it without reading the (now empty) socket.
    online = sig.wait_for_status(DEVICE_UUID, "online", timeout=1)
    assert online is not None and online["status"] == "online"


class _RecordingTurn:
    server_ip = "198.51.100.1"
    server_port = 9100
    peer_local_port = 40000
    peer_mapped_ip = peer_mapped_port = None
    relay_ip = "198.51.100.5"
    relay_port = 9998

    def __init__(self):
        self.calls = []
        self.channels = {}
        self.reverse_channels = {}
        self.peer_sock = SimpleNamespace(
            sendto=lambda data, addr: self.calls.append(("direct", addr))
        )

    def drain_socket(self):
        pass

    def drain_pending_datagrams(self, limit=2000):
        return []

    def create_permission(self, ip, **kwargs):
        self.calls.append(("permission", ip))
        return True

    def channel_bind(self, ip, port, **kwargs):
        self.calls.append(("channel_bind", (ip, port)))
        return 0x4000

    def refresh(self, *args):
        self.calls.append(("refresh", None))
        return 600

    def renew_due(self):
        return 60

    def send_ice_binding(self, ip, port, *args, **kwargs):
        self.calls.append(("ice", (ip, port)))

    def send_to_peer(self, ip, port, data):
        self.calls.append(("relay", (ip, port)))


def _camera_answer():
    return webrtc_reply({"cmd": "answer", "sdp": (
        "a=ice-ufrag:cam\na=ice-pwd:secret\nm=audio 5555 RTP / AVP 0\n"
        "c=IN IP4 203.0.113.10\n"
        "a=candidate:H 1 UDP 1694498815 192.168.1.30 40000 typ host\n"
    )})


def _negotiating_streamer(monkeypatch, now):
    """A streamer about to send its offer, on the simulated clock ``now``."""
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0], sleep=lambda s: None,
    ))
    monkeypatch.setattr(p2p_streamer, "_get_local_ips", lambda: ["192.168.1.2"])
    streamer = P2PStreamer(
        SimpleNamespace(session_data={"userID": "1"}, country_code="IT"),
        {"serial_number": "ppsl0123456789", "host_key": HOST_KEY},
    )
    streamer._running = True
    return streamer


@pytest.mark.parametrize("statuses,dormant", [
    (["dormancy"], True),
    (["dormancy", "online"], False),
    (["online", "dormancy"], True),
])
def test_latest_status_during_negotiation_controls_whether_ice_starts(
    monkeypatch, statuses, dormant
):
    # App capture: a logout can still put a battery camera to sleep after it
    # answered the next offer.  The push must end the attempt, not be
    # swallowed while the session waits 30 s for a login that never comes.
    now = [100.0]
    streamer = _negotiating_streamer(monkeypatch, now)
    sig = connected_client({
        1: [_camera_answer(), *[status_push(status) for status in statuses]],
        2: [webrtc_reply({"cmd": "candidate"})],
    })

    def stop_on_first_poll(turn_arg, timeout, limit=2000):
        now[0] += timeout
        streamer.request_stop()
        return []

    monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", stop_on_first_poll)
    turn = _RecordingTurn()

    result = streamer._stream_with_turn(
        sig, turn, DEVICE_UUID, HOST_KEY, "ppsl0123456789", {}, turn.server_ip, False
    )

    assert result == (0, 0)
    assert bool(turn.calls) is not dormant, (
        "Only the latest camera status may stop TURN permissions and ICE checks"
    )


def _connect_to_a_silent_camera(monkeypatch, push):
    """Run an attempt to a camera that answers, then never sends a packet.

    msgsvr delivers the status frame ``push`` 4 s after the answer, while the
    session keepalives read msgsvr about once a second.  Times are seconds
    after the answer.
    """
    now = [100.0]
    streamer = _negotiating_streamer(monkeypatch, now)
    sig = connected_client({
        1: [_camera_answer()],
        2: [webrtc_reply({"cmd": "candidate"})],
    })
    answered_at = now[0]
    pushed_at = []

    def silent_camera(turn_arg, timeout, limit=2000):
        now[0] += timeout
        if not pushed_at and now[0] - answered_at >= 4.0:
            pushed_at.append(now[0] - answered_at)
            sig.sock.inbound += push
        return []

    monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", silent_camera)
    turn = _RecordingTurn()
    keepalive = p2p_streamer._SessionKeepalive(
        sig, registered_at=now[0], heartbeat_interval=30
    )

    result = streamer._stream_with_turn(
        sig, turn, DEVICE_UUID, HOST_KEY, "ppsl0123456789", {}, turn.server_ip,
        False, keepalive,
    )
    return SimpleNamespace(
        result=result,
        sig=sig,
        pushed_at=pushed_at[0] if pushed_at else None,
        ended_at=now[0] - answered_at,
    )


def test_dormancy_push_while_connecting_ends_the_attempt_before_the_ice_deadline(
    monkeypatch,
):
    # Dormancy follows the previous logout by 1.3-4.7 s, so the push can also
    # land after the trickle wait, while the camera stays silent.  The
    # session keepalives read msgsvr about once a second: the attempt must
    # end on the push instead of waiting out the 30 s ICE deadline.
    attempt = _connect_to_a_silent_camera(monkeypatch, status_push("dormancy"))

    assert attempt.result == (0, 0)
    assert attempt.pushed_at is not None, (
        "the push must arrive while the attempt is connecting"
    )
    # Reads run every second and polls every 0.5 s: 1.5 s at most.
    assert attempt.ended_at - attempt.pushed_at <= 1.5, (
        f"attempt ended {attempt.ended_at:.1f} s after the answer, "
        f"{attempt.ended_at - attempt.pushed_at:.1f} s after the dormancy push"
    )


@pytest.mark.parametrize(
    "status,uuid",
    [("dormancy", OTHER_CAMERA_UUID), ("online", DEVICE_UUID)],
    ids=["another-cameras-dormancy", "this-cameras-online"],
)
def test_only_this_cameras_dormancy_push_ends_the_attempt(monkeypatch, status, uuid):
    # Status pushes name their camera.  Any other push the keepalives read
    # while connecting leaves the attempt to its own outcome: here the
    # silent camera's 30 s ICE deadline.
    attempt = _connect_to_a_silent_camera(monkeypatch, status_push(status, uuid=uuid))

    queued = [(push["status"], push["uuid"]) for push in attempt.sig.pending_status]
    assert queued == [(status, uuid)], "the push must be read while connecting"
    assert attempt.ended_at >= 29.0, (
        f"attempt ended {attempt.ended_at:.1f} s after the answer, "
        f"{attempt.ended_at - attempt.pushed_at:.1f} s after the push"
    )


def test_wake_connect_addresses_the_camera_like_the_app():
    sig = connected_client()
    keepalive = {
        "node": "natsvr",
        "domain": "natsvr.eu",
        "transport": "tcp",
        "type": "binary",
        "ip": "198.51.100.30",
        "port": 18860,
    }

    # msgsvr may not answer before the socket timeout; that is not fatal.
    assert sig.send_wake_connect(DEVICE_UUID, keepalive, ["192.168.1.2"], 16685) is None

    (_, _, _, _, body), = client_frames(sig.sock.sent)
    assert body["contact"] == {
        "node": "dev",
        "domain": DEVICE_UUID,
        "transport": "tcp",
        "type": "binary",
        "ip": "198.51.100.30",
        "port": 18860,
    }


def test_signaling_wake_failure_still_sends_the_http_wake():
    class _Api:
        wake_calls = []

        def wake_device(self, serial_number, device_id):
            self.wake_calls.append(serial_number)

        def wait_for_online(self, *args, **kwargs):
            return True

    class _Sig:
        def send_wake_connect(self, *args):
            raise ConnectionResetError("msgsvr reset")

        def wait_for_status(self, *args, **kwargs):
            return None

        def query_device_status(self, device_uuid):
            return {"status": "online", "contact": {}, "nat": {}}

    api = _Api()
    streamer = P2PStreamer(api, {"serial_number": "ppsl0123456789", "host_key": HOST_KEY})
    status = streamer._wake_dormant_device(
        _Sig(), DEVICE_UUID, {"contact": {"keepalive": {"node": "natsvr"}}}
    )

    assert api.wake_calls == ["ppsl0123456789"]
    assert status["status"] == "online"


# ----------------------------------------------------------------------
# C93 - keepalive frames and non-blocking reads while streaming
# ----------------------------------------------------------------------


def test_heartbeat_and_hello_have_the_app_frame_shape():
    sig = connected_client()

    sig.send_heartbeat(runtime=30.7)
    sig.send_hello()

    (hb_header, _, _, _, heartbeat), (hello_header, _, _, _, hello) = client_frames(
        sig.sock.sent
    )
    # traffic.pcap: e6 a1 b1 c9 d3 e6 {uuid, token, ver, runtime} every 30 s
    # and a routed (b2) hello to webrtcsvr every ~21 s.
    assert hb_header[:6] == bytes.fromhex("e6a1b1c9d3e6")
    assert heartbeat == {"uuid": sig.uuid, "token": "tok123", "ver": 15340, "runtime": 30}
    assert hello_header[:6] == bytes.fromhex("e6a1b2c6d3e6")
    assert hello["to"]["node"] == "webrtcsvr"
    assert (hello["to"]["ip"], hello["to"]["port"]) == ("198.51.100.21", 18836)
    assert json.loads(base64.b64decode(hello["content"])) == {"cmd": "hello"}


def test_drain_frames_queues_status_pushes_without_blocking():
    heartbeat_reply = server_frame(0xC9, {"wan": "203.0.113.1", "ver": 17720})
    hello_reply = webrtc_reply({"cmd": "hello", "tag": "efgh"})
    late_reply = webrtc_reply({"cmd": "ack"})
    sig = connected_client(
        initial=heartbeat_reply + hello_reply + status_push("dormancy") + late_reply[:9]
    )
    sig.sock.settimeout(10.0)

    assert sig.drain_frames() == 3
    assert [push["status"] for push in sig.pending_status] == ["dormancy"]
    assert set(sig.sock.recv_timeouts) == {0.0}, "the media loop never blocks"
    assert sig.sock.gettimeout() == 10.0, "the blocking timeout is restored"
    # A frame cut by the read stays buffered and completes on the next read.
    sig.sock.inbound += late_reply[9:]
    assert sig._recv_webrtc_content() == {"cmd": "ack"}


def test_drain_frames_reports_a_closed_connection():
    sig = connected_client()

    class _Closed(ScriptedTcp):
        def recv(self, size):
            return b""

    sig.sock = _Closed()
    with pytest.raises(ConnectionError):
        sig.drain_frames()
