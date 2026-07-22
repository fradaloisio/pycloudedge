import logging
import ssl

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
