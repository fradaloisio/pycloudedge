"""Timers of the P2P streaming loops (C106, C75, C83, C93, C87, C81, C72).

The connection loop (_stream_with_turn) and the continuation receiver
(_receive_stream) run against a scripted camera on a simulated clock.  The
camera answers the VVP login, streams video frames, and then pauses, loses a
segment or goes silent; the tests observe what the loops send (VVP STOP and
START restarts, ICE checks, TURN renewals, session keepalives) and when the
session ends.
"""
import struct
from types import SimpleNamespace

import pytest

from cloudedge.p2p import p2p_streamer
from cloudedge.p2p.kcp_tunnel import KcpTunnel, parse_kcp_segments
from cloudedge.p2p.p2p_streamer import P2PStreamer, _SessionKeepalive

HOST_KEY = "0123456789abcdef0123456789abcdef"
CAMERA_HOST = ("192.168.1.30", 40000)
CAMERA_RELAY = ("198.51.100.9", 5555)
SERVER = ("198.51.100.1", 9100)
START = 100.0
VVP_STOP = 0x12FF
VVP_START = 0x11FF


def push(sn, payload):
    # Independent KCP wire format, rather than the production encoder.
    return struct.pack("<IBBHIIII", 12, 81, 0, 1024, 0, sn, 0, len(payload)) + payload


def video_frame():
    frame = bytearray(68)
    frame[:4] = b"\x00\x00\x01\xfc"
    struct.pack_into("<I", frame, 56, 8)
    frame[60:] = b"video-au"
    return bytes(frame)


def iva_keepalive():
    # The camera's 0x7012 frame: KCP stays alive, no media.
    return struct.pack("<BBHIIHHI", 0xFF, 0x01, 0, 7, 9, 0x0200, 0x7012, 0)


class Camera:
    """Numbers the camera's KCP PUSH segments; optionally relays them."""

    def __init__(self, turn=None):
        self.sn = 0
        self.turn = turn
        self.next_keepalive = 0.0

    def send(self, payload):
        segment = push(self.sn, payload)
        self.sn += 1
        if self.turn is None:
            return (segment, CAMERA_HOST, False)
        channel = self.turn.channels[CAMERA_RELAY]
        return (struct.pack(">HH", channel, len(segment)) + segment, SERVER, True)

    def login(self):
        return self.send(b"login-accepted")

    def keepalive_only(self, t):
        # Live window over: KCP stays alive with a 0x7012 frame every 3 s.
        if t < self.next_keepalive:
            return []
        self.next_keepalive = t + 3.0
        return [self.send(iva_keepalive())]


class ScriptedSig:
    sock = SimpleNamespace(settimeout=lambda seconds: None)
    pending_status = ()

    def send_offer(self, device_uuid, sdp):
        return {"sdp": (
            "a=ice-ufrag:cam\na=ice-pwd:campwd\nm=audio 5555 RTP / AVP 0\n"
            "c=IN IP4 198.51.100.9\n"
            "a=candidate:H 1 UDP 1694498815 192.168.1.30 40000 typ host\n"
        )}

    def _recv_webrtc_content(self):
        raise TimeoutError()

    def send_candidate_complete(self, device_uuid):
        return {}


class KeepaliveSig:
    """msgsvr client of the session keepalives; records each heartbeat."""

    def __init__(self):
        self.heartbeats = []

    def send_heartbeat(self, runtime=0):
        # The loop fixture's simulated clock.
        self.heartbeats.append(p2p_streamer.time.monotonic() - START)

    def send_hello(self):
        pass

    def drain_frames(self, max_frames=64):
        return 0


