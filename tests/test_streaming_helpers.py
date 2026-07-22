import struct

import pytest

from cloudedge.client import CloudEdgeClient, DEVICE_STATUS_DORMANCY
from cloudedge.p2p.p2p_streamer import (
    P2PStreamer,
    STREAM_TYPE_AUDIO,
    STREAM_TYPE_IFRAME,
    STREAM_TYPE_INFO,
    STREAM_TYPE_PFRAME,
    VVP_CMD_START_LIVE,
    VVP_CMD_STOP,
    _resolve_signaling_candidates,
    parse_stream_frame,
)
from cloudedge.p2p.root_discovery import (
    _build_discovery_frame,
    _parse_discovery_frame,
    discover_msgsvr_endpoints,
)


class _RegionApi:
    OPENAPI_BASE_URL = "https://openapi-usce.mearicloud.com"
    region = "us"
    session_data = {"mqtt": {"mqtt_host": "events-usce.mearicloud.com"}}


class _RunSessionApi:
    OPENAPI_BASE_URL = "https://openapi-usce.mearicloud.com"
    region = "us"
    session_data = {"mqtt": {"mqtt_host": "events-usce.mearicloud.com"}}

    def get_device_online_status(self, serial_number):
        return "online"


class _StreamingSwitchApi(_RunSessionApi):
    def __init__(self):
        self.config_calls = []

    def refresh_streaming_metadata(self, device):
        device.update(
            {
                "device_id": 1032537077,
                "host_key": "account-region-host-key",
            }
        )
        return device

    def set_device_config(
        self,
        serial_number,
        parameters,
        auto_wake=True,
        device_id=None,
    ):
        self.config_calls.append(
            (serial_number, parameters, auto_wake, device_id)
        )
        return True


class _RunSessionSig:
    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.closed = False
        self.logged_out = []
        self.connected = False
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def send_logout(self, device_uuid):
        self.logged_out.append(device_uuid)

    def close(self):
        self.closed = True


class _DummyApi:
    def __init__(self, api_status):
        self._api_status = api_status

    def get_device_online_status(self, serial_number):
        return self._api_status


class _DummySig:
    def query_device_status(self, device_uuid):
        raise AssertionError("signaling retry should not run for dormancy")


class _RecordingKcp:
    def __init__(self):
        self.payloads = []

    def send_iva_data(self, payload):
        self.payloads.append(payload)


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
            "coturn_ip": "192.0.2.1",
            "coturn_port": 9100,
            "username": "user",
            "pwd": "password",
        }


class _LifecycleTurn:
    instances = []

    def __init__(self, *args):
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        return None

    def allocate(self):
        return True

    def close(self):
        self.closed = True


class _NoContactWakeApi:
    def __init__(self):
        self.wake_calls = []
        self.wait_calls = []

    def wake_device(self, serial_number, device_id):
        self.wake_calls.append((serial_number, device_id))
        return True

    def wait_for_online(self, serial_number, timeout=30.0, poll_interval=2.0):
        self.wait_calls.append((serial_number, timeout, poll_interval))
        return True


class _NoContactWakeSig:
    def __init__(self):
        self.query_calls = []

    def send_wake_connect(self, *args, **kwargs):
        raise AssertionError("signaling wake should not run without keepalive contact")

    def wait_for_status(self, *args, **kwargs):
        raise AssertionError("wait_for_status should not run without keepalive contact")

    def query_device_status(self, device_uuid):
        self.query_calls.append(device_uuid)
        return {"status": "online", "contact": {"keepalive": {}}, "nat": {}}


class _SignalingLagApi:
    def __init__(self):
        self.wake_calls = []
        self.wait_calls = []

    def wake_device(self, serial_number, device_id):
        self.wake_calls.append((serial_number, device_id))
        return True

    def wait_for_online(self, serial_number, timeout=30.0, poll_interval=2.0):
        self.wait_calls.append((serial_number, timeout, poll_interval))
        return True

    def get_device_online_status(self, serial_number):
        return "online"


