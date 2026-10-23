"""Regressions for malformed caches and incomplete device discovery."""
import json
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from cloudedge import CloudEdgeClient, CloudEdgeError, NetworkError


@pytest.fixture
def client(tmp_path):
    return CloudEdgeClient(
        "user@example.test", "password", "IT", "+39",
        session_cache_file=str(tmp_path / "session.json"),
    )


@pytest.mark.parametrize("data", [
    [], ["token"], None, "invalid", 42,
    *({"loginTime": stamp, "userToken": "token", "userID": "id"}
      for stamp in ("yesterday", None, float("nan"), float("inf"))),
    {"loginTime": time.time() + 86400, "userToken": "token", "userID": "id"},
    {"loginTime": time.time()},
    {"loginTime": time.time(), "userToken": "token"},
    {"loginTime": time.time(), "userToken": 123, "userID": "id"},
])
def test_invalid_cache_is_ignored(client, data):
    with open(client.session_cache_file, "w") as file:
        json.dump(data, file)
    assert client._load_session_cache() is None


def test_valid_cache_is_reused(client):
    session = {"loginTime": time.time(), "userToken": "token", "userID": "id"}
    client._save_session_cache(session)
    loaded = client._load_session_cache()
    assert loaded is not None
    assert loaded["userToken"] == session["userToken"]
    assert loaded["userID"] == session["userID"]


def test_cache_is_bound_to_the_account(client):
    """Two accounts sharing one cache path must never adopt each other's
    session token."""
    other = CloudEdgeClient(
        "other@example.test", "password", "IT", "+39",
        session_cache_file=client.session_cache_file,
    )
    client._save_session_cache(
        {"loginTime": time.time(), "userToken": "token-a", "userID": "id"}
    )

    assert other._load_session_cache() is None
    # The owning account still reuses its own entry.
    assert client._load_session_cache()["userToken"] == "token-a"


def test_cache_is_bound_to_the_region(client):
    client._save_session_cache(
        {"loginTime": time.time(), "userToken": "token", "userID": "id"}
    )
    client.country_code = "FR"

    assert client._load_session_cache() is None


def test_legacy_cache_without_identity_is_invalidated(client):
    with open(client.session_cache_file, "w") as file:
        json.dump(
            {"loginTime": time.time(), "userToken": "token", "userID": "id"},
            file,
        )

    assert client._load_session_cache() is None


def test_legacy_cache_discovers_missing_endpoints_without_logging_in(client):
    client._save_session_cache({
        "loginTime": time.time(), "userToken": "token", "userID": "id", "mqtt": {"host": "host"},
    })
    def discover():
        client.BASE_URL = "https://api.example.test"
        client.OPENAPI_BASE_URL = "https://openapi.example.test"
    client._discover_endpoints = Mock(side_effect=discover)
    client._session.post = Mock(side_effect=AssertionError("Must reuse the token"))
    client._session.get = Mock(side_effect=NetworkError("Platform temporarily unavailable"))
    assert client.authenticate() is True
    assert client.BASE_URL == "https://api.example.test"
    assert client.OPENAPI_BASE_URL == "https://openapi.example.test"
    assert client._load_session_cache()["apiServer"] == client.BASE_URL


def test_failed_home_discovery_does_not_return_partial_inventory(client):
    client.session_data = {"userToken": "token"}
    client._get_owned_devices = Mock(return_value=[])
    client.get_homes = Mock(return_value=[
        {"home_id": "a", "name": "A"}, {"home_id": "b", "name": "B"},
    ])
    client.get_devices_by_home = Mock(side_effect=[
        [{"serial_number": "sn-a"}], NetworkError("temporarily unavailable"),
    ])
    with pytest.raises(CloudEdgeError, match="temporarily unavailable"):
        client.get_all_devices()


