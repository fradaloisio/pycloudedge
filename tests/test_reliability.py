"""Regressions for malformed caches and incomplete device discovery."""
import json
import time
from unittest.mock import Mock

import pytest

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
    assert client._load_session_cache() == session


def test_legacy_cache_discovers_missing_endpoints_without_logging_in(client):
    client._save_session_cache({
        "loginTime": time.time(), "userToken": "token", "userID": "id", "mqtt": {"host": "host"},
    })
    def discover():
        client.BASE_URL = "https://api.example.test"
        client.OPENAPI_BASE_URL = "https://openapi.example.test"
    client._discover_endpoints = Mock(side_effect=discover)
    client._session.post = Mock(side_effect=AssertionError("Must reuse the token"))
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


def test_owned_device_failure_keeps_home_inventory(client):
    """A legacy endpoint going away must not take the whole inventory down."""
    client.session_data = {"userToken": "token"}
    client.get_homes = Mock(return_value=[{"home_id": "h", "name": "Home"}])
    client.get_devices_by_home = Mock(return_value=[{"serial_number": "sn-1"}])
    client._get_owned_devices = Mock(side_effect=NetworkError("gone"))

    assert [d["serial_number"] for d in client.get_devices()] == ["sn-1"]


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