class _SignalingLagSig:
    def __init__(self):
        self.query_calls = []
        self._statuses = [
            {"status": "offline"},
            {"status": "offline"},
            {
                "status": "online",
                "contact": {"keepalive": {"node": "natsvr"}},
                "nat": {"local_port": 1},
            },
        ]

    def send_wake_connect(self, *args, **kwargs):
        raise AssertionError("signaling wake should not run without keepalive contact")

    def wait_for_status(self, *args, **kwargs):
        raise AssertionError("wait_for_status should not run without keepalive contact")

    def query_device_status(self, device_uuid):
        self.query_calls.append(device_uuid)
        index = min(len(self.query_calls) - 1, len(self._statuses) - 1)
        return self._statuses[index]


def test_retry_offline_signaling_status_maps_dormancy_from_openapi():
    streamer = P2PStreamer(
        api=_DummyApi(DEVICE_STATUS_DORMANCY),
        device={
            "serial_number": "ppsl123",
            "host_key": "host-key",
            "name": "Camera",
        },
    )

    status = streamer._retry_offline_signaling_status(
        _DummySig(),
        "device-uuid",
        {"status": "offline", "contact": {"keepalive": {}}, "nat": {}},
    )

    assert status["status"] == DEVICE_STATUS_DORMANCY


def test_streamer_prefers_device_uuid_from_device_metadata():
    streamer = P2PStreamer(
        api=_DummyApi(DEVICE_STATUS_DORMANCY),
        device={
            "serial_number": "ppsl123",
            "device_uuid": "device-uuid-from-app",
            "host_key": "host-key",
            "name": "Camera",
        },
    )

    assert streamer._device_uuid == "device-uuid-from-app"


def test_streamer_prefers_relay_license_id_for_vvp_login():
    streamer = P2PStreamer(
        api=_DummyApi(DEVICE_STATUS_DORMANCY),
        device={
            "serial_number": "ppsl123456789",
            "relay_license_id": "relay-license-id",
            "host_key": "host-key",
            "name": "Camera",
        },
    )

    assert streamer._device.get("relay_license_id") == "relay-license-id"
    assert streamer._get_vvp_licence_id() == "relay-license-id"


def test_streamer_accepts_explicit_video_id():
    streamer = P2PStreamer(
        api=_DummyApi(DEVICE_STATUS_DORMANCY),
        device={
            "serial_number": "ppsl123",
            "host_key": "host-key",
            "name": "Camera",
        },
        video_id=1,
    )

    assert streamer._video_id == 1


def test_streamer_rejects_invalid_video_id():
    with pytest.raises(ValueError, match="video_id"):
        P2PStreamer(
            api=_DummyApi(DEVICE_STATUS_DORMANCY),
            device={
                "serial_number": "ppsl123",
                "host_key": "host-key",
                "name": "Camera",
            },
            video_id=256,
        )


def test_active_vvp_restart_uses_observed_stop_and_start_commands():
    streamer = P2PStreamer(
        api=_DummyApi(DEVICE_STATUS_DORMANCY),
        device={
            "serial_number": "ppsl123",
            "host_key": "0123456789abcdef0123456789abcdef",
            "name": "Camera",
        },
        video_id=102,
    )
    kcp = _RecordingKcp()
    streamer._active_vvp_session = {
        "kcp": kcp,
        "host_key": streamer._host_key,
        "licence_id": "licence-id",
        "video_id": 102,
        "next_sequence": 7,
    }

    assert streamer._restart_active_vvp_stream() is True

    assert [struct.unpack_from(">I", packet, 0x0C)[0] for packet in kcp.payloads] == [
        VVP_CMD_STOP,
        VVP_CMD_START_LIVE,
    ]
    assert [struct.unpack_from(">I", packet, 0x08)[0] for packet in kcp.payloads] == [
        7,
        8,
    ]
    assert [packet[0x38] for packet in kcp.payloads] == [102, 102]
    assert streamer._active_vvp_session["next_sequence"] == 9