def test_owned_and_shared_devices_are_merged(client):
    """Neither inventory is complete: shared homes only appear in the per-home
    list, owned devices only in the legacy list."""
    client.session_data = {"userToken": "token"}
    client.get_homes = Mock(return_value=[
        {"home_id": "own", "name": ""}, {"home_id": "shared", "name": "Colonna"},
    ])
    client.get_devices_by_home = Mock(side_effect=[
        [], [{"serial_number": "sn-shared"}],
    ])
    client._get_owned_devices = Mock(return_value=[{"serial_number": "sn-owned"}])

    devices = client.get_devices()

    assert [d["serial_number"] for d in devices] == ["sn-shared", "sn-owned"]


def test_devices_listed_by_both_endpoints_are_not_duplicated(client):
    client.session_data = {"userToken": "token"}
    client.get_homes = Mock(return_value=[{"home_id": "h", "name": "Home"}])
    client.get_devices_by_home = Mock(return_value=[{"serial_number": "sn-1"}])
    client._get_owned_devices = Mock(return_value=[{"serial_number": "sn-1"}])

    assert len(client.get_devices()) == 1


def test_transient_owned_device_failure_fails_the_refresh(client):
    """A network blip on the owned endpoint must not present a partial
    inventory as complete: consumers would drop the devices they cannot
    see (Home Assistant removes entities and stops their streams)."""
    client.session_data = {"userToken": "token"}
    client.get_homes = Mock(return_value=[{"home_id": "h", "name": "Home"}])
    client.get_devices_by_home = Mock(return_value=[{"serial_number": "sn-1"}])
    client._get_owned_devices = Mock(side_effect=NetworkError("gone"))

    with pytest.raises(NetworkError):
        client.get_devices()


def _response(data, status=200):
    response = requests.Response()
    response.status_code = status
    response.url = "https://api.example.test/endpoint"
    response._content = json.dumps(data).encode()
    return response


SHARED_DEVICE = {"deviceID": 1, "snNum": "sn-1", "deviceName": "Shared"}


def _serve_inventory(client, owned_reply, home_devices=(SHARED_DEVICE,), *, home_reply=None):
    """Real request path: one home, and getDevice.action answers owned_reply."""
    client.session_data = {"userToken": "token", "userID": "123"}

    def transport(method, url, **kwargs):
        path = url.split("?")[0]
        if path.endswith("/v1/app/home/list"):
            return _response({"resultCode": "1001", "result": {"homes": [
                {"homeID": "h", "homeName": "Home"},
            ]}})
        if path.endswith("/v1/app/home/join/device/list"):
            if home_reply is not None:
                return home_reply
            return _response({"resultCode": "1001", "ipc": list(home_devices)})
        assert path.endswith("/ppstrongs/getDevice.action")
        return owned_reply

    client._session.request = transport


@pytest.mark.parametrize("joined_result_code", ["1001", "1106"])
def test_owner_home_does_not_block_joined_or_owned_devices(client, joined_result_code):
    # The real API rejects the join endpoint for an owner's default home.
    # A rejection for a joined home must still fail the complete inventory.
    client.session_data = {"userToken": "token", "userID": "123"}

    def transport(method, url, **kwargs):
        parsed = urlsplit(url)
        if parsed.path.endswith("/v1/app/home/list"):
            return _response({"resultCode": "1001", "result": {"homes": [
                {"homeID": "owned", "homeName": "", "owner": True, "rooms": []},
                {"homeID": "joined", "homeName": "Shared", "owner": False},
            ]}})
        if parsed.path.endswith("/v1/app/home/join/device/list"):
            if parse_qs(parsed.query)["homeID"] == ["owned"]:
                return _response({"resultCode": "1106"})
            return _response({"resultCode": joined_result_code, "ipc": [SHARED_DEVICE]})
        assert parsed.path.endswith("/ppstrongs/getDevice.action")
        return _response({"resultCode": "1001", "ipc": [
            {"deviceID": 2, "snNum": "sn-owned", "deviceName": "Owned"},
        ]})

    client._session.request = transport
    if joined_result_code == "1001":
        assert {device["serial_number"] for device in client.get_devices()} == {
            "sn-1", "sn-owned",
        }
    else:
        with pytest.raises(CloudEdgeError, match="home 'Shared'.*Code: 1106"):
            client.get_devices()


