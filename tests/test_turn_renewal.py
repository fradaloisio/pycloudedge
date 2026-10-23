"""Regression tests for TURN lifecycle renewals (F06) and setup cleanup (V02).

F06 — RFC 8656 §9/§12: allocation, permission (300 s) and channel-binding
(600 s) expiries are tracked separately; renewals run before expiry, reuse
the channel number already bound to the peer, no longer depend on the UDP
receive queue being empty, and a refused renewal raises a typed error so the
caller can rebuild the session.

C83/C84 narrow that contract to relayed media: like the vendor app, a
session whose confirmed media path is direct stops renewing TURN (no
deallocation; the allocation expires on the server), so a TURN refusal or
outage ends relay sessions only, and the next renewal deadline counts from
when renew_due() returns.  C74 — a rotated nonce (438 Stale Nonce) is
adopted and the request repeated once.

V02 — every socket acquired by the TURN setup path (connect / allocate /
peer_stun_binding) is closed deterministically when a later setup step
raises.

All long-duration scenarios use a simulated clock and a scripted transport;
no real sleeps are involved.  New production symbols (renew_due,
TurnRenewalRefusedError) are imported inside the tests that exercise them so
a pre-fix run of this module reports each behavioural regression separately
instead of dying at collection time.
"""

import hashlib
import hmac
import socket
import struct
from types import SimpleNamespace

import pytest

from cloudedge.p2p import p2p_streamer, turn_client
from cloudedge.p2p.p2p_streamer import P2PStreamer
from cloudedge.p2p.turn_client import (
    ALLOCATE_REQUEST,
    ATTR_CHANNEL_NUMBER,
    ATTR_ERROR_CODE,
    ATTR_LIFETIME,
    ATTR_MESSAGE_INTEGRITY,
    ATTR_NONCE,
    ATTR_REALM,
    ATTR_USERNAME,
    ATTR_XOR_PEER_ADDRESS,
    ATTR_XOR_RELAYED_ADDRESS,
    CHANNEL_BIND_REQUEST,
    CREATE_PERM_REQUEST,
    REFRESH_REQUEST,
    TurnClient,
    _build_stun,
    _decode_xor_address,
    _encode_attr,
    _encode_xor_address,
    _parse_stun,
)


# RFC 8656 lifetimes, mirrored here so the module collects against the
# pre-fix code and each test fails for its own behavioural reason.
PERMISSION_LIFETIME = 300
CHANNEL_BIND_LIFETIME = 600


