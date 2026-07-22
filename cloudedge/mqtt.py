"""CloudEdge MQTT event listener.

Subscribes to the Meari/CloudEdge MQTT broker and dispatches push events
(motion detection, camera wake, tamper, etc.) in real-time.

Requires ``paho-mqtt`` (optional dependency)::

    pip install paho-mqtt

Usage::

    from cloudedge import CloudEdgeClient
    from cloudedge.mqtt import CloudEdgeMqttListener

    client = CloudEdgeClient(...)
    client.authenticate()

    def on_event(device_id, event_name, event_type, is_motion):
        print(f"{device_id}: {event_name}  motion={is_motion}")

    listener = CloudEdgeMqttListener(client, on_event=on_event)
    listener.start()       # non-blocking (background thread)
    ...
    listener.stop()
"""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)

ALARM_TYPE_NAMES: Dict[int, str] = {
    1: "PIR",
    2: "Motion",
    3: "Visitor",
    6: "Noise",
    7: "Baby cry",
    8: "Face",
    9: "Call",
    10: "Tamper",
    11: "Human body",
    12: "Face detected",
    14: "Dog bark",
    17: "Cat",
    18: "Pet",
    19: "Package",
    20: "Person",
    21: "SD card removed",
    39: "Fire",
    41: "Cat meow",
}

MOTION_ALARM_TYPES: frozenset[int] = frozenset({1, 2, 11, 20})

OnEventCallback = Callable[[str, str, int, bool], None]
"""Signature: (device_id, event_name, event_type_int, is_motion) -> None"""

OnEventCallbackEx = Callable[[str, str, int, bool, dict], None]
"""Extended signature: (device_id, event_name, event_type_int, is_motion, extra) -> None

``extra`` contains optional fields from the payload such as
``url``, ``alert``, ``deviceName``, ``licenseID``, ``msgDate``.
"""


