"""Per-attempt refresh of a camera's account-specific streaming metadata.

P2PStreamer.run_session() calls refresh_streaming_metadata() before every
attempt, so it must stay a plain lookup: no OpenAPI config reads, no ping,
the client lock taken per request, and no say over the caller's ``online``.
Only the HTTP transport is stubbed; the client code paths are real.
"""
import json
import threading
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
import requests

from cloudedge import CloudEdgeClient

SERIAL = "ppsl0000000123456789"


def _response(data, status=200):
    response = requests.Response()
    response.status_code = status
    response.url = "https://api.example.test/endpoint"
    response._content = json.dumps(data).encode()
    return response


def _camera(**fields):
    """A listing entry as the current account sees the camera."""
    return {
        "deviceID": 7, "snNum": SERIAL, "deviceName": "Garden",
        "hostKey": "current-host-key", "devStatus": 0, "onLine": 0,
        "deviceSignature": "device-token", "t": "1", **fields,
    }


@pytest.fixture
def client(tmp_path):
    c = CloudEdgeClient(
        "user@example.test", "password", "IT", "+39",
        session_cache_file=str(tmp_path / "session.json"),
    )
    c.BASE_URL = "https://api.example.test"
    c.OPENAPI_BASE_URL = "https://openapi.example.test"
    c.session_data = {
        "userToken": "token", "userID": "123",
        "iotPlatformKeys": {"accessid": "id", "accesskey": "key"},
    }
    c._save_session_cache = Mock()
    c._detect_local_network = Mock(return_value=None)
    return c


def _serve(client, *, homes, owned, fail=None):
    """Serve the plain listings; ``homes`` maps homeID to its device list.

    A home this account cannot list answers with an error code; ``fail``
    maps a request to the response served instead. Returns the request log:
    "homes", "home:<id>", "owned", or the path of anything else.
    """
    calls = []
    fail = fail or {}

    def transport(method, url, **kwargs):
        path, _, query = url.partition("?")
        if path.endswith("/v1/app/home/list"):
            label = "homes"
            data = {"resultCode": "1001", "result": {"homes": [
                {"homeID": home_id, "homeName": home_id} for home_id in homes
            ]}}
        elif path.endswith("/v1/app/home/join/device/list"):
            home_id = parse_qs(query)["homeID"][0]
            label = f"home:{home_id}"
            data = (
                {"resultCode": "1001", "ipc": homes[home_id]} if home_id in homes
                else {"resultCode": "1005", "resultMsg": "no such home"}
            )
        elif path.endswith("/ppstrongs/getDevice.action"):
            label, data = "owned", {"resultCode": "1001", "ipc": owned}
        else:
            label, data = path, {"iot": {"126": "192.168.50.10"}}
        calls.append(label)
        # Not fail.get(...) or ...: an error Response is falsy.
        return fail[label] if label in fail else _response(data)

    client._session.request = transport
    return calls


class _HandOffLock:
    """The client lock, handed to ``hook`` once this thread lets go of it.

    Runs ``hook`` the first time the creating thread's hold drops to zero,
    so another caller gets in exactly when the walk releases the lock (two
    threads racing for the RLock would be up to the scheduler).
    """

    def __init__(self, lock, hook):
        self._lock = lock
        self._hook = hook
        self._owner = threading.get_ident()
        self._depth = 0

    def acquire(self, *args, **kwargs):
        acquired = self._lock.acquire(*args, **kwargs)
        if acquired and threading.get_ident() == self._owner:
            self._depth += 1
        return acquired

    def release(self):
        self._lock.release()
        if threading.get_ident() == self._owner:
            self._depth -= 1
            if not self._depth and self._hook is not None:
                hook, self._hook = self._hook, None
                hook()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc_info):
        self.release()


