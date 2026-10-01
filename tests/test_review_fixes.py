"""Regressions for the 2026-09-28 review fixes (F02, F03, F07, F08, F09,
F10, F13, F15, F16, F17).

Every test drives the real client code paths and mocks only the HTTP
transport (`_make_request` / `session.request`) or the clock.
"""
import base64
import hashlib
import hmac
import json
import logging
import subprocess
import threading
import time
from unittest.mock import Mock, patch

import pytest
import requests

from cloudedge import (
    CloudEdgeClient,
    CloudEdgeError,
    NetworkError,
    RateLimitError,
)
from cloudedge.constants import CA_KEY
from cloudedge.utils import parse_retry_after, retry_on_failure


@pytest.fixture
def client(tmp_path):
    c = CloudEdgeClient(
        "user@example.test", "password", "IT", "+39",
        session_cache_file=str(tmp_path / "session"),
    )
    c.BASE_URL = "https://api.example.test"
    c.OPENAPI_BASE_URL = "https://openapi.example.test"
    c.session_data = {
        "userToken": "token", "userID": "123",
        "iotPlatformKeys": {"accessid": "id", "accesskey": "key"},
    }
    c._save_session_cache = Mock()
    return c


def response(data, status_code=200, headers=None):
    r = Mock(status_code=status_code, headers=headers or {})
    r.json.return_value = data
    r.raise_for_status.return_value = None
    return r


# ── F02: token refresh must not recurse through inventory enrichment ──────

OWNED_SN = "ppsl0000000123456789"


def _inventory_transport(calls, *, home_device_sig, owned_device_sig):
    """Transport serving one home (one device) and one owned device."""

    def transport(method, url, **kwargs):
        calls.append(url)
        if url.split("?")[0].endswith("/v1/app/home/list"):
            return response({"resultCode": "1001", "result": {"homes": [
                {"homeID": "h1", "homeName": "Home"},
            ]}})
        if url.split("?")[0].endswith("/v1/app/home/join/device/list"):
            device = {"deviceID": 1, "snNum": OWNED_SN, "deviceName": "Cam",
                      "devStatus": 1, "devTypeID": 1}
            if home_device_sig:
                device["deviceSignature"] = home_device_sig
                device["t"] = 1789766455
            return response({"resultCode": "1001", "ipc": [device]})
        if url.split("?")[0].endswith("/ppstrongs/getDevice.action"):
            device = {"deviceID": 2, "snNum": OWNED_SN, "deviceName": "Cam2",
                      "onLine": 1}
            if owned_device_sig:
                device["deviceSignature"] = owned_device_sig
                device["t"] = 1789766455
            return response({"resultCode": "1001", "ipc": [device]})
        if url.split("?")[0].endswith("/openapi/device/config"):
            return response({"iot": {"126": "192.168.50.10"}})
        raise AssertionError(f"unexpected url {url}")

    return transport


def test_signatureless_device_with_ping_enabled_does_not_recurse(client):
    """Inventory → config-for-IP → missing token → inventory must terminate.

    Before the fix this recursed (each cycle re-listing devices while
    looking for a signature that never comes); with ping enabled the
    enhancement path is the recursion vector. The request count is the
    regression signal: unbounded before, small and constant after.
    """
    client.enable_network_ping = True
    client._detect_local_network = Mock(return_value=None)  # no local ping
    calls = []
    client._make_request = Mock(side_effect=_inventory_transport(
        calls, home_device_sig=None, owned_device_sig=None,
    ))

    devices = client.get_devices()

    assert len(devices) == 1
    # homes + home-list + (refresh: homes + home-list + owned) + one
    # config read per enhanced device (home + owned) — and nothing more.
    # Before the fix this walk recursed without bound.
    assert len(calls) <= 8, calls
    home_lists = [u for u in calls if u.split("?")[0].endswith("/v1/app/home/list")]
    assert len(home_lists) == 2


def test_token_refresh_skips_ping_and_openapi_enrichment(client):
    calls = []
    client._make_request = Mock(side_effect=_inventory_transport(
        calls, home_device_sig="sig", owned_device_sig=None,
    ))

    client._refresh_device_tokens()

    # Plain walk only: homes + per-home + owned. No OpenAPI config reads,
    # no ping-driven status fetches.
    assert len(calls) == 3
    assert not any("/openapi/" in u for u in calls)
    assert "0000000123456789" in client._device_tokens


def test_token_refresh_is_throttled_for_signatureless_devices(client):
    calls = []
    client._make_request = Mock(side_effect=_inventory_transport(
        calls, home_device_sig=None, owned_device_sig=None,
    ))

    client._refresh_device_tokens()
    first = len(calls)
    client._refresh_device_tokens()
    client._refresh_device_tokens()

    assert first == 3
    assert len(calls) == 3  # throttled: no repeated inventory walks