class CloudEdgeMqttListener:
    """Subscribe to the CloudEdge/Meari MQTT broker for push events.

    Args:
        client: An authenticated :class:`~cloudedge.client.CloudEdgeClient`.
        on_event: Callback invoked for **every** event.
        on_motion: Callback invoked only for motion-related events.
        on_connect: Called when the MQTT connection is established.
        on_disconnect: Called when the MQTT connection drops.
        verify_tls: Verify the broker's TLS certificate (default ``True``).
            Only set ``False`` if your regional broker presents an
            unverifiable certificate — this exposes the connection to MITM.
    """

    def __init__(
        self,
        client: Any,
        *,
        on_event: Optional[OnEventCallback] = None,
        on_motion: Optional[OnEventCallback] = None,
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
        verify_tls: bool = True,
    ) -> None:
        mqtt_cfg = client.get_mqtt_config()
        if not mqtt_cfg:
            raise RuntimeError(
                "MQTT config not available — call client.authenticate() first"
            )

        self._host = mqtt_cfg["mqtt_host"]
        self._port = mqtt_cfg["mqtt_port"]
        self._username = mqtt_cfg["mqtt_access_id"]
        self._password = mqtt_cfg["mqtt_signature"]
        self._user_id = str(mqtt_cfg["user_id"])
        self._topic = (
            f"$bsssvr/iot/{self._user_id}/{self._user_id}/event/update/accepted"
        )

        self.on_event = on_event
        self.on_motion = on_motion
        self._on_connect_cb = on_connect
        self._on_disconnect_cb = on_disconnect

        self._client: Any = None
        self._connected = False
        self._verify_tls = verify_tls

    @property
    def connected(self) -> bool:
        """``True`` if the MQTT connection is active."""
        return self._connected

    @property
    def topic(self) -> str:
        """The MQTT topic being subscribed to."""
        return self._topic

    def start(self) -> bool:
        """Start the MQTT background loop (non-blocking).

        Returns ``True`` if the connection was initiated successfully.
        Raises ``ImportError`` if ``paho-mqtt`` is not installed.
        """
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise ImportError(
                "paho-mqtt is required for MQTT support: pip install paho-mqtt"
            )

        def _on_connect(client, userdata, flags, rc, *args):
            # paho-mqtt v2 passes rc as ReasonCode object, not int
            rc_ok = (rc == 0) if isinstance(rc, int) else rc.is_failure is False
            if rc_ok:
                self._connected = True
                client.subscribe(self._topic, qos=2)
                _LOGGER.info("MQTT connected — topic: %s", self._topic)
                if self._on_connect_cb:
                    self._on_connect_cb()
            else:
                _LOGGER.warning("MQTT connect failed: rc=%s", rc)

        def _on_disconnect(client, userdata, *args):
            self._connected = False
            _LOGGER.debug("MQTT disconnected")
            if self._on_disconnect_cb:
                self._on_disconnect_cb()

        def _on_message(client, userdata, msg):
            try:
                self._dispatch(msg.payload)
            except Exception as exc:
                _LOGGER.debug("MQTT message parse error: %s", exc)

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._user_id,
                clean_session=True,
                protocol=mqtt.MQTTv311,
            )
        except (AttributeError, TypeError):
            client = mqtt.Client(
                client_id=self._user_id,
                clean_session=True,
                protocol=mqtt.MQTTv311,
            )

        client.username_pw_set(self._username, self._password)

        client.tls_set_context(self._build_ssl_context())

        client.on_connect = _on_connect
        client.on_disconnect = _on_disconnect
        client.on_message = _on_message
        client.reconnect_delay_set(min_delay=3, max_delay=60)

        self._client = client

        try:
            client.connect_async(self._host, self._port, keepalive=300)
            client.loop_start()
            return True
        except Exception as exc:
            _LOGGER.error("MQTT connection failed: %s", exc)
            self._connected = False
            return False

    def _build_ssl_context(self) -> ssl.SSLContext:
        """Return the TLS context for the broker connection.

        Certificate verification is ON by default: the broker credentials
        travel over this connection and unverified TLS allows MITM. If a
        regional broker really presents an unverifiable certificate,
        construct the listener with ``verify_tls=False`` (a warning is
        logged); there is no silent automatic downgrade.
        """
        ctx = ssl.create_default_context()
        if not self._verify_tls:
            _LOGGER.warning(
                "MQTT TLS certificate verification DISABLED for %s:%s — "
                "connection is exposed to man-in-the-middle attacks",
                self._host, self._port,
            )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def stop(self) -> None:
        """Stop the MQTT background loop and disconnect."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
            self._connected = False

    def _dispatch(self, payload: bytes) -> None:
        """Parse an MQTT payload and invoke callbacks."""
        try:
            data: dict = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        _LOGGER.info("MQTT raw payload: %s", json.dumps(data, default=str)[:2000])

        original = data

        # Unwrap nested envelope — walk into nested dicts looking for the event
        # Real payload: { "event": "alarm", "params": { "result": { "evt": "1", "deviceID": "...", ... } } }
        for key in ("params", "result", "data", "state", "reported"):
            if key in data and isinstance(data[key], dict):
                data = data[key]
        if "msg" in data and isinstance(data["msg"], dict):
            data = data["msg"]

        for key in ("alarm", "alarmInfo"):
            if key in data and isinstance(data[key], dict):
                data = data[key]

        evt_raw = (
            data.get("evt")
            or data.get("eventType")
            or data.get("alarmType")
            or data.get("type")
            or ""
        )
        device_id = str(
            data.get("deviceID")
            or data.get("deviceId")
            or data.get("devID")
            or data.get("did")
            or original.get("state", {}).get("reported", {}).get("deviceID", "")
            or ""
        )

        try:
            evt_int = int(evt_raw)
        except (ValueError, TypeError):
            evt_int = -1

        evt_name = ALARM_TYPE_NAMES.get(evt_int, f"type={evt_raw}")
        is_motion = evt_int in MOTION_ALARM_TYPES

        extra: dict = {}
        for field in ("url", "alert", "deviceName", "licenseID", "msgDate", "title"):
            val = data.get(field)
            if val:
                extra[field] = str(val)

        _LOGGER.info(
            "MQTT event: %s  device=%s  motion=%s  url=%s",
            evt_name, device_id, is_motion, bool(extra.get("url")),
        )

        if self.on_event:
            try:
                self.on_event(device_id, evt_name, evt_int, is_motion, extra)
            except TypeError:
                try:
                    self.on_event(device_id, evt_name, evt_int, is_motion)
                except Exception as exc:
                    _LOGGER.debug("on_event callback error: %s", exc)
            except Exception as exc:
                _LOGGER.debug("on_event callback error: %s", exc)

        if is_motion and self.on_motion:
            try:
                self.on_motion(device_id, evt_name, evt_int, is_motion, extra)
            except TypeError:
                try:
                    self.on_motion(device_id, evt_name, evt_int, is_motion)
                except Exception as exc:
                    _LOGGER.debug("on_motion callback error: %s", exc)
            except Exception as exc:
                _LOGGER.debug("on_motion callback error: %s", exc)