def test_do_stream_sends_stop_before_closing_turn_on_failure(monkeypatch):
    _LifecycleTurn.instances = []
    kcp = _RecordingKcp()
    streamer = P2PStreamer(
        api=_LifecycleApi(),
        device={
            "serial_number": "ppsl123",
            "device_uuid": "device-uuid",
            "host_key": "0123456789abcdef0123456789abcdef",
            "name": "Camera",
        },
    )
    streamer._running = True

    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer.TurnClient",
        _LifecycleTurn,
    )

    def fail_after_vvp_start(self, *args):
        self._active_vvp_session = {
            "kcp": kcp,
            "host_key": self._host_key,
            "licence_id": "licence-id",
            "video_id": 100,
            "next_sequence": 3,
        }
        raise RuntimeError("stream failure")

    monkeypatch.setattr(P2PStreamer, "_stream_with_turn", fail_after_vvp_start)

    with pytest.raises(RuntimeError, match="stream failure"):
        streamer._do_stream(_LifecycleSig())

    assert struct.unpack_from(">I", kcp.payloads[0], 0x0C)[0] == VVP_CMD_STOP
    assert _LifecycleTurn.instances[0].closed is True
    assert streamer._active_vvp_session is None


@pytest.mark.parametrize(
    ("frame_type", "header_size", "length_offset", "length_format"),
    [
        (STREAM_TYPE_IFRAME, 0x3C, 0x38, "<I"),
        (STREAM_TYPE_PFRAME, 0x34, 0x30, "<I"),
        (STREAM_TYPE_AUDIO, 0x34, 0x30, "<I"),
        (STREAM_TYPE_INFO, 8, 6, "<H"),
    ],
)
def test_stream_frame_parser_rejects_truncated_declared_payload(
    frame_type,
    header_size,
    length_offset,
    length_format,
):
    frame = bytearray(header_size + 2)
    frame[:4] = bytes((0, 0, 1, frame_type))
    struct.pack_into(length_format, frame, length_offset, 100)

    assert parse_stream_frame(bytes(frame)) is None


def test_refresh_streaming_metadata_uses_current_account_device_values(monkeypatch):
    client = CloudEdgeClient("user@example.com", "password", "US", "+1")
    current_account_device = {
        "serial_number": "ppsl123",
        "device_id": 1032537077,
        "host_key": "us-account-host-key",
        "name": "Camera",
    }
    monkeypatch.setattr(client, "get_all_devices", lambda: [current_account_device])
    stale_device = {
        "serial_number": "ppsl123",
        "device_id": 112539302,
        "host_key": "eu-account-host-key",
        "name": "Camera",
    }

    refreshed = client.refresh_streaming_metadata(stale_device)

    assert refreshed is stale_device
    assert refreshed["device_id"] == 1032537077
    assert refreshed["host_key"] == "us-account-host-key"


def test_wake_dormant_device_falls_back_to_http_wake_without_keepalive_contact():
    api = _NoContactWakeApi()
    sig = _NoContactWakeSig()
    streamer = P2PStreamer(
        api=api,
        device={
            "serial_number": "ppsl123",
            "host_key": "host-key",
            "name": "Camera",
            "device_id": 123,
        },
    )

    status = streamer._wake_dormant_device(
        sig,
        "device-uuid",
        {"contact": {}, "nat": {}},
    )

    assert status == {"status": "online", "contact": {"keepalive": {}}, "nat": {}}
    assert api.wake_calls == [("ppsl123", 123)]
    assert len(api.wait_calls) == 1
    assert api.wait_calls[0][0] == "ppsl123"
    assert sig.query_calls == ["device-uuid"]