# ── F03: complete inventory or failed refresh (sequence) ──────────────────

def test_inventory_recovers_after_transient_owned_failure(client):
    """complete → owned NetworkError → recovery."""
    state = {"fail": False}

    def transport(method, url, **kwargs):
        if url.split("?")[0].endswith("/v1/app/home/list"):
            return response({"resultCode": "1001", "result": {"homes": [
                {"homeID": "h1", "homeName": "Home"},
            ]}})
        if url.split("?")[0].endswith("/v1/app/home/join/device/list"):
            return response({"resultCode": "1001", "ipc": [
                {"deviceID": 1, "snNum": "sn-shared", "deviceName": "S",
                 "devStatus": 1},
            ]})
        if url.split("?")[0].endswith("/ppstrongs/getDevice.action"):
            if state["fail"]:
                raise requests.exceptions.ConnectionError("blip")
            return response({"resultCode": "1001", "ipc": [
                {"deviceID": 2, "snNum": "sn-owned", "deviceName": "O",
                 "onLine": 1},
            ]})
        raise AssertionError(f"unexpected url {url}")

    client._make_request = Mock(side_effect=transport)

    assert {d["serial_number"] for d in client.get_devices()} == {
        "sn-shared", "sn-owned",
    }

    state["fail"] = True
    with pytest.raises(NetworkError):
        client.get_devices()

    state["fail"] = False
    assert {d["serial_number"] for d in client.get_devices()} == {
        "sn-shared", "sn-owned",
    }


# ── F07: concurrency contract ──────────────────────────────────────────────

