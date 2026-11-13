# Changelog

## Unreleased

Changes:
- Fix corrupt video frames when a camera abandons a fragmented message: its fragments are no longer joined to the next message.
- Harden the MQTT listener: a second `start()` no longer leaves two connections open, an error in an `on_connect`/`on_disconnect` callback no longer stops push events, and an event callback that raises `TypeError` is no longer called twice.
- Tolerate devices without a name in `find_device_by_name()`, a malformed fps in a stream profile and relayed ICE checks without an IPv4 peer address; restore the signaling timeout after waiting for a camera to wake; deliver frames that arrive together with a camera keepalive without delay.
- A renewal of the OpenAPI credentials that fails (for example on a network error) no longer drops every device's signature, and no longer holds the next renewal back for a minute: one transient error used to fail the other devices' requests as rejected credentials.
- `get_device_status()` and `refresh_mqtt_config()` raise `AuthenticationError` when the server rejects the session (code 1023), as the home and device lists already did.
- Fix the README examples: import `cloudedge`, the package that `pip install pycloudedge` installs.

## 0.1.12

Relayed live streams that negotiated successfully but never delivered video now work: the P2P transport handles the extra XTS relay byte in both directions while retaining strict validation of direct KCP packets.

Changes:
- Fix device discovery for accounts with owned/default homes and shared homes; malformed or failed inventories no longer silently remove devices.
- Improve P2P recovery, signaling frame buffering, camera wake status handling, KCP acknowledgements, and TURN renewal/media handling.
- Refresh MQTT credentials on reconnect and preserve dormant battery-camera status.
- Redact credentials and signed URLs from diagnostics and debug logging.
- Opening a live stream no longer toggles the camera floodlight setting.

Requires Python 3.10 or newer. Live verification covered three cameras, including a relayed H.264 stream at 2304×1296 through Home Assistant. The library regression suite contains 331 passing tests.