def test_wake_dormant_device_waits_for_signaling_to_report_online_after_http_wake():
    api = _SignalingLagApi()
    sig = _SignalingLagSig()
    streamer = P2PStreamer(
        api=api,
        device={
            "serial_number": "ppsl123",
            "host_key": "host-key",
            "name": "Camera",
            "device_id": 123,
        },
    )
    streamer._running = True

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "cloudedge.p2p.p2p_streamer._SIGNAL_STATUS_RETRY_WINDOW", 0.01
        )
        monkeypatch.setattr("cloudedge.p2p.p2p_streamer._SIGNAL_STATUS_RETRY_POLL", 0.0)
        monkeypatch.setattr("cloudedge.p2p.p2p_streamer.time.sleep", lambda _: None)
        status = streamer._wake_dormant_device(
            sig,
            "device-uuid",
            {"contact": {}, "nat": {}},
        )

    assert status["status"] == "online"
    assert status["contact"]["keepalive"]["node"] == "natsvr"
    assert sig.query_calls == ["device-uuid", "device-uuid", "device-uuid"]


def test_signaling_candidates_skip_static_hosts_when_discovery_works(monkeypatch):
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer.discover_msgsvr_endpoints",
        lambda **kwargs: [("198.51.100.20", 31001)],
    )
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer.socket.gethostbyname",
        lambda host: {"euce.mearicloud.com": "47.254.142.96"}.get(host, "203.0.113.10"),
    )

    candidates = _resolve_signaling_candidates(
        _RegionApi(),
        {
            "device_region": "Europe/Rome",
            "device_icon_url": "https://meari-eu.oss-eu-central-1.aliyuncs.com/device.png",
        },
    )

    # Dynamic discovery worked: the legacy fixed-port 28974 hosts are dead
    # weight (each costs ~10s of refused/timeout per retry loop) — skip them.
    assert candidates == [("198.51.100.20", 31001)]


def test_signaling_candidates_prefer_account_region_over_device_region(monkeypatch):
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer.discover_msgsvr_endpoints",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer.socket.gethostbyname",
        lambda host: {"euce.mearicloud.com": "47.254.142.96"}.get(host, "203.0.113.10"),
    )

    candidates = _resolve_signaling_candidates(
        _RegionApi(),
        {
            "device_region": "Europe/Rome",
            "device_icon_url": "https://meari-eu.oss-eu-central-1.aliyuncs.com/device.png",
        },
    )

    # Discovery empty: static fallbacks kick in, account region (US) first.
    assert ("usce.mearicloud.com", 28974) in candidates
    assert ("euce.mearicloud.com", 28974) in candidates
    assert candidates.index(("usce.mearicloud.com", 28974)) < candidates.index(
        ("euce.mearicloud.com", 28974)
    )


def test_root_discovery_frame_round_trip():
    payload = {"action": "conf", "ver": 15259, "uuid": "110673554"}

    assert _parse_discovery_frame(_build_discovery_frame(payload)) == payload


def test_root_discovery_returns_dynamic_tcp_contact(monkeypatch):
    monkeypatch.setattr(
        "cloudedge.p2p.root_discovery.socket.getaddrinfo",
        lambda host, port, family: [(family, 2, 17, "", ("203.0.113.5", 0))],
    )
    calls = []

    def fake_query(endpoint, payload, timeout):
        calls.append((endpoint, payload, timeout))
        return {
            "contact": {
                "transport": "tcp",
                "ip": "198.51.100.25",
                "port": 32018,
            }
        }

    monkeypatch.setattr("cloudedge.p2p.root_discovery._query_root", fake_query)

    endpoints = discover_msgsvr_endpoints(
        openapi_server_hint="https://openapi-euce.mearicloud.com",
        api_server_hint="https://apis-eu-frankfurt.cloudedge360.com",
        client_id_hint=110673554,
    )

    assert endpoints == [("198.51.100.25", 32018)]
    assert calls[0][0] == ("203.0.113.5", 9253)
    assert calls[0][1]["uuid"] == "110673554"