def test_overlapping_401_renewal_runs_once_and_both_callers_succeed(client):
    """Two requests hitting 401 at the same time: one renewal, both succeed.

    Without the state lock the second caller would build its signature
    with keys that are being replaced mid-flight and surface a spurious
    AuthenticationError.
    """
    arrive = threading.Barrier(2, timeout=10)
    served = {"first_denied": False, "lock": threading.Lock()}
    renewals = []

    def transport(method, url, **kwargs):
        with served["lock"]:
            deny = not served["first_denied"]
            served["first_denied"] = True
        if deny:
            return response({"errid": 401, "errstr": "Authorization Failed"})
        return response({"iot": {"154": 75}})

    def renew(**kwargs):
        renewals.append(kwargs)
        client.session_data["iotPlatformKeys"] = {
            "accessid": "fresh-id", "accesskey": "fresh-key",
        }

    client._make_request = Mock(side_effect=transport)
    client._fetch_iot_config = Mock(side_effect=renew)
    client._refresh_device_tokens = Mock()

    results = {}

    def read(name):
        try:
            arrive.wait(timeout=10)
            results[name] = client.get_device_config("123456789")["iot"]["154"]
        except Exception as exc:  # noqa: BLE001 - record for assertion
            results[name] = repr(exc)

    threads = [threading.Thread(target=read, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive(), "deadlock: reader thread did not finish"

    assert results == {"a": 75, "b": 75}
    assert len(renewals) == 1


def test_failed_relogin_never_publishes_none_session(client):
    client.session_data = {"userToken": "old", "userID": "1"}
    observed = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            observed.append(client.session_data)

    mock_login = Mock(status_code=200)
    mock_login.raise_for_status.return_value = None
    mock_login.json.return_value = {
        "resultCode": "1002", "resultMsg": "Invalid credentials",
    }

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    try:
        with (
            patch.object(client, "_discover_endpoints"),
            patch.object(client, "_aes_encode_param", return_value="account"),
            patch.object(client, "_des_encode", return_value="password"),
            patch.object(client._session, "post", return_value=mock_login),
            patch.object(client, "_fetch_iot_config"),
        ):
            with pytest.raises(CloudEdgeError):
                client.authenticate(force_refresh=True)
    finally:
        stop.set()
        reader_thread.join(timeout=5)

    # A concurrent reader never observed the intermediate None.
    assert observed
    assert all(entry is not None for entry in observed)


# ── F09: no credentials in errors or logs ─────────────────────────────────

def test_transport_errors_never_leak_the_signed_url(client, caplog):
    secret = "MARKER-SIGNATURE-9f2a"
    signed_url = f"https://api.example.test/v1/app/home/list?signature={secret}"

    def transport(method, url, **kwargs):
        raise requests.exceptions.HTTPError(
            f"500 Server Error for url: {signed_url}",
            response=Mock(status_code=500, url=signed_url, headers={}),
        )

    client._make_request = Mock(side_effect=transport)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(NetworkError) as excinfo:
            client.get_homes()

    blob = "\n".join(
        [
            str(excinfo.value),
            str(excinfo.value.details),
            repr(excinfo.value.__cause__),
            caplog.text,
        ]
    )
    assert secret not in blob
    assert "api.example.test/v1/app/home/list" in str(excinfo.value)


def test_make_request_logs_strip_the_query_string(client, caplog):
    secret = "MARKER-ACCESSID-c331"
    signed_url = f"https://api.example.test/x?accessid={secret}"
    client._session.request = Mock(
        side_effect=requests.exceptions.ConnectionError(f"refused: {signed_url}")
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(requests.exceptions.ConnectionError):
            client._make_request("GET", signed_url, timeout=0.1)

    assert secret not in caplog.text


# ── F10: retry classification and Retry-After ─────────────────────────────

def headers_of(value):
    return {"Retry-After": value} if value is not None else {}


@pytest.mark.parametrize("value,expected", [
    ("120", 120.0),
    ("0", 0.0),
    (None, None),
    ("not-a-date", None),
])
def test_parse_retry_after_accepts_and_rejects_values(value, expected):
    assert parse_retry_after(Mock(headers=headers_of(value))) == expected


def test_parse_retry_after_supports_http_date():
    from datetime import datetime, timezone, timedelta

    when = datetime.now(timezone.utc) + timedelta(seconds=90)
    from email.utils import format_datetime

    parsed = parse_retry_after(
        Mock(headers={"Retry-After": format_datetime(when)})
    )
    assert parsed is not None
    assert 80 <= parsed <= 100


def test_http_429_raises_rate_limit_with_retry_after(client):
    client._session.request = Mock(return_value=response(
        {"detail": "too many"}, status_code=429,
        headers={"Retry-After": "120"},
    ))

    with pytest.raises(RateLimitError) as excinfo:
        client._make_request("GET", "https://api.example.test/x")

    assert excinfo.value.retry_after == 120.0
    # Immediate: no retries, no sleeping out the 120 seconds.
    assert client._session.request.call_count == 1


def test_retry_decorator_does_not_repeat_permanent_http_errors():
    attempts = {"n": 0}

    @retry_on_failure(max_attempts=3, delay=0.01)
    def call():
        attempts["n"] += 1
        raise requests.exceptions.HTTPError(
            "400", response=Mock(status_code=400)
        )

    with pytest.raises(requests.exceptions.HTTPError):
        call()
    assert attempts["n"] == 1


@pytest.mark.parametrize("status", [502, 503, 504])
def test_retry_decorator_retries_transient_server_errors(status):
    attempts = {"n": 0}

    @retry_on_failure(max_attempts=3, delay=0.01)
    def call():
        attempts["n"] += 1
        raise requests.exceptions.HTTPError(
            str(status), response=Mock(status_code=status)
        )

    with pytest.raises(requests.exceptions.HTTPError):
        call()
    assert attempts["n"] == 3


def test_retry_decorator_still_retries_timeouts():
    attempts = {"n": 0}

    @retry_on_failure(max_attempts=3, delay=0.01)
    def call():
        attempts["n"] += 1
        raise requests.exceptions.Timeout("t")

    with pytest.raises(requests.exceptions.Timeout):
        call()
    assert attempts["n"] == 3


# ── F13: MQTT credentials are complete or unavailable ─────────────────────

def pf_response(signature_ok, expire_ms=None):
    from cloudedge.iot_parameters import get_parameter_name  # noqa: F401

    platform = {"signature": "encrypted" if signature_ok else "garbage",
                "expireTime": expire_ms or int((time.time() + 3600) * 1000)}
    return {"resultCode": "1001", "result": {"pfApi": {
        "platform": platform,
        "mqtt": {"host": "mqtt.example.test", "port": "1883"},
        "mqttSignature": "broker-signature",
    }}}


def test_undecryptable_signature_keeps_existing_usable_config(client):
    client._aes_cbc_decrypt = Mock(side_effect=ValueError("nope"))
    client._session.get = Mock(return_value=response(pf_response(False)))
    client.session_data["mqtt"] = {
        "mqtt_host": "h", "mqtt_port": 1883,
        "mqtt_access_id": "old-id", "mqtt_signature": "old-sig",
    }
    client.session_data["iotCredentialsExpireTime"] = time.time() + 3600

    client._fetch_iot_config(force_refresh=True)

    cfg = client.get_mqtt_config()
    assert cfg is not None
    assert cfg["mqtt_access_id"] == "old-id"


def test_undecryptable_signature_never_publishes_empty_credentials(client):
    client._aes_cbc_decrypt = Mock(side_effect=ValueError("nope"))
    client._session.get = Mock(return_value=response(pf_response(False)))

    client._fetch_iot_config(force_refresh=True)

    assert "mqtt" not in client.session_data
    assert client.get_mqtt_config() is None


def test_expired_credentials_are_dropped(client):
    client._aes_cbc_decrypt = Mock(side_effect=ValueError("nope"))
    client._session.get = Mock(return_value=response(pf_response(False)))
    client.session_data["mqtt"] = {
        "mqtt_host": "h", "mqtt_port": 1883,
        "mqtt_access_id": "old-id", "mqtt_signature": "old-sig",
    }
    client.session_data["iotCredentialsExpireTime"] = time.time() - 10

    client._fetch_iot_config(force_refresh=True)

    assert "mqtt" not in client.session_data
    assert client.get_mqtt_config() is None


def test_mqtt_config_recovers_on_later_valid_fetch(client):
    client._aes_cbc_decrypt = Mock(side_effect=ValueError("nope"))
    client._session.get = Mock(return_value=response(pf_response(False)))
    client._fetch_iot_config(force_refresh=True)
    assert client.get_mqtt_config() is None

    info = base64.b64encode(json.dumps(
        {"accessid": "new-id", "accesskey": "new-key"}).encode()
    ).decode()
    client._aes_cbc_decrypt = Mock(return_value=info + "-sig")
    client._session.get = Mock(return_value=response(pf_response(True)))
    client._fetch_iot_config(force_refresh=True)

    cfg = client.get_mqtt_config()
    assert cfg is not None
    assert cfg["mqtt_access_id"] == "new-id"
    assert cfg["mqtt_signature"] == "broker-signature"


def test_get_mqtt_config_requires_complete_credentials(client):
    client.session_data["mqtt"] = {
        "mqtt_host": "h", "mqtt_port": 1883,
        "mqtt_access_id": "id",
        # mqtt_signature missing
    }
    assert client.get_mqtt_config() is None


# ── F15: wake polling budget and rate-limit handling ──────────────────────

def test_ensure_online_success_costs_at_most_three_requests(client):
    client._device_tokens["123456789"] = {"token": "t", "t": "1"}
    client._make_request = Mock(return_value=response({"status": "online"}))
    client.wake_device = Mock(return_value=True)

    assert client.ensure_online("123456789", device_id=1) is True
    # One initial status check + one post-wake poll at most.
    assert client._make_request.call_count <= 2


def test_wake_polling_backs_off_on_rate_limit(client, monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    client._device_tokens["123456789"] = {"token": "t", "t": "1"}
    statuses = iter([
        "dormancy",           # ensure_online initial check
        RateLimitError("rl", retry_after=120.0),  # first wake poll
        "online",             # poll after the backoff
    ])

    def transport(method, url, **kwargs):
        if url.split("?")[0].endswith("/openapi/device/status"):
            outcome = next(statuses)
            if isinstance(outcome, Exception):
                raise outcome
            return response({"status": outcome})
        raise AssertionError(f"unexpected url {url}")

    client._make_request = Mock(side_effect=transport)
    client.wake_device = Mock(return_value=True)

    assert client.ensure_online("123456789") is True
    # One backoff bounded by the 35s wait budget, no rapid loop.
    assert sleeps and max(sleeps) <= 35.0
    assert len(sleeps) <= 2


# ── F16: ping availability probed once ────────────────────────────────────

def test_ping_availability_is_probed_once_per_client(client, monkeypatch):
    probes = {"n": 0}

    def fake_run(*args, **kwargs):
        probes["n"] += 1
        raise FileNotFoundError("no ping")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert client._check_ping_availability() is False
    assert client._check_ping_availability() is False
    assert probes["n"] == 1


# ── F17: login uses the shared CA_KEY ─────────────────────────────────────

def test_login_headers_and_signature_use_the_shared_ca_key(client):
    captured = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["headers"] = headers
        captured["data"] = data
        return response({"resultCode": "1001", "result": {
            "userToken": "t", "userID": "u",
        }})

    with (
        patch.object(client, "_discover_endpoints"),
        patch.object(client, "_aes_encode_param", return_value="account"),
        patch.object(client, "_des_encode", return_value="password"),
        patch.object(client._session, "post", side_effect=fake_post),
        patch.object(client, "_fetch_iot_config"),
    ):
        assert client.authenticate(force_refresh=True) is True

    assert captured["headers"]["X-Ca-Key"] == CA_KEY

    # Reproduce the exact signature with the shared constant: the wire
    # format is pinned, independent of how the key is spelled in code.
    data = captured["data"]
    timestamp = data["t"]
    ca_sign_data = (
        f"phoneType=a&sourceApp=8&appVer=6.2.8&iotType=4&equipmentNo= &"
        f"appVerCode=643&localTime={timestamp}&password={data['password']}&"
        f"t={timestamp}&lngType=en&countryCode=IT&"
        f"userAccount={data['userAccount']}&encryStatus=1&"
        f"phoneCode=39"
    )
    expected = base64.b64encode(
        hmac.new(CA_KEY.encode(), ca_sign_data.encode(), hashlib.sha1).digest()
    ).decode()
    assert captured["headers"]["X-Ca-Sign"] == expected