def test_refresh_uses_the_current_account_device_values(client):
    """The same camera has other ids and host keys in another region's
    account: the handshake must use the authenticated account's values."""
    _serve(client, homes={}, owned=[_camera(deviceID=1032537077, hostKey="us-account-host-key")])
    stale_device = {
        "serial_number": SERIAL, "device_id": 112539302,
        "host_key": "eu-account-host-key", "name": "Garden",
    }

    refreshed = client.refresh_streaming_metadata(stale_device)

    assert refreshed is stale_device
    assert refreshed["device_id"] == 1032537077
    assert refreshed["host_key"] == "us-account-host-key"


@pytest.mark.parametrize("home_id, homes, owned, listing", [
    ("h1", {"h1": [_camera()], "h2": []}, [], "home:h1"),
    (None, {"h1": []}, [_camera()], "owned"),
])
def test_refresh_reads_one_plain_listing(client, monkeypatch, home_id, homes, owned, listing):
    """No full inventory per stream attempt: only the listing the camera
    came from, without OpenAPI config reads or pings (seconds per camera on
    a LAN, all under the client lock), and the caller's ``online`` stays."""
    client._detect_local_network = Mock(return_value="192.168.50.0/24")
    pings = []
    monkeypatch.setattr(
        "cloudedge.client.subprocess.run",
        lambda cmd, **kwargs: pings.append(cmd) or Mock(returncode=1),
    )
    calls = _serve(client, homes=homes, owned=owned)
    device = {"serial_number": SERIAL, "home_id": home_id, "host_key": "stale", "online": True}

    client.refresh_streaming_metadata(device)

    assert device["host_key"] == "current-host-key"
    assert device["online"] is True
    assert calls == [listing]
    assert pings == []


def test_refresh_walks_the_other_listings_when_its_own_misses(client):
    """A dict from another account names a home this account cannot list:
    the remaining listings are searched in inventory order instead."""
    calls = _serve(client, homes={"h1": []}, owned=[_camera()])
    device = {"serial_number": SERIAL, "home_id": "eu-home", "host_key": "eu-host-key"}

    client.refresh_streaming_metadata(device)

    assert device["host_key"] == "current-host-key"
    assert calls == ["home:eu-home", "homes", "home:h1", "owned"]


def test_other_client_calls_run_between_the_listings_of_a_walk(client):
    """The walk takes the client lock per listing, never across the walk: a
    coordinator poll or another camera's wake waits for one request, not
    for every listing the walk reads."""
    calls = _serve(client, homes={"h1": []}, owned=[_camera()])
    serve = client._session.request
    callers = []

    def transport(method, url, **kwargs):
        response = serve(method, url, **kwargs)
        callers.append((threading.current_thread().name, calls[-1]))
        return response

    def another_caller():
        other = threading.Thread(target=client.get_homes, name="other", daemon=True)
        other.start()
        other.join(timeout=5)

    client._session.request = transport
    client._state_lock = _HandOffLock(client._state_lock, another_caller)
    device = {"serial_number": SERIAL, "home_id": "eu-home", "host_key": "eu-host-key"}

    client.refresh_streaming_metadata(device)

    walk = threading.current_thread().name
    assert callers == [
        (walk, "home:eu-home"),
        ("other", "homes"),
        (walk, "homes"),
        (walk, "home:h1"),
        (walk, "owned"),
    ]
    assert device["host_key"] == "current-host-key"


@pytest.mark.parametrize("failure", [
    _response({"resultCode": "1023", "resultMsg": "session rejected"}),
    _response({}, status=500),
    _response({}, status=429),
], ids=["AuthenticationError", "NetworkError", "RateLimitError"])
def test_transient_failures_end_the_walk_instead_of_being_skipped(client, failure):
    """Only a listing this account cannot read is skipped. A rejected
    session, a network or server error or rate limiting ends the lookup
    (the caller keeps its metadata) instead of pressing on through the
    remaining listings."""
    calls = _serve(client, homes={"h1": []}, owned=[_camera()], fail={"home:h1": failure})
    device = {"serial_number": SERIAL, "home_id": "eu-home", "host_key": "eu-host-key"}

    client.refresh_streaming_metadata(device)

    assert calls == ["home:eu-home", "homes", "home:h1"]
    assert device["host_key"] == "eu-host-key"
