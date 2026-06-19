import pytest

from cloudedge.client import DEVICE_STATUS_DORMANCY
from cloudedge.p2p.p2p_streamer import P2PStreamer, _resolve_signaling_candidates


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


def test_signaling_candidates_prefer_device_region_over_account_region(monkeypatch):
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

    assert candidates[0] == ("euce.mearicloud.com", 28974)
    assert ("usce.mearicloud.com", 28974) in candidates


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