class ScriptedTurn:
    server_ip, server_port = SERVER
    relay_ip = "198.51.100.5"
    relay_port = 9998
    peer_local_port = 41000
    peer_mapped_ip = peer_mapped_port = None

    def __init__(self, now):
        self.now = now
        self.sent = []  # (relative time, "direct" | "relay", address, datagram)
        self.renewals = []
        self.channels = {}
        self.reverse_channels = {}
        self.peer_sock = SimpleNamespace(
            sendto=lambda data, addr: self.sent.append(
                (self.now[0] - START, "direct", tuple(addr), data)
            )
        )

    def drain_socket(self):
        pass

    def create_permission(self, *args, **kwargs):
        return True

    def channel_bind(self, ip, port, **kwargs):
        channel = self.channels.setdefault((ip, port), 0x4000 + len(self.channels))
        self.reverse_channels[channel] = (ip, port)
        return channel

    def refresh(self, *args):
        return 600

    def renew_due(self):
        self.renewals.append(self.now[0] - START)
        return 60.0

    def send_ice_binding(self, ip, port, *args, **kwargs):
        self.sent.append((self.now[0] - START, "relay-check", (ip, port), b""))

    def send_to_peer(self, ip, port, data):
        self.sent.append((self.now[0] - START, "relay", (ip, port), data))

    def vvp_commands(self):
        """First transmission time of each VVP command we sent over KCP."""
        first = {}
        for at, _, _, data in self.sent:
            for segment in parse_kcp_segments(data) or []:
                payload = segment["data"]
                if (
                    segment["cmd"] == 81
                    and segment["sn"] not in first
                    and len(payload) >= 80
                    and struct.unpack_from("<H", payload, 0x0E)[0] == 0x7010
                ):
                    command = struct.unpack_from(">I", payload, 20 + 0x0C)[0]
                    first[segment["sn"]] = (at, command)
        return [first[sn] for sn in sorted(first)]

    def restarts(self):
        commands = self.vvp_commands()
        return [
            at
            for (at, command), (_, following) in zip(commands, commands[1:])
            if command == VVP_STOP and following == VVP_START
        ]

    def direct_checks(self):
        return [
            at for at, path, address, data in self.sent
            if path == "direct" and address == CAMERA_HOST and data[:2] == b"\x00\x01"
        ]

    def indications(self):
        """Bare STUN Binding Indications (ICE consent) sent to the camera."""
        return [
            at for at, path, address, data in self.sent
            if path == "direct" and address == CAMERA_HOST
            and len(data) == 20 and data[:2] == b"\x00\x11"
        ]


@pytest.fixture
def loop(monkeypatch):
    now = [START]
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0], sleep=lambda s: None,
    ))
    monkeypatch.setattr(p2p_streamer, "_get_local_ips", lambda: ["192.168.1.2"])
    frames = []
    streamer = P2PStreamer(
        SimpleNamespace(session_data={"userID": "1"}, country_code="IT"),
        {"serial_number": "ppsl0123456789", "host_key": HOST_KEY},
        on_video=lambda data: frames.append(now[0] - START),
    )
    streamer._running = True
    turn = ScriptedTurn(now)
    phase = {"continuation": None}
    receive_stream = P2PStreamer._receive_stream

    def continuation(self, *args, **kwargs):
        phase["continuation"] = now[0] - START
        return receive_stream(self, *args, **kwargs)

    monkeypatch.setattr(P2PStreamer, "_receive_stream", continuation)

    def install(script, duration):
        def receive_batch(turn_arg, timeout, limit=2000):
            batch = script(now[0] - START) or []
            now[0] += 0.1 if batch else timeout
            if now[0] - START >= duration:
                streamer.request_stop()
            return batch

        monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", receive_batch)

    def connection(script, *, duration, keepalive=None):
        install(script, duration)
        streamer._stream_with_turn(
            ScriptedSig(), turn, "device-uuid", HOST_KEY, "ppsl0123456789", {},
            turn.server_ip, False, keepalive,
        )
        return now[0] - START

    def continuation_only(script, *, duration, video_count=1):
        """Run _receive_stream alone, as handed over at START."""
        install(script, duration)
        kcp = KcpTunnel(lambda data: turn.sent.append(
            (now[0] - START, "direct", CAMERA_HOST, data)
        ))
        kcp.next_recv_sn = 1
        streamer._active_vvp_session = {
            "kcp": kcp,
            "host_key": HOST_KEY,
            "licence_id": None,
            "video_id": 0,
            "next_sequence": 1,
        }
        receive_stream(
            streamer, turn, kcp, "", HOST_KEY, None, 1, video_count, video_count,
            0, START,
        )
        return now[0] - START

    return SimpleNamespace(
        connection=connection,
        continuation=continuation_only,
        frames=frames,
        turn=turn,
        phase=phase,
    )


