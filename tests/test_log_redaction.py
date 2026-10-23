"""Debug logs carry no device secrets or signed URLs (2026-10-09 review,
C33 and C34): users attach them to public issues."""
import json
import logging
from unittest.mock import Mock

import pytest
import requests

from cloudedge import CloudEdgeClient
from cloudedge.image_decrypt import decrypt_jpgx3_from_url

DEVICE_SECRETS = {
    "hostKey": "HOSTKEY-SECRET",
    "hostKey1": "HOSTKEY1-SECRET",
    "deviceSignature": "DEVSIG-SECRET",
    "p2pInit": "P2PINIT-SECRET",
    "deviceP2P": "DEVP2P-SECRET",
    "shareAccessSign": "SHARESIGN-SECRET",
    "relayLicenseID": "RELAY-SECRET",
}


def _response(data):
    response = requests.Response()
    response.status_code = 200
    response.url = "https://api.example.test/endpoint"
    response._content = json.dumps(data).encode()
    return response


def test_debug_mode_logs_the_device_list_shape_not_its_secrets(tmp_path, caplog):
    client = CloudEdgeClient(
        "user@example.test", "password", "IT", "+39",
        session_cache_file=str(tmp_path / "session.json"),
        debug=True, enable_network_ping=False,
    )
    client.BASE_URL = "https://api.example.test"
    client.session_data = {"userToken": "token", "userID": "123"}

    def transport(method, url, **kwargs):
        if url.split("?")[0].endswith("/v1/app/home/list"):
            return _response({"resultCode": "1001", "result": {"homes": []}})
        return _response({"resultCode": "1001", "ipc": [{
            "deviceID": 7, "snNum": "ppsl0000000123456789", "deviceName": "Garden",
            "onLine": 1, "t": "1", **DEVICE_SECRETS,
        }]})

    client._session.request = transport
    caplog.set_level(logging.DEBUG)

    devices = client.get_devices()

    assert devices[0]["host_key"] == "HOSTKEY-SECRET"
    assert "API Response structure" in caplog.text
    leaked = [secret for secret in DEVICE_SECRETS.values() if secret in caplog.text]
    assert leaked == []


SIGNED_URL = "https://alarm.example.test/a.jpgx3?OSSAccessKeyId=K&Signature=SIG-SECRET%3D"


def _http_error_session():
    response = requests.Response()
    response.status_code = 403
    response.reason = "Forbidden"
    response.url = SIGNED_URL
    return Mock(**{"get.return_value": response})


def _connection_error_session():
    # urllib3 names the request path and query in the failure text.
    error = requests.ConnectionError(
        "HTTPSConnectionPool(host='alarm.example.test', port=443): Max retries "
        "exceeded with url: /a.jpgx3?OSSAccessKeyId=K&Signature=SIG-SECRET%3D",
        request=requests.Request("GET", SIGNED_URL).prepare(),
    )
    return Mock(**{"get.side_effect": error})


@pytest.mark.parametrize("session_factory", [_http_error_session, _connection_error_session])
def test_alarm_image_download_failures_log_no_signed_url(caplog, session_factory):
    """The snapshot query is a bearer credential for the image."""
    with caplog.at_level(logging.DEBUG, logger="cloudedge.image_decrypt"):
        image = decrypt_jpgx3_from_url(SIGNED_URL, "ppsl123", session=session_factory())

    assert image is None
    assert "Failed to download alarm image" in caplog.text
    assert "SIG-SECRET" not in caplog.text