def test_run_session_tries_next_signaling_candidate_after_empty_stream(monkeypatch):
    _RunSessionSig.instances = []
    calls = []

    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer._resolve_signaling_candidates",
        lambda api, device=None: [("first.example", 28974), ("second.example", 28974)],
    )
    monkeypatch.setattr("cloudedge.p2p.p2p_streamer.MsgSvrClient", _RunSessionSig)

    def fake_do_stream(self, sig):
        calls.append(sig.host)
        if len(calls) == 1:
            return (0, 0)
        self._running = False
        return (3, 100)

    monkeypatch.setattr(P2PStreamer, "_do_stream", fake_do_stream)

    streamer = P2PStreamer(
        api=_RunSessionApi(),
        device={
            "serial_number": "ppsl123",
            "device_uuid": "device-uuid",
            "host_key": "host-key",
            "name": "Camera",
        },
    )

    assert streamer.run_session() == (3, 100)
    assert calls == ["first.example", "second.example"]
    assert [sig.host for sig in _RunSessionSig.instances] == [
        "first.example",
        "second.example",
    ]
    assert all(sig.closed for sig in _RunSessionSig.instances)


def test_run_session_does_not_retry_after_stop_request(monkeypatch):
    _RunSessionSig.instances = []
    calls = []
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer._resolve_signaling_candidates",
        lambda api, device=None: [("first.example", 28974), ("second.example", 28974)],
    )
    monkeypatch.setattr("cloudedge.p2p.p2p_streamer.MsgSvrClient", _RunSessionSig)

    def fake_do_stream(self, sig):
        calls.append(sig.host)
        self.request_stop()
        raise OSError("stream stopped")

    monkeypatch.setattr(P2PStreamer, "_do_stream", fake_do_stream)
    streamer = P2PStreamer(
        api=_RunSessionApi(),
        device={
            "serial_number": "ppsl123",
            "device_uuid": "device-uuid",
            "host_key": "host-key",
            "name": "Camera",
        },
    )

    assert streamer.run_session() == (0, 0)
    assert calls == ["first.example"]


def test_run_session_refreshes_metadata_and_toggles_live_stream_switch(monkeypatch):
    api = _StreamingSwitchApi()
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer._resolve_signaling_candidates",
        lambda api, device=None: [("account-region.example", 28974)],
    )
    monkeypatch.setattr("cloudedge.p2p.p2p_streamer.MsgSvrClient", _RunSessionSig)
    monkeypatch.setattr(P2PStreamer, "_do_stream", lambda self, sig: (1, 100))

    streamer = P2PStreamer(
        api=api,
        device={
            "serial_number": "ppsl123",
            "device_id": 112539302,
            "host_key": "stale-host-key",
            "name": "Camera",
        },
    )

    assert streamer.run_session() == (1, 100)
    assert streamer._host_key == "account-region-host-key"
    assert api.config_calls == [
        ("ppsl123", {"167": 1}, False, 1032537077),
        ("ppsl123", {"167": 0}, False, 1032537077),
    ]


def test_run_session_can_leave_live_stream_switch_to_caller(monkeypatch):
    api = _StreamingSwitchApi()
    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer._resolve_signaling_candidates",
        lambda api, device=None: [("account-region.example", 28974)],
    )
    monkeypatch.setattr("cloudedge.p2p.p2p_streamer.MsgSvrClient", _RunSessionSig)
    monkeypatch.setattr(P2PStreamer, "_do_stream", lambda self, sig: (1, 100))

    streamer = P2PStreamer(
        api=api,
        device={
            "serial_number": "ppsl123",
            "device_id": 112539302,
            "host_key": "stale-host-key",
            "name": "Camera",
        },
        manage_stream_switch=False,
    )

    assert streamer.run_session() == (1, 100)
    assert streamer._host_key == "account-region-host-key"
    assert api.config_calls == []