class FakeClock:
    """Deterministic monotonic clock stand-in (no real waiting)."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class FakeTurnSocket:
    """UDP socket double: records sends, answers recvfrom from a script."""

    def __init__(self, server):
        self._server = server
        self.timeout = 5.0
        self.closed = False

    def bind(self, address):
        return None

    def getsockname(self):
        return ("0.0.0.0", 41000 + len(self._server.sockets))

    def settimeout(self, timeout):
        self.timeout = timeout

    def setblocking(self, flag):
        return None

    def setsockopt(self, *args):
        return None

    def sendto(self, data, addr):
        self._server.record(data, control=(self is self._server.control))

    def recvfrom(self, bufsize):
        if self is not self._server.control:
            raise socket.timeout("peer-media socket has no scripted traffic")
        if self._server.muted:
            # TURN/WAN outage: the wait runs to the socket timeout.
            self._server.clock.advance(self.timeout)
            raise socket.timeout("TURN server unreachable")
        response = self._server.respond()
        if response is None:
            raise socket.timeout("no scripted response")
        return response, (self._server.server_ip, 9100)

    def close(self):
        self.closed = True


class ScriptedTurnServer:
    """Controlled TURN transport answering straight from a script.

    Records every granted request as (clock_time, kind, detail) so tests can
    assert renewal timing against the simulated clock without sleeps.
    """

    def __init__(self, clock):
        self.clock = clock
        self.server_ip = "198.51.100.53"
        self.pending = []
        self.events = []
        self.sockets = []
        self.control = None
        self.refuse_refresh = False
        self.muted = False
        self.granted_lifetime = 600
        # Nonce rotation like coturn (STUN_DEFAULT_NONCE_EXPIRATION_TIME is
        # 600 s); None keeps one nonce for the whole test.
        self.nonce = b"nonce-123"
        self.nonce_lifetime = None
        self.nonce_issued_at = clock()
        self.nonce_serial = 0
        self.integrity_failures = 0

    def socket_factory(self, *args):
        sock = FakeTurnSocket(self)
        self.sockets.append(sock)
        if self.control is None:
            self.control = sock
        return sock

    def record(self, data, control):
        if not control:
            return
        parsed = _parse_stun(data)
        if parsed is not None:
            parsed["raw"] = data
            self.pending.append(parsed)

    def respond(self):
        if not self.pending:
            return None
        request = self.pending.pop(0)
        msg_type = request["type"]
        txn_id = request["txn_id"]
        attrs = request["attrs"]

        if ATTR_USERNAME in attrs:
            if not _integrity_ok(request["raw"], b"user", b"hangzhou", b"password"):
                self.integrity_failures += 1
            if (
                self.nonce_lifetime is not None
                and self.clock() - self.nonce_issued_at >= self.nonce_lifetime
            ):
                self.nonce_serial += 1
                self.nonce = b"nonce-%d" % self.nonce_serial
                self.nonce_issued_at = self.clock()
            if attrs.get(ATTR_NONCE) != self.nonce:
                payload = _encode_attr(
                    ATTR_ERROR_CODE, b"\x00\x00\x04\x26 Stale Nonce"
                )
                payload += _encode_attr(ATTR_REALM, b"hangzhou")
                payload += _encode_attr(ATTR_NONCE, self.nonce)
                self.events.append((self.clock(), "stale_nonce", (msg_type, txn_id)))
                return _build_stun(msg_type | 0x0110, payload, txn_id)[0]
            self.events.append((self.clock(), "accepted", (msg_type, txn_id)))

        if msg_type == ALLOCATE_REQUEST:
            if ATTR_USERNAME not in attrs:  # unauthenticated probe -> 401
                payload = _encode_attr(
                    ATTR_ERROR_CODE, b"\x00\x00\x04\x01 Unauthorized"
                )
                payload += _encode_attr(ATTR_REALM, b"hangzhou")
                payload += _encode_attr(ATTR_NONCE, self.nonce)
                self.events.append((self.clock(), "allocate_challenge", None))
                return _build_stun(msg_type | 0x0110, payload, txn_id)[0]
            payload = _encode_attr(
                ATTR_XOR_RELAYED_ADDRESS,
                _encode_xor_address("198.51.100.99", 37000),
            )
            payload += _encode_attr(
                ATTR_LIFETIME, struct.pack(">I", self.granted_lifetime)
            )
            self.events.append(
                (self.clock(), "allocate", self.granted_lifetime)
            )
            return _build_stun(msg_type | 0x0100, payload, txn_id)[0]

        if msg_type == REFRESH_REQUEST:
            if self.refuse_refresh:
                payload = _encode_attr(
                    ATTR_ERROR_CODE, b"\x00\x00\x04\x37 Allocation Mismatch"
                )
                self.events.append((self.clock(), "refresh_refused", None))
                return _build_stun(msg_type | 0x0110, payload, txn_id)[0]
            payload = _encode_attr(
                ATTR_LIFETIME, struct.pack(">I", self.granted_lifetime)
            )
            self.events.append((self.clock(), "refresh", self.granted_lifetime))
            return _build_stun(msg_type | 0x0100, payload, txn_id)[0]

        if msg_type == CREATE_PERM_REQUEST:
            peer_ip, _ = _decode_xor_address(attrs[ATTR_XOR_PEER_ADDRESS])
            self.events.append((self.clock(), "permission", peer_ip))
            return _build_stun(msg_type | 0x0100, b"", txn_id)[0]

        if msg_type == CHANNEL_BIND_REQUEST:
            channel = struct.unpack(">H", attrs[ATTR_CHANNEL_NUMBER][:2])[0]
            peer_ip, peer_port = _decode_xor_address(
                attrs[ATTR_XOR_PEER_ADDRESS]
            )
            self.events.append(
                (self.clock(), "channel_bind", (peer_ip, peer_port, channel))
            )
            return _build_stun(msg_type | 0x0100, b"", txn_id)[0]

        return _build_stun(msg_type | 0x0100, b"", txn_id)[0]


def _integrity_ok(raw, username, realm, password):
    """Check MESSAGE-INTEGRITY with the long-term key (RFC 8489 §14.5)."""
    key = hashlib.md5(username + b":" + realm + b":" + password).digest()
    length = struct.unpack_from(">H", raw, 2)[0]
    pos = 20
    while pos + 4 <= 20 + length:
        attr_type, attr_length = struct.unpack_from(">HH", raw, pos)
        if attr_type == ATTR_MESSAGE_INTEGRITY:
            header = raw[:2] + struct.pack(">H", pos - 20 + 24) + raw[4:20]
            expected = hmac.new(key, header + raw[20:pos], hashlib.sha1).digest()
            return raw[pos + 4 : pos + 24] == expected
        pos += 4 + ((attr_length + 3) & ~3)
    return False


@pytest.fixture
def turn_env(monkeypatch):
    clock = FakeClock()
    server = ScriptedTurnServer(clock)
    monkeypatch.setattr(
        "cloudedge.p2p.turn_client.socket.socket", server.socket_factory
    )
    return SimpleNamespace(clock=clock, server=server)


def _connected_turn(clock):
    # The clock seam is part of the F06 fix.  Falling back to the pre-fix
    # constructor keeps this module collectible against unfixed code so the
    # fail-first run reports each behavioural defect instead of a TypeError.
    try:
        turn = TurnClient("198.51.100.53", 9100, "user", "password", clock=clock)
    except TypeError:
        turn = TurnClient("198.51.100.53", 9100, "user", "password")
    turn.connect()
    assert turn.allocate()
    return turn


def _session_events(server, start):
    return [event for event in server.events if event[0] >= start]


# ----------------------------------------------------------------------
# F06 — channel-number stability and separate renewal deadlines
# ----------------------------------------------------------------------


def test_channel_bind_renewal_reuses_channel_number(turn_env):
    turn = _connected_turn(turn_env.clock)
    try:
        first = turn.channel_bind("203.0.113.7", 5555)
        turn_env.clock.advance(570)  # binding close to its 600 s expiry
        renewed = turn.channel_bind("203.0.113.7", 5555)
        assert first is not None
        assert renewed == first, "re-binding a peer must reuse its channel"
        assert turn.channels[("203.0.113.7", 5555)] == first
    finally:
        turn.close()


def test_simulated_session_renews_all_lifetimes_before_expiry(turn_env):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        turn.create_permission("203.0.113.11")
        turn.channel_bind("203.0.113.10", 6000)
        turn.channel_bind("203.0.113.11", 6001)

        start = clock()
        # Drive renewals exactly like P2PStreamer does: poll, then wait the
        # recommended interval.  >10 simulated minutes, zero real sleeps.
        renewal_at = clock() + 60
        while clock() - start < 700:
            clock.advance(0.5)
            if clock() >= renewal_at:
                renewal_at = clock() + turn.renew_due()
    finally:
        turn.close()

    events = _session_events(server, start)

    refresh_times = [at for at, kind, _ in events if kind == "refresh"]
    assert len(refresh_times) >= 10, "allocation must keep being refreshed"
    for earlier, later in zip(refresh_times, refresh_times[1:]):
        assert later - earlier < 600, "allocation renewed before expiry"

    for ip in ("203.0.113.10", "203.0.113.11"):
        times = [at for at, kind, d in events if kind == "permission" and d == ip]
        assert len(times) >= 2, "permission must be renewed inside 700 s"
        for earlier, later in zip(times, times[1:]):
            assert later - earlier < PERMISSION_LIFETIME, (
                "permission renewed before its 300 s expiry"
            )

    for peer in (("203.0.113.10", 6000), ("203.0.113.11", 6001)):
        entries = [
            (at, detail[2])
            for at, kind, detail in events
            if kind == "channel_bind" and (detail[0], detail[1]) == peer
        ]
        assert len(entries) >= 1, "channel binding must be renewed inside 700 s"
        for earlier, later in zip(entries, entries[1:]):
            assert later[0] - earlier[0] < CHANNEL_BIND_LIFETIME, (
                "binding renewed before its 600 s expiry"
            )
        assert {channel for _, channel in entries} == {turn.channels[peer]}, (
            "renewals must reuse the channel number bound to the peer"
        )


def test_refused_renewal_raises_typed_error(turn_env):
    from cloudedge.p2p.turn_client import TurnRenewalRefusedError

    clock = turn_env.clock
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        turn_env.server.refuse_refresh = True
        clock.advance(700)
        with pytest.raises(TurnRenewalRefusedError):
            turn.renew_due()
    finally:
        turn.close()


# ----------------------------------------------------------------------
# F06 — renewals must not depend on the receive queue being empty
# ----------------------------------------------------------------------


class FakeKcp:
    def __init__(self):
        self.handshakes = 0
        self.sent = []

    def poll_data(self):
        return None

    def flush_acks(self):
        return None

    def send_handshake(self):
        self.handshakes += 1

    def send_iva_data(self, payload):
        self.sent.append(payload)

    def retransmit_unacked(self):
        return None

    def skip_gap(self):
        return False

    def process_input(self, data):
        # Keep the simulated camera delivering video while exercising TURN
        # renewal ownership. Empty live sessions now correctly time out.
        frame = bytearray(64)
        frame[:4] = b"\x00\x00\x01\xfc"
        struct.pack_into("<I", frame, 56, 4)
        frame[60:] = b"live"
        return ("data", bytes(frame))


def _kcp_keepalive_datagram():
    header = struct.pack("<IBBHIIII", 0x0C, 81, 0, 32, 0, 0, 0, 4)
    return header + b"junk"


class _LifecycleApi:
    session_data = {"userID": "123"}
    country_code = "IT"


class _LifecycleSig:
    def register(self, **kwargs):
        return {}

    def webrtc_hello_full(self):
        return None

    def query_device_status(self, device_uuid):
        return {"status": "online", "contact": {}, "nat": {}}

    def request_coturn(self, device_uuid):
        return {
            "coturn_ip": "198.51.100.53",
            "coturn_port": 9100,
            "username": "user",
            "pwd": "password",
        }


def _make_streamer():
    return P2PStreamer(
        api=_LifecycleApi(),
        device={
            "serial_number": "ppsl123",
            "device_uuid": "device-uuid",
            "host_key": "0123456789abcdef0123456789abcdef",
            "name": "Camera",
        },
    )


def _run_receive_stream(
    monkeypatch, turn, clock, *, packets_per_poll, duration=700.0, **receive_kwargs
):
    """Drive _receive_stream over `duration` simulated seconds.

    packets_per_poll > 0 models continuous traffic: the receive buffer is
    refilled before it can ever drain, which is exactly the condition that
    used to starve TURN renewals.  packets_per_poll == 0 models an idle
    path with only a sparse KCP keepalive every 10 s so the receiver's
    20 s no-data guard does not end the session early.
    """
    streamer = _make_streamer()
    streamer._running = True
    kcp = FakeKcp()

    fake_time = SimpleNamespace(
        time=lambda: clock.now,
        monotonic=lambda: clock.now,
        sleep=lambda seconds: None,
    )
    monkeypatch.setattr(p2p_streamer, "time", fake_time)

    start = clock()
    state = {"last_keepalive": start}

    def fake_recv_udp_batch(turn_arg, timeout, limit=2000):
        clock.advance(timeout)
        if clock() - start >= duration:
            streamer._running = False
            # Keep the buffer non-empty so stopping the loop cannot run the
            # idle branch (which would let the pre-fix code sneak in one
            # last renewal and mask the starvation defect).
            if packets_per_poll:
                return [
                    (_kcp_keepalive_datagram(), ("203.0.113.10", 6000), False)
                ] * packets_per_poll
            return []
        if packets_per_poll:
            return [
                (_kcp_keepalive_datagram(), ("203.0.113.10", 6000), False)
            ] * packets_per_poll
        if clock() - state["last_keepalive"] >= 10.0:
            state["last_keepalive"] = clock()
            return [(_kcp_keepalive_datagram(), ("203.0.113.10", 6000), False)]
        return []

    monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", fake_recv_udp_batch)

    counts = streamer._receive_stream(
        turn,
        kcp,
        ice_pwd="",
        host_key="",
        licence_id=None,
        vvp_seq_start=1,
        frame_count_start=0,
        video_count_start=0,
        bytes_start=0,
        start_time=start,
        **receive_kwargs,
    )
    return counts, start


def test_receive_stream_renews_turn_under_continuous_traffic(
    monkeypatch, turn_env
):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        turn.channel_bind("203.0.113.10", 6000)
        clock.advance(1.0)  # setup grants fall before the session window

        counts, start = _run_receive_stream(
            monkeypatch, turn, clock, packets_per_poll=4
        )

        events = _session_events(server, start)
        refresh_times = [at for at, kind, _ in events if kind == "refresh"]
        assert refresh_times, "TURN renewals never ran under continuous traffic"
        for earlier, later in zip(refresh_times, refresh_times[1:]):
            assert later - earlier < 600, "allocation renewed before expiry"

        perm_times = [
            at for at, kind, _ in events if kind == "permission"
        ]
        assert perm_times, "permissions never renewed under continuous traffic"
        for earlier, later in zip(perm_times, perm_times[1:]):
            assert later - earlier < PERMISSION_LIFETIME, (
                "permission renewed before its 300 s expiry"
            )

        binds = [
            (at, detail[2])
            for at, kind, detail in events
            if kind == "channel_bind"
        ]
        assert binds, "channel bindings never renewed under continuous traffic"
        assert {channel for _, channel in binds} == {
            turn.channels[("203.0.113.10", 6000)]
        }, "renewal must reuse the peer's channel number"
        assert counts[0] > 0
        assert counts[1] == counts[0] * 4
    finally:
        turn.close()


def test_receive_stream_renews_turn_without_continuous_traffic(
    monkeypatch, turn_env
):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        turn.channel_bind("203.0.113.10", 6000)
        clock.advance(1.0)  # setup grants fall before the session window

        counts, start = _run_receive_stream(
            monkeypatch, turn, clock, packets_per_poll=0
        )

        events = _session_events(server, start)
        refresh_times = [at for at, kind, _ in events if kind == "refresh"]
        assert len(refresh_times) >= 10, "allocation must keep being refreshed"

        perm_times = [
            at for at, kind, _ in events if kind == "permission"
        ]
        assert perm_times, "permissions never renewed on the idle path"
        for earlier, later in zip(perm_times, perm_times[1:]):
            assert later - earlier < PERMISSION_LIFETIME, (
                "permission renewed before its 300 s expiry"
            )

        binds = [
            (at, detail[2])
            for at, kind, detail in events
            if kind == "channel_bind"
        ]
        assert binds, "channel bindings never renewed on the idle path"
        assert {channel for _, channel in binds} == {
            turn.channels[("203.0.113.10", 6000)]
        }
        assert counts[0] > 0
        assert counts[1] == counts[0] * 4
    finally:
        turn.close()


def test_receive_stream_ends_session_when_renewal_is_refused(
    monkeypatch, turn_env
):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        clock.advance(1.0)  # setup grants fall before the session window
        server.refuse_refresh = True

        counts, start = _run_receive_stream(
            monkeypatch, turn, clock, packets_per_poll=0
        )

        refused = [e for e in server.events if e[1] == "refresh_refused"]
        assert refused, "the refusal must be observable"
        assert counts[0] > 0
        assert counts[1] == counts[0] * 4
        assert clock() - start < 200, (
            "a refused renewal must end the session promptly so the caller "
            "can rebuild it, not be silently ignored"
        )
    finally:
        turn.close()


# ----------------------------------------------------------------------
# C83/C84 — renewals only while media is relayed, deadline after the call
# ----------------------------------------------------------------------

CAMERA_HOST = ("192.168.1.30", 40000)
CAMERA_RELAY = ("198.51.100.99", 5555)
RENEWAL_KINDS = ("refresh", "refresh_refused", "permission", "channel_bind")


def _push(sn, payload):
    # Independent KCP wire format, rather than the production encoder.
    return struct.pack("<IBBHIIII", 12, 81, 0, 1024, 0, sn, 0, len(payload)) + payload


def _video_frame():
    frame = bytearray(64)
    frame[:4] = b"\x00\x00\x01\xfc"
    struct.pack_into("<I", frame, 56, 4)
    frame[60:] = b"live"
    return bytes(frame)


class _MediaSignaling:
    sock = SimpleNamespace(settimeout=lambda seconds: None)
    pending_status = ()

    def send_offer(self, device_uuid, sdp):
        return {"sdp": (
            "a=ice-ufrag:cam\na=ice-pwd:secret\nm=audio 5555 RTP / AVP 0\n"
            "c=IN IP4 198.51.100.99\n"
            "a=candidate:H 1 UDP 1694498815 192.168.1.30 40000 typ host\n"
        )}

    def _recv_webrtc_content(self):
        raise socket.timeout()

    def send_candidate_complete(self, device_uuid):
        return {}


def _run_stream_with_turn(monkeypatch, turn, clock, *, relayed, duration):
    """Drive the connection loop with a camera streaming on one media path."""
    streamer = _make_streamer()
    streamer._running = True
    frames = []
    streamer.on_video = frames.append
    monkeypatch.setattr(p2p_streamer, "time", SimpleNamespace(
        time=lambda: clock.now, monotonic=lambda: clock.now, sleep=lambda s: None,
    ))
    monkeypatch.setattr(p2p_streamer, "_get_local_ips", lambda: ["192.168.1.2"])
    start = clock()
    sn = [0]

    def receive_batch(turn_arg, timeout, limit=2000):
        clock.advance(0.1)
        if clock() - start >= duration:
            streamer.request_stop()
        kcp = _push(sn[0], b"login-accepted" if sn[0] == 0 else _video_frame())
        sn[0] += 1
        if relayed:
            channel = turn.channels[CAMERA_RELAY]
            datagram = struct.pack(">HH", channel, len(kcp)) + kcp
            return [(datagram, (turn.server_ip, turn.server_port), True)]
        return [(kcp, CAMERA_HOST, False)]

    monkeypatch.setattr(p2p_streamer, "_recv_udp_batch", receive_batch)
    counts = streamer._stream_with_turn(
        _MediaSignaling(), turn, "device-uuid", streamer._host_key,
        streamer._sn_num, {}, turn.server_ip, False,
    )
    return counts, start, frames


def test_direct_session_stops_renewing_turn_and_survives_a_refusal(
    monkeypatch, turn_env
):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        server.refuse_refresh = True
        counts, start, frames = _run_stream_with_turn(
            monkeypatch, turn, clock, relayed=False, duration=700
        )
    finally:
        turn.close()

    assert clock() - start >= 700, "a TURN-only refusal must not end direct media"
    assert counts[0] > 6000
    renewals = [e for e in server.events if e[0] > start and e[1] in RENEWAL_KINDS]
    assert renewals == [], "no TURN renewals while media is direct"


def test_relayed_session_still_ends_when_a_renewal_is_refused(monkeypatch, turn_env):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        server.refuse_refresh = True
        counts, start, _ = _run_stream_with_turn(
            monkeypatch, turn, clock, relayed=True, duration=700
        )
    finally:
        turn.close()

    assert counts[0] > 0
    assert clock() - start < 200, "relay sessions keep the F06 contract"
    assert [e for e in server.events if e[0] > start and e[1] == "refresh_refused"]


def test_receive_stream_defers_renewals_on_a_direct_path(monkeypatch, turn_env):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        turn.channel_bind("203.0.113.10", 6000)
        clock.advance(1.0)
        server.refuse_refresh = True

        counts, start = _run_receive_stream(
            monkeypatch, turn, clock, packets_per_poll=4,
            confirmed_peer=("203.0.113.10", 6000, True),
        )
    finally:
        turn.close()

    assert clock() - start >= 700
    assert [e for e in server.events if e[0] > start and e[1] in RENEWAL_KINDS] == []


def test_renewal_deadline_counts_from_after_a_blocked_renewal(monkeypatch, turn_env):
    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    calls = []
    renew_due = turn.renew_due

    def timed_renew_due(*args, **kwargs):
        began = clock()
        try:
            return renew_due(*args, **kwargs)
        finally:
            calls.append((began, clock()))

    turn.renew_due = timed_renew_due
    try:
        turn.create_permission("203.0.113.10")
        clock.advance(1.0)
        server.muted = True  # TURN outage: every transaction waits 3 s

        _run_receive_stream(
            monkeypatch, turn, clock, packets_per_poll=4, duration=120.0
        )
    finally:
        turn.close()

    assert len(calls) >= 3
    blocked = [ended - began for began, ended in calls]
    assert all(seconds >= 3.0 for seconds in blocked)
    gaps = [later[0] - earlier[1] for earlier, later in zip(calls, calls[1:])]
    # renew_due() asks for a retry in 5 s; counted from before the 3 s
    # block, the next renewal would start 2 s after it returned and starve
    # the media loop in back-to-back blocking transactions.
    assert min(gaps) >= 4.5


# ----------------------------------------------------------------------
# C74 — a rotated nonce (438 Stale Nonce) is renewed, not fatal
# ----------------------------------------------------------------------


def test_renewals_retry_once_with_each_rotated_nonce(turn_env):
    clock = turn_env.clock
    server = turn_env.server
    server.nonce_lifetime = 600  # coturn's default nonce lifetime
    turn = _connected_turn(clock)
    try:
        turn.create_permission("203.0.113.10")
        turn.channel_bind("203.0.113.10", 6000)
        start = clock()
        renewal_at = clock() + 60
        while clock() - start < 1300:
            clock.advance(0.5)
            if clock() >= renewal_at:
                renewal_at = clock() + turn.renew_due()
    finally:
        turn.close()

    events = _session_events(server, start)
    stale = [(at, detail) for at, kind, detail in events if kind == "stale_nonce"]
    accepted = [(at, detail) for at, kind, detail in events if kind == "accepted"]
    assert len(stale) >= 2, "the nonce rotated at 600 and 1200 s"
    for at, (msg_type, txn_id) in stale:
        retries = [d for t, d in accepted if t == at and d[0] == msg_type]
        assert retries, "a stale request is repeated at once"
        assert all(d[1] != txn_id for d in retries), "with a new transaction"
    refresh_times = [at for at, kind, _ in events if kind == "refresh"]
    assert refresh_times[-1] - start > 1200, "renewals continue past each rotation"
    assert turn.nonce == server.nonce
    assert server.integrity_failures == 0, "every retry is signed with the new nonce"


def test_refresh_gives_up_when_the_retry_is_refused_too(turn_env):
    from cloudedge.p2p.turn_client import TurnRenewalRefusedError

    clock = turn_env.clock
    server = turn_env.server
    turn = _connected_turn(clock)
    try:
        server.nonce_lifetime = 0  # every request finds its nonce stale
        clock.advance(1)
        with pytest.raises(TurnRenewalRefusedError, match="438"):
            turn.refresh()
    finally:
        turn.close()

    assert len([e for e in server.events if e[1] == "stale_nonce"]) == 2


# ----------------------------------------------------------------------
# V02 — deterministic cleanup of sockets acquired during TURN setup
# ----------------------------------------------------------------------


def test_do_stream_closes_sockets_when_allocate_fails(turn_env, monkeypatch):
    server = turn_env.server
    streamer = _make_streamer()
    streamer._running = True

    def failing_allocate(self):
        raise RuntimeError("TURN server said no")

    monkeypatch.setattr(turn_client.TurnClient, "allocate", failing_allocate)

    with pytest.raises(RuntimeError, match="TURN server said no"):
        streamer._do_stream(_LifecycleSig())

    assert server.sockets, "connect() must have acquired TURN sockets"
    assert all(sock.closed for sock in server.sockets), (
        "sockets acquired before the failure must be closed deterministically"
    )


def test_do_stream_closes_sockets_when_peer_binding_fails(
    turn_env, monkeypatch
):
    server = turn_env.server
    streamer = _make_streamer()
    streamer._running = True

    def failing_peer_stun_binding(self):
        raise OSError("network unreachable")

    monkeypatch.setattr(
        turn_client.TurnClient, "peer_stun_binding", failing_peer_stun_binding
    )

    with pytest.raises(OSError, match="network unreachable"):
        streamer._do_stream(_LifecycleSig())

    assert server.sockets, "connect() must have acquired TURN sockets"
    assert all(sock.closed for sock in server.sockets), (
        "sockets acquired before the failure must be closed deterministically"
    )


def test_do_stream_closes_sockets_when_stream_loop_completes(
    turn_env, monkeypatch
):
    server = turn_env.server
    streamer = _make_streamer()
    streamer._running = True

    monkeypatch.setattr(
        P2PStreamer, "_stream_with_turn", lambda self, *args, **kwargs: (2, 200)
    )

    assert streamer._do_stream(_LifecycleSig()) == (2, 200)
    assert all(sock.closed for sock in server.sockets)
