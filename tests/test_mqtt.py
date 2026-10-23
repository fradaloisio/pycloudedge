import json
import logging
import ssl

import pytest

from cloudedge.mqtt import CloudEdgeMqttListener


class _MqttConfigClient:
    def get_mqtt_config(self):
        return {
            "mqtt_host": "events.example.test",
            "mqtt_port": 8883,
            "mqtt_access_id": "access-id",
            "mqtt_signature": "signature",
            "user_id": "user-id",
        }


def test_mqtt_tls_verification_is_enabled_by_default():
    listener = CloudEdgeMqttListener(_MqttConfigClient())

    context = listener._build_ssl_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_mqtt_tls_verification_can_be_explicitly_disabled(caplog):
    listener = CloudEdgeMqttListener(_MqttConfigClient(), verify_tls=False)

    with caplog.at_level(logging.WARNING, logger="cloudedge.mqtt"):
        context = listener._build_ssl_context()

    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
    assert "verification DISABLED" in caplog.text


def test_mqtt_payloads_are_logged_only_at_debug(caplog):
    listener = CloudEdgeMqttListener(_MqttConfigClient())

    with caplog.at_level(logging.DEBUG, logger="cloudedge.mqtt"):
        listener._dispatch(b'{"evt": "2", "deviceID": "camera-id"}')

    mqtt_records = [record for record in caplog.records if record.name == "cloudedge.mqtt"]
    assert mqtt_records
    assert all(record.levelno == logging.DEBUG for record in mqtt_records)


class _FakePahoClient:
    """Stands in for paho's Client: records credentials, never connects."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.credentials = []
        self.on_disconnect = None
        self.keepalive = None
        self.reconnect_delay = None
        _FakePahoClient.instances.append(self)

    def username_pw_set(self, username, password=None):
        self.credentials.append((username, password))

    def tls_set_context(self, context):
        pass

    def enable_logger(self, logger=None):
        pass

    def reconnect_delay_set(self, **kwargs):
        self.reconnect_delay = kwargs

    def connect_async(self, host, port, keepalive=60):
        self.keepalive = keepalive

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        # paho >= 2 reports a deliberate disconnect through on_disconnect,
        # synchronously once the network thread is stopped.
        self.on_disconnect(self, None, None, 0, None)


class _RenewingConfigClient(_MqttConfigClient):
    """A CloudEdge client whose platform credentials were renewed."""

    def __init__(self):
        self.refreshes = 0

    def refresh_mqtt_config(self):
        self.refreshes += 1
        return {**self.get_mqtt_config(),
                "mqtt_access_id": "renewed-id", "mqtt_signature": "renewed-signature"}


def _started_listener(monkeypatch, client):
    pytest.importorskip("paho.mqtt.client")
    monkeypatch.setattr("paho.mqtt.client.Client", _FakePahoClient)
    _FakePahoClient.instances.clear()
    listener = CloudEdgeMqttListener(client)
    assert listener.start() is True
    return listener, _FakePahoClient.instances[-1]


def test_reconnect_uses_broker_credentials_renewed_by_the_client(monkeypatch):
    """paho reconnects with the last username_pw_set pair: after a drop the
    listener must hand it the credentials the client renewed, or every
    reconnect is refused once the old ones expire."""
    _, paho = _started_listener(monkeypatch, _RenewingConfigClient())

    paho.on_disconnect(paho, None, None, 5, None)  # e.g. CONNACK "not authorized"

    assert paho.credentials == [
        ("access-id", "signature"),
        ("renewed-id", "renewed-signature"),
    ]


def test_listener_pings_before_the_broker_cutoff_and_reconnects_quickly(monkeypatch):
    """The EU broker ends every connection ~300 s after CONNECT and the app
    pings after 120 s (C115): paho must send a PINGREQ well before the
    cutoff, and retry a dropped connection after 1 s so the gap in push
    events stays short."""
    _, paho = _started_listener(monkeypatch, _MqttConfigClient())

    assert 0 < paho.keepalive <= 120
    assert paho.reconnect_delay["min_delay"] <= 1


@pytest.mark.parametrize("callback_api", ["paho2", "paho1"])
def test_disconnect_reason_is_logged(monkeypatch, caplog, callback_api):
    """Why the broker drops the connection is not known yet (C115): the
    reason paho reports must reach the log at INFO."""
    listener, paho = _started_listener(monkeypatch, _MqttConfigClient())
    if callback_api == "paho2":
        from paho.mqtt.client import DisconnectFlags
        from paho.mqtt.packettypes import PacketTypes
        from paho.mqtt.reasoncodes import ReasonCode

        # CallbackAPIVersion.VERSION2: flags, reason code, properties.
        args = (
            DisconnectFlags(is_disconnect_packet_from_server=False),
            ReasonCode(PacketTypes.DISCONNECT, "Keep alive timeout"),
            None,
        )
        expected = "Keep alive timeout"
    else:
        args = (7,)  # paho 1.x: the bare MQTT_ERR_CONN_LOST code.
        expected = "reason=7"

    with caplog.at_level(logging.INFO, logger="cloudedge.mqtt"):
        paho.on_disconnect(paho, None, *args)

    assert listener.connected is False
    assert any(
        record.levelno == logging.INFO and expected in record.getMessage()
        for record in caplog.records
        if record.name == "cloudedge.mqtt"
    )


def test_stopping_the_listener_does_not_renew_credentials(monkeypatch):
    """A deliberate disconnect needs no renewal: it would run a platform
    fetch (and take the client lock) from the thread stopping the listener."""
    client = _RenewingConfigClient()
    listener, paho = _started_listener(monkeypatch, client)

    listener.stop()

    assert client.refreshes == 0
    assert paho.credentials == [("access-id", "signature")]


def test_mqtt_debug_logs_omit_signed_snapshot_urls(caplog):
    """The alarm snapshot URL is a bearer link: debug logs, which users
    attach to issues, must not carry its signed query."""
    events = []
    listener = CloudEdgeMqttListener(
        _MqttConfigClient(), on_event=lambda *args: events.append(args)
    )
    url = "https://alarm.example.test/a.jpgx3?OSSAccessKeyId=K&Signature=SIG-SECRET%3D"
    payload = {"event": "alarm", "params": {"result": {
        "evt": "2", "deviceID": "camera-id", "url": url,
    }}}

    with caplog.at_level(logging.DEBUG, logger="cloudedge.mqtt"):
        listener._dispatch(json.dumps(payload).encode())

    assert events and events[0][4]["url"] == url  # still delivered to callbacks
    assert "SIG-SECRET" not in caplog.text
