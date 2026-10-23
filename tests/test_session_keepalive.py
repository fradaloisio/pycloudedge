"""Session keepalives of the vendor app (C93, C87).

traffic.pcap (app 6.2.8a10): while a live view is open the app sends a msgsvr
heartbeat (cmd 0xC9) every register-reply ``hb`` seconds, a routed webrtcsvr
hello about every 21 s and, once ICE nominated the direct camera address, a
bare 20-byte STUN Binding Indication to it 10 s later and then every 30 s.
A dormancy push the keepalives read once the camera streams leaves the
session alone (C95).
The production pipeline runs against a scripted signaling client, a scripted
TURN client and a simulated clock; ``keepalive=False`` or
``CLOUDEDGE_P2P_KEEPALIVE=0`` switch all of it off for live A/B tests.
"""
import collections
import socket
import struct
from types import SimpleNamespace

import pytest

from cloudedge import CloudEdgeClient
from cloudedge.p2p import p2p_streamer
from cloudedge.p2p.p2p_streamer import P2PStreamer

CAMERA = ("192.168.1.30", 40000)
HOST_KEY = "0123456789abcdef0123456789abcdef"
START = 1000.0


def push(sn, payload):
    # Independent KCP wire format, rather than the production encoder.
    return struct.pack("<IBBHIIII", 12, 81, 0, 1024, 0, sn, 0, len(payload)) + payload


def video_frame():
    frame = bytearray(68)
    frame[:4] = b"\x00\x00\x01\xfc"
    struct.pack_into("<I", frame, 56, 8)
    frame[60:] = b"video-au"
    return bytes(frame)


def is_binding_indication(data):
    return (
        len(data) == 20
        and struct.unpack_from(">HHI", data) == (0x0011, 0, 0x2112A442)
    )


class ScriptedSig:
    """msgsvr client double recording the keepalives it is asked to send."""

    def __init__(self, clock, fail_heartbeat=False):
        self.clock = clock
        self.fail_heartbeat = fail_heartbeat
        self.events = []
        self.pending_status = collections.deque()
        self.sock = SimpleNamespace(settimeout=lambda seconds: None)

    def register(self, **kwargs):
        return {"result": "OK", "uuid": "client", "token": "tok", "hb": 30, "ht": 3}

    def webrtc_hello_full(self):
        return {"cmd": "hello", "tag": "abcd"}

    def query_device_status(self, device_uuid):
        return {"status": "online", "contact": {}, "nat": {}}

    def request_coturn(self, device_uuid):
        return {
            "coturn_ip": "198.51.100.1",
            "coturn_port": 9100,
            "username": "user",
            "pwd": "password",
        }

    def send_offer(self, device_uuid, sdp):
        return {"sdp": (
            "a=ice-ufrag:cam\na=ice-pwd:secret\nm=audio 5555 RTP / AVP 0\n"
            "c=IN IP4 198.51.100.9\n"
            "a=candidate:H 1 UDP 1694498815 192.168.1.30 40000 typ host\n"
        )}

    def _recv_webrtc_content(self):
        raise socket.timeout()

    def send_candidate_complete(self, device_uuid):
        return {}

    def send_heartbeat(self, runtime=0):
        if self.fail_heartbeat:
            raise BrokenPipeError("msgsvr went away")
        self.events.append((self.clock[0], "heartbeat", int(runtime)))

    def send_hello(self):
        self.events.append((self.clock[0], "hello", None))

    def drain_frames(self, max_frames=64):
        self.events.append((self.clock[0], "drain", None))
        return 0

    def times(self, kind):
        return [at for at, event, _ in self.events if event == kind]


class ScriptedTurn:
    server_ip = "198.51.100.1"
    server_port = 9100
    relay_ip = "198.51.100.5"
    relay_port = 9998
    peer_local_port = 41000
    peer_mapped_ip = peer_mapped_port = None

    def __init__(self, clock):
        self.clock = clock
        self.sent = []
        self.channels = {}
        self.reverse_channels = {}
        self.peer_sock = SimpleNamespace(
            sendto=lambda data, addr: self.sent.append((self.clock[0], addr, data))
        )

    def connect(self):
        pass

    def allocate(self):
        return True

    def peer_stun_binding(self):
        return True

    def close(self):
        pass

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
        self.sent.append((self.clock[0], (ip, port), data))

    def indications(self):
        return [(at, addr) for at, addr, data in self.sent if is_binding_indication(data)]