def _video_then(silence_from, then):
    camera = Camera()

    def script(t):
        if camera.sn == 0:
            return [camera.login()]
        if t < silence_from:
            return [camera.send(video_frame())]
        return then(camera, t)

    return script


# ----------------------------------------------------------------------
# C106 / C75 - the hand-over keeps the stall clocks and renewal schedule
# ----------------------------------------------------------------------


def test_silent_camera_ends_the_session_about_30s_after_its_last_frame(loop):
    ended = loop.connection(_video_then(20.0, lambda camera, t: []), duration=120.0)

    last_frame = loop.frames[-1]
    assert loop.phase["continuation"] is not None, "the hand-over took place"
    # The connection loop gives up 30 s after the last payload; the
    # continuation must not then wait another 20 s with restarted clocks.
    assert 30.0 <= ended - last_frame < 31.5


def test_a_short_total_silence_does_not_end_the_session(loop):
    def resume_after_25s(camera, t):
        return [] if t < 45.0 else [camera.send(video_frame())]

    ended = loop.connection(_video_then(20.0, resume_after_25s), duration=80.0)

    assert ended >= 80.0
    assert loop.frames[-1] > 79.0


def test_stall_clocks_survive_the_hand_over(loop):
    # Video stops, the camera is silent long enough for the connection loop
    # to hand over, then only keeps KCP alive: the stall that began with the
    # last frame still ends the session 35 s after it, with no extra
    # STOP/START at the hand-over.
    def silent_then_kcp_alive(camera, t):
        if loop.phase["continuation"] is None:
            return []
        return camera.keepalive_only(t)

    ended = loop.connection(_video_then(20.0, silent_then_kcp_alive), duration=150.0)

    last_frame = loop.frames[-1]
    assert loop.phase["continuation"] is not None
    assert ended - last_frame == pytest.approx(35.0, abs=0.6)
    assert len(loop.turn.restarts()) == 3, "8, 18 and 28 s into the stall only"


def test_continuation_keeps_the_turn_renewal_schedule(loop):
    camera = Camera(loop.turn)

    def script(t):
        if camera.sn == 0:
            return [camera.login()]
        if t < 70.0 or loop.phase["continuation"] is not None:
            return [camera.send(video_frame())]
        return []  # silent until the connection loop hands over

    loop.connection(script, duration=150.0)

    handed_over = loop.phase["continuation"]
    assert handed_over is not None
    assert loop.turn.renewals[0] == pytest.approx(60.1, abs=0.2)
    # Renewals stay on their 60 s grid (60, 120, ...) instead of restarting
    # 60 s after the hand-over.
    after = [at for at in loop.turn.renewals if at > handed_over]
    assert after and after[0] == pytest.approx(120.1, abs=0.2)


# ----------------------------------------------------------------------
# C83 / C93 / C87 - the hand-over keeps the direct path and the keepalives
# ----------------------------------------------------------------------


def test_direct_path_and_keepalives_survive_the_hand_over(loop):
    camera = Camera()  # direct: the host candidate on the media socket
    sig = KeepaliveSig()

    def script(t):
        if camera.sn == 0:
            return [camera.login()]
        if t < 20.0 or loop.phase["continuation"] is not None:
            return [camera.send(video_frame())]
        return []  # silent until the connection loop hands over

    loop.connection(
        script,
        duration=150.0,
        keepalive=_SessionKeepalive(sig, registered_at=START, heartbeat_interval=30),
    )

    handed_over = loop.phase["continuation"]
    # Handed over before the first TURN renewal (60 s) is due.
    assert handed_over is not None and handed_over < 60.0
    assert loop.frames[-1] > 149.0, "video resumed after the hand-over"
    # Direct media defers every TURN renewal, in the continuation too, so a
    # TURN-only refusal cannot end the session (C83).
    assert loop.turn.renewals == [], "TURN renewed on a direct path"
    # The msgsvr heartbeat (C93) and ICE consent (C87) go on after it.
    assert [at for at in sig.heartbeats if at > handed_over], (
        "no msgsvr heartbeat after the hand-over"
    )
    assert [at for at in loop.turn.indications() if at > handed_over], (
        "no ICE consent after the hand-over"
    )


