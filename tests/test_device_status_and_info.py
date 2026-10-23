"""Device online state and device info (2026-10-09 review, C108 and C109).

A failed ping must not demote a camera the cloud reports online, and
get_device_info() must read the live configuration shape and agree with
the inventory on ``online``. Only the transport and the ping binary are
stubbed.
"""
import json
from unittest.mock import Mock

import pytest
import requests

from cloudedge import CloudEdgeClient

SERIAL = "ppsl0000000123456789"
LAN_CAMERA_IP = "192.168.50.10"


@pytest.fixture
def client(tmp_path):
    c = CloudEdgeClient(
        "user@example.test", "password", "IT", "+39",
        session_cache_file=str(tmp_path / "session.json"),
    )
    c._detect_local_network = Mock(return_value="192.168.50.0/24")
    return c


def _ping(monkeypatch, *, replies, system="Linux"):
    """Stub the ping binary; returns the command lines it was run with."""
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return Mock(returncode=0 if replies else 1)

    monkeypatch.setattr("cloudedge.client.subprocess.run", run)
    monkeypatch.setattr("platform.system", lambda: system)
    return commands


@pytest.mark.parametrize("cloud_online, replies, expected", [
    (True, False, True),
    (False, True, True),
    (False, False, False),
])
def test_ping_can_only_add_evidence_to_the_cloud_state(
    client, monkeypatch, cloud_online, replies, expected
):
    """No reply proves nothing (sleeping battery cameras answer late or
    never, another site may reuse the same private /24); a reply does show
    the camera is up."""
    _ping(monkeypatch, replies=replies)
    device = {"name": "Garden", "online": cloud_online, "ip_address": LAN_CAMERA_IP}

    assert client.refresh_device_status(device) is expected
    assert device["online"] is expected


@pytest.mark.parametrize("system, flag", [
    ("Darwin", "-t"),
    ("FreeBSD", "-t"),
    ("Linux", "-W"),
])
def test_ping_bounds_its_wait_in_whole_seconds(client, monkeypatch, system, flag):
    """BSD ping reads -W in milliseconds and only after its 1 s send
    interval; -t bounds the whole run in seconds there, -W does on Linux."""
    client.ping_timeout = 1.5
    commands = _ping(monkeypatch, replies=True, system=system)

    client.refresh_device_status(
        {"name": "Garden", "online": False, "ip_address": LAN_CAMERA_IP}
    )

    assert commands[-1] == ["ping", "-c", "1", flag, "2", LAN_CAMERA_IP]


def _response(data):
    response = requests.Response()
    response.status_code = 200
    response.url = "https://api.example.test/endpoint"
    response._content = json.dumps(data).encode()
    return response


@pytest.fixture
def info_client(tmp_path):
    c = CloudEdgeClient(
        "user@example.test", "password", "IT", "+39",
        session_cache_file=str(tmp_path / "session.json"),
        enable_network_ping=False,
    )
    c.BASE_URL = "https://api.example.test"
    c.OPENAPI_BASE_URL = "https://openapi.example.test"
    c.session_data = {
        "userToken": "token", "userID": "123",
        "iotPlatformKeys": {"accessid": "id", "accesskey": "key"},
    }
    return c


def _serve_battery_camera(client, config_reply=None):
    """An account owning one battery camera, listed online, asleep now."""

    def transport(method, url, **kwargs):
        path = url.split("?")[0]
        if path.endswith("/v1/app/home/list"):
            return _response({"resultCode": "1001", "result": {"homes": []}})
        if path.endswith("/ppstrongs/getDevice.action"):
            return _response({"resultCode": "1001", "ipc": [{
                "deviceID": 7, "snNum": SERIAL, "deviceName": "Garden", "onLine": 1,
                "deviceSignature": "device-token", "t": "1",
            }]})
        if path.endswith("/ppstrongs/getDeviceOnLine.action"):
            return _response({"resultCode": "1001", "result": {
                "onLine": 0, "lastOnLineTime": "2026-10-09 20:00:00",
            }})
        assert path.endswith("/openapi/device/config") and config_reply is not None
        return _response(config_reply)

    client._session.request = transport


@pytest.mark.parametrize("config_reply", [
    {"action": "get", "code": 100001, "name": "iot", "iot": {"154": 75}},
    {"result": {"iot": {"154": 75}}},
])
def test_device_info_includes_the_configuration(info_client, config_reply):
    """Live /openapi/device/config replies carry ``iot`` at the top level;
    the older ``result.iot`` shape keeps working."""
    _serve_battery_camera(info_client, config_reply)

    info = info_client.get_device_info("Garden")

    assert info["configuration"]["154"]["value"] == 75


def test_device_info_agrees_with_the_inventory_on_online(info_client):
    """getDeviceOnLine.action reports every sleeping battery camera offline:
    `info` must not contradict `list`, so that flag gets its own key."""
    _serve_battery_camera(info_client)

    info = info_client.get_device_info("Garden", include_config=False)

    assert info["online"] is True
    assert info["legacy_online"] is False
    assert info["last_seen"] == "2026-10-09 20:00:00"