@pytest.fixture
def session(monkeypatch, tmp_path):
    clock = [START]
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: clock[0], monotonic=lambda: clock[0], sleep=lambda s: None,
    ))
    monkeypatch.setattr(p2p_streamer, "_get_local_ips", lambda: ["192.168.1.2"])
    monkeypatch.delenv("CLOUDEDGE_P2P_KEEPALIVE", raising=False)
    turns = []

    def turn_factory(*args):
        turns.append(ScriptedTurn(clock))
        return turns[-1]

    monkeypatch.setattr(p2p_streamer, "TurnClient", turn_factory)

    def run(duration=100.0, sig=None, via_client=False, **streamer_kwargs):
        sig = sig or ScriptedSig(clock)
        frames = []
        device = {"serial_number": "ppsl0123456789", "host_key": HOST_KEY}
        if via_client:
            # The factory integrations call (Home Assistant among them).
            api = CloudEdgeClient(
                "user@example.test", "password", "IT", "+39",
                session_cache_file=str(tmp_path / "session.json"),
            )
            api.session_data = {"userID": "1"}
            streamer = api.create_streamer(
                device, on_video=frames.append, **streamer_kwargs
            )
        else:
            streamer = P2PStreamer(
                SimpleNamespace(session_data={"userID": "1"}, country_code="IT"),
                device,
                on_video=frames.append,
                **streamer_kwargs,
            )
        streamer._running = True
        tick = [0]

        def receive_batch(turn_arg, timeout, limit=2000):
            # The camera answers the login, then streams on the host candidate.
            clock[0] += 0.1
            tick[0] += 1
            if clock[0] - START >= duration:
                streamer.request_stop()
            payload = b"login-accepted" if tick[0] == 1 else video_frame()
            return [(push(tick[0] - 1, payload), CAMERA, False)]

        monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", receive_batch)
        counts = streamer._do_stream(sig)
        return SimpleNamespace(sig=sig, turn=turns[-1], frames=frames, counts=counts)

    return SimpleNamespace(run=run, clock=clock)


def test_streaming_session_sends_the_app_msgsvr_keepalives(session):
    result = session.run(duration=100.0)

    heartbeats = [
        (round(at - START), runtime)
        for at, event, runtime in result.sig.events
        if event == "heartbeat"
    ]
    assert heartbeats == [(30, 30), (60, 60), (90, 90)]
    assert [round(at - START) for at in result.sig.times("hello")] == [21, 42, 63, 84]
    # Replies are read (and status pushes queued) without blocking, ~1 Hz.
    assert len(result.sig.times("drain")) >= 90
    assert len(result.frames) > 900, "keepalives must not disturb the media loop"


def test_nominated_direct_peer_gets_binding_indications(session):
    result = session.run(duration=100.0)

    indications = result.turn.indications()
    assert [addr for _, addr in indications] == [CAMERA] * 3
    # Nominated by the first PUSH at ~0.1 s: first indication 10 s later,
    # then every 30 s, as the app does.
    assert [round(at - START) for at, _ in indications] == [10, 40, 70]


@pytest.mark.parametrize(
    "kwargs,env",
    [({"keepalive": False}, None), ({}, "0"), ({}, "off")],
    ids=["kwarg-false", "env-0", "env-off"],
)
def test_keepalives_can_be_switched_off_for_ab_tests(session, monkeypatch, kwargs, env):
    if env is not None:
        monkeypatch.setenv("CLOUDEDGE_P2P_KEEPALIVE", env)

    result = session.run(duration=100.0, **kwargs)

    assert result.sig.events == []
    assert result.turn.indications() == []
    assert len(result.frames) > 900


def test_explicit_keepalive_wins_over_the_environment(session, monkeypatch):
    monkeypatch.setenv("CLOUDEDGE_P2P_KEEPALIVE", "0")

    result = session.run(duration=40.0, keepalive=True)

    assert result.sig.times("heartbeat")
    assert result.turn.indications()


@pytest.mark.parametrize(
    "kwargs,env,expected",
    [({}, None, True), ({}, "0", False), ({"keepalive": False}, None, False)],
    ids=["default-on", "env-off", "kwarg-off"],
)
def test_client_factory_passes_the_keepalive_switch_through(
    session, monkeypatch, kwargs, env, expected
):
    # create_streamer() takes the same switch with the same default: on
    # unless the caller or CLOUDEDGE_P2P_KEEPALIVE turns it off.
    if env is not None:
        monkeypatch.setenv("CLOUDEDGE_P2P_KEEPALIVE", env)

    result = session.run(duration=40.0, via_client=True, **kwargs)

    assert bool(result.sig.times("heartbeat")) is expected
    assert bool(result.turn.indications()) is expected