# ----------------------------------------------------------------------
# C81 - stall restarts, session end, gap skip and ICE resend
# ----------------------------------------------------------------------


def _kcp_alive_without_video(camera, t):
    return camera.keepalive_only(t)


@pytest.mark.parametrize("receiver", ["connection", "continuation"])
def test_stalled_video_is_restarted_then_the_session_ends_at_35s(loop, receiver):
    if receiver == "connection":
        ended = loop.connection(
            _video_then(10.0, _kcp_alive_without_video), duration=120.0
        )
        last_frame = loop.frames[-1]
    else:
        camera = Camera()
        camera.sn = 1
        ended = loop.continuation(
            lambda t: _kcp_alive_without_video(camera, t), duration=120.0
        )
        last_frame = 0.0  # handed over with a frame just received

    restarts = [at - last_frame for at in loop.turn.restarts()]
    # STOP/START 8 s into the stall, then every 10 s, until the 35 s limit.
    assert len(restarts) == 3
    assert 8.0 < restarts[0] <= 8.6
    assert all(10.0 < later - earlier <= 10.6 for earlier, later in zip(
        restarts, restarts[1:]
    ))
    assert ended - last_frame == pytest.approx(35.0, abs=0.6)


@pytest.mark.parametrize("receiver", ["connection", "continuation"])
def test_a_persistent_hole_is_skipped_after_5s_without_video(loop, receiver):
    camera = Camera()

    def script(t):
        if camera.sn == 0 and receiver == "connection":
            return [camera.login()]
        if 10.0 <= t < 10.15 and not getattr(camera, "lost", False):
            camera.sn += 1  # this segment never arrives
            camera.lost = True
        return [camera.send(video_frame())]

    if receiver == "connection":
        loop.connection(script, duration=20.0)
    else:
        camera.sn = 1
        loop.continuation(script, duration=20.0)

    gaps = [later - earlier for earlier, later in zip(loop.frames, loop.frames[1:])]
    assert max(gaps) == pytest.approx(5.0, abs=0.3)
    assert loop.frames[-1] > 19.0, "delivery resumes after the skip"


def test_ice_checks_are_resent_every_2s_until_login(loop):
    camera = Camera()

    def script(t):
        if t < 7.0:
            return []
        return [camera.login() if camera.sn == 0 else camera.send(video_frame())]

    loop.connection(script, duration=20.0)

    assert loop.turn.direct_checks() == pytest.approx([0.0, 2.0, 4.0, 6.0], abs=0.01)


# ----------------------------------------------------------------------
# C72 - before the first video frame (the 30 s first-video timeout itself is
# owned by test_stream_transport.py)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("receiver", ["connection", "continuation"])
def test_no_vvp_restart_before_the_first_video_frame(loop, receiver):
    # Like 0.1.11 and the vendor app: STOP/START only recovers a stream that
    # was flowing; a camera still starting its encoder is left alone until
    # the 30 s first-video timeout.
    camera = Camera()
    if receiver == "connection":

        def script(t):
            if camera.sn == 0:
                return [camera.login()]
            return _kcp_alive_without_video(camera, t)

        loop.connection(script, duration=120.0)
    else:
        camera.sn = 1
        loop.continuation(
            lambda t: _kcp_alive_without_video(camera, t), duration=120.0,
            video_count=0,
        )

    assert loop.turn.restarts() == []