@pytest.mark.parametrize("status", [404, 410])
def test_permanent_owned_device_failure_keeps_home_inventory(client, status):
    """A legacy endpoint going away must not take the whole inventory down."""
    _serve_inventory(client, _response({}, status))

    assert [d["serial_number"] for d in client.get_devices()] == ["sn-1"]


@pytest.mark.parametrize("reply", [
    {"resultCode": "1004", "resultMsg": "unexpected"},
    ["not", "an", "object"],
    {"resultCode": "1001", "result": "garbled"},
])
def test_unrecognised_owned_device_reply_fails_the_refresh(client, reply):
    """Only an explicit "endpoint gone" may leave the owned cameras out: an
    unknown result code or a malformed reply would present a partial
    inventory as complete, and consumers remove the cameras they miss."""
    _serve_inventory(client, _response(reply))

    with pytest.raises(CloudEdgeError):
        client.get_devices()


@pytest.mark.parametrize("source", ["owned", "home", "legacy"])
@pytest.mark.parametrize("device_list", [{}, "invalid", None, ["invalid"], []])
def test_device_list_types_are_validated_before_publishing_inventory(
    client, source, device_list
):
    # An empty list is valid; a malformed list must fail the refresh rather
    # than publish a partial inventory and remove the missing cameras in HA.
    reply = {"resultCode": "1001"}
    if source == "legacy":
        reply["result"] = {"deviceList": device_list}
    else:
        reply["snap"] = device_list
    if source == "home":
        _serve_inventory(
            client, _response({"resultCode": "1001", "ipc": [SHARED_DEVICE]}),
            home_reply=_response(reply),
        )
    else:
        _serve_inventory(client, _response(reply))

    if device_list == []:
        assert [device["serial_number"] for device in client.get_devices()] == ["sn-1"]
    else:
        with pytest.raises(CloudEdgeError, match="Invalid device list"):
            client.get_devices()


def test_owned_device_endpoint_gone_without_home_devices_fails_the_refresh(client):
    """With nothing listed by the homes, a missing owned endpoint is an
    error, not an empty inventory."""
    _serve_inventory(client, _response({}, 404), home_devices=())

    with pytest.raises(CloudEdgeError):
        client.get_devices()


def test_owned_device_failure_raises_when_nothing_else_was_found(client):
    client.session_data = {"userToken": "token"}
    client.get_homes = Mock(return_value=[])
    client._get_owned_devices = Mock(side_effect=NetworkError("gone"))

    with pytest.raises(NetworkError):
        client.get_devices()


@pytest.mark.parametrize("names, query", [
    (["Camera", "Camera"], "Camera"),
    (["Camera North", "Camera South"], "Camera"),
])
def test_ambiguous_device_names_do_not_control_arbitrary_camera(client, names, query):
    from cloudedge import DeviceNotFoundError
    client.get_all_devices = Mock(return_value=[{"name": name} for name in names])
    client.set_device_config = Mock()
    with pytest.raises(DeviceNotFoundError, match="ambiguous"):
        client.set_device_parameter(query, "LED_ENABLE", 1)
    client.set_device_config.assert_not_called()


def test_exact_name_takes_precedence_over_partial_match(client):
    exact = {"name": "Camera", "serial_number": "sn"}
    client.get_all_devices = Mock(return_value=[{"name": "Camera North"}, exact])
    assert client.find_device_by_name("camera") == exact


def test_password_encryption_matches_independent_3des_implementation(client):
    import base64
    from Crypto.Cipher import DES
    from Crypto.Util.Padding import pad
    # The vendor key repeats the same 8-byte block, so EDE reduces to DES.
    cipher = DES.new(b"12345678", DES.MODE_CBC, b"01234567")
    expected = base64.b64encode(cipher.encrypt(pad(b"password", 8))).decode()
    assert client._des_encode("password") == expected