def test_msgsvr_failure_stops_signaling_keepalives_not_the_media(session):
    sig = ScriptedSig(session.clock, fail_heartbeat=True)

    result = session.run(duration=100.0, sig=sig)

    # The failed heartbeat at 30 s ends signaling keepalives for the session
    # (no hello after it), while video and ICE consent continue.
    assert [round(at - START) for at in result.sig.times("hello")] == [21]
    assert len(result.frames) > 900
    assert len(result.turn.indications()) == 3


class DormancyPushAfterLogin(ScriptedSig):
    """msgsvr pushes the streamed camera's dormancy 5 s into the session."""

    def __init__(self, clock):
        super().__init__(clock)
        self.camera = None
        self.pushed_at = None

    def send_offer(self, device_uuid, sdp):
        self.camera = device_uuid
        return super().send_offer(device_uuid, sdp)

    def drain_frames(self, max_frames=64):
        if self.pushed_at is None and self.clock[0] - START >= 5.0:
            self.pushed_at = self.clock[0]
            self.pending_status.append(
                {"action": "status", "uuid": self.camera, "status": "dormancy"}
            )
        return super().drain_frames(max_frames)


def test_dormancy_push_after_the_login_leaves_the_session_streaming(session):
    # C95 ends an attempt on the camera's dormancy push only before the VVP
    # login; once video flows, the stall timers decide.  Ending here would
    # also report (0, 0), and run_session would log "produced no stream" and
    # wake the camera again.
    sig = DormancyPushAfterLogin(session.clock)

    result = session.run(duration=20.0, sig=sig)

    assert sig.pushed_at is not None, "the push must land while the session streams"
    # Login at ~0.1 s, then a frame every 0.1 s: ~50 frames before the push.
    assert len(result.frames) > 150, "video must keep flowing after the push"
    video_count, total_bytes = result.counts
    assert video_count == len(result.frames) and total_bytes > 0, (
        f"the session reported {result.counts} for {len(result.frames)} frames"
    )


def _run_continuation(monkeypatch, confirmed_peer, duration=70.0):
    """Run _receive_stream alone with session keepalives due from START."""
    from cloudedge.p2p.kcp_tunnel import KcpTunnel
    from cloudedge.p2p.p2p_streamer import _SessionKeepalive

    clock = [START]
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: clock[0], monotonic=lambda: clock[0], sleep=lambda s: None,
    ))
    streamer = P2PStreamer(
        SimpleNamespace(session_data={"userID": "1"}, country_code="IT"),
        {"serial_number": "ppsl0123456789", "host_key": HOST_KEY},
    )
    streamer._running = True
    sig = ScriptedSig(clock)
    turn = ScriptedTurn(clock)
    sn = [0]

    def receive_batch(turn_arg, timeout, limit=2000):
        clock[0] += 0.1
        sn[0] += 1
        if clock[0] - START >= duration:
            streamer.request_stop()
        return [(push(sn[0], video_frame()), CAMERA, False)]

    monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", receive_batch)
    kcp = KcpTunnel(lambda data: turn.sent.append((clock[0], CAMERA, data)))
    kcp.next_recv_sn = 1
    streamer._receive_stream(
        turn, kcp, "", HOST_KEY, None, 1, 1, 1, 0, START,
        confirmed_peer=confirmed_peer,
        keepalive=_SessionKeepalive(sig, registered_at=START, heartbeat_interval=30),
    )
    return sig, turn


def test_continuation_receiver_keeps_sending_the_keepalives(monkeypatch):
    sig, turn = _run_continuation(monkeypatch, (CAMERA[0], CAMERA[1], True))

    assert [round(at - START) for at in sig.times("heartbeat")] == [30, 60]
    assert [round(at - START) for at in sig.times("hello")] == [21, 42, 63]
    assert [round(at - START) for at, _ in turn.indications()] == [10, 40]


def test_relayed_media_gets_no_binding_indications(monkeypatch):
    # The app's indications go to the nominated direct address; a relayed
    # path is kept alive by the TURN allocation instead.
    sig, turn = _run_continuation(monkeypatch, ("198.51.100.9", 5555, False))

    assert turn.indications() == []
    assert [round(at - START) for at in sig.times("heartbeat")] == [30, 60]
