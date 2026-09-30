"""Public-client regressions: cache, renewal, concurrency and error privacy."""
import base64
import json
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import requests

from cloudedge import CloudEdgeClient, NetworkError, RateLimitError


@pytest.fixture
def client(tmp_path):
    c = CloudEdgeClient("review@example.test", "password", "IT", "+39",
                        session_cache_file=str(tmp_path / "session.json"),
                        enable_network_ping=False)
    c.BASE_URL = "https://api.example.test"
    c.OPENAPI_BASE_URL = "https://openapi.example.test"
    c.session_data = {
        "userToken": "cached-token", "userID": "123", "loginTime": time.time(),
        "apiServer": c.BASE_URL, "openapiServer": c.OPENAPI_BASE_URL,
        "iotPlatformKeys": {"accessid": "old-id", "accesskey": "old-key"},
    }
    return c


def response(data, status=200, url="https://api.example.test"):
    r = requests.Response()
    r.status_code = status
    r.url = url
    r._content = json.dumps(data).encode()
    return r


def platform_response():
    return response({"resultCode": "1001", "result": {"pfApi": {
        "platform": {"signature": "encrypted", "expireTime": int((time.time() + 3600) * 1000)},
        "mqtt": {"host": "mqtt.example.test", "port": 1883},
        "mqttSignature": "mqtt-password",
    }}})


def setup_platform(client):
    info = base64.b64encode(json.dumps({"accessid": "fresh-id", "accesskey": "fresh-key"}).encode()).decode()
    client._aes_cbc_decrypt = Mock(return_value=info + "-signature")
    client._session.get = Mock(return_value=platform_response())


@pytest.mark.parametrize("existing_session", [False, True])
def test_cached_login_refreshes_and_persists_its_own_iot_credentials(client, existing_session):
    client._save_session_cache(client.session_data)
    client.session_data = {"userToken": "different-token", "userID": "456"} if existing_session else None
    setup_platform(client)
    client._session.post = Mock(side_effect=AssertionError("Must not log in"))

    assert client.authenticate() is True
    assert client.get_mqtt_config()["mqtt_access_id"] == "fresh-id"
    assert client._load_session_cache()["iotPlatformKeys"]["accessid"] == "fresh-id"
    assert client._session.get.call_args.kwargs["params"]["userID"] == "123"


def test_401_after_initial_inventory_rebuilds_tokens_despite_throttle(client):
    setup_platform(client)
    attempts = []
    def transport(method, url, **kwargs):
        base = url.split("?")[0]
        if base.endswith("/v1/app/home/list"):
            return response({"resultCode": "1001", "result": {"homes": []}})
        if base.endswith("/ppstrongs/getDevice.action"):
            return response({"resultCode": "1001", "ipc": [{
                "deviceID": 1, "snNum": "ppsl0000000123456789", "deviceName": "Camera",
                "deviceSignature": "device-token", "t": "123", "onLine": 1,
            }]})
        assert base.endswith("/openapi/device/config")
        params = kwargs["params"]
        attempts.append(params)
        if params["accessid"] == "fresh-id" and params.get("token") == "device-token":
            return response({"iot": {"154": 75}})
        return response({"errid": 401})
    client._session.request = transport

    assert client.get_device_config("123456789")["iot"]["154"] == 75
    assert len(attempts) == 2


@pytest.mark.parametrize("operation", ["read", "write", "status", "wake"])
def test_openapi_http_errors_do_not_expose_signed_urls(client, operation, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "FAKE-SECRET-ERROR-91"
    client._device_tokens["0000000123456789"] = {"token": "device-token", "t": "1"}
    client._session.request = Mock(return_value=response({}, 500, "https://api.example.test/config?token=" + marker))
    calls = {
        "read": lambda: client.get_device_config("123456789"),
        "write": lambda: client.set_device_config("123456789", {"150": 1}, auto_wake=False),
        "status": lambda: client.get_device_online_status("123456789"),
        "wake": lambda: client.wake_device("123456789"),
    }
    error_text = ""
    try:
        result = calls[operation]()
        assert operation == "wake" and result is False
    except NetworkError:
        error_text = traceback.format_exc()
    assert marker not in error_text + caplog.text


def test_inventory_waits_for_login_and_uses_the_new_session(client):
    entered = threading.Event()
    reader_started = threading.Event()
    release = threading.Event()
    observed = []
    client._discover_endpoints = Mock()
    client._aes_encode_param = Mock(return_value="account")
    client._des_encode = Mock(return_value="password")
    setup_platform(client)
    def login(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response({"resultCode": "1001", "result": {"userToken": "new-token", "userID": "123"}})
    def homes(*args, **kwargs):
        observed.append(kwargs["headers"]["X-Ca-Key"])
        return response({"resultCode": "1001", "result": {"homes": []}})
    def read():
        reader_started.set()
        return client.get_homes()
    client._session.post = login
    client._session.request = homes
    with ThreadPoolExecutor(max_workers=2) as pool:
        auth = pool.submit(client.authenticate, force_refresh=True)
        try:
            assert entered.wait(5)
            reader = pool.submit(read)
            assert reader_started.wait(5)
            # The read must remain pending while login owns the transaction.
            with pytest.raises(TimeoutError):
                reader.result(timeout=0.1)
        finally:
            release.set()
        assert auth.result(timeout=5) is True
        assert reader.result(timeout=5) == []
    assert observed == ["new-token"]


def test_mqtt_recovery_retries_platform_fetch_at_a_bounded_cadence(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("cloudedge.client.time.monotonic", lambda: now[0])
    setup_platform(client)
    client._session.get.side_effect = [requests.ConnectionError("unavailable"), platform_response()]
    assert client.refresh_mqtt_config() is None
    for _ in range(3):
        assert client.refresh_mqtt_config() is None
    assert client._session.get.call_count == 1
    now[0] += 60
    assert client.refresh_mqtt_config()["mqtt_access_id"] == "fresh-id"
    assert client._session.get.call_count == 2


def test_poll_budget_caps_requests_and_does_not_retry_after_deadline(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("cloudedge.client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("cloudedge.client.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    client._device_tokens["0000000123456789"] = {"token": "device-token", "t": "1"}
    timeouts = []
    def transport(method, url, **kwargs):
        timeout = kwargs["timeout"]
        limit = timeout.total if hasattr(timeout, "total") else timeout
        timeouts.append(limit)
        now[0] += limit
        raise requests.Timeout("slow server")
    client._session.request = transport
    assert client.wait_for_online("123456789", timeout=3) is False
    assert now[0] <= 103
    assert timeouts == [3]


def test_rate_limited_owned_inventory_never_becomes_a_partial_success(client):
    def transport(method, url, **kwargs):
        base = url.split("?")[0]
        if base.endswith("/v1/app/home/list"):
            return response({"resultCode": "1001", "result": {"homes": [{"homeID": "h", "homeName": "Home"}]}})
        if base.endswith("/v1/app/home/join/device/list"):
            return response({"resultCode": "1001", "ipc": [{"deviceID": 1, "snNum": "shared-sn", "deviceName": "Shared"}]})
        assert base.endswith("/ppstrongs/getDevice.action")
        limited = response({}, 429)
        limited.headers["Retry-After"] = "60"
        return limited
    client._session.request = transport
    with pytest.raises(RateLimitError) as error:
        client.get_all_devices()
    assert error.value.retry_after == 60
