# Changelog

## 0.1.12

Relayed live streams that negotiated successfully but never delivered video now work: the P2P transport handles the extra XTS relay byte in both directions while retaining strict validation of direct KCP packets.

Changes:
- Fix device discovery for accounts with owned/default homes and shared homes; malformed or failed inventories no longer silently remove devices.
- Improve P2P recovery, signaling frame buffering, camera wake status handling, KCP acknowledgements, and TURN renewal/media handling.
- Refresh MQTT credentials on reconnect and preserve dormant battery-camera status.
- Redact credentials and signed URLs from diagnostics and debug logging.
- Opening a live stream no longer toggles the camera floodlight setting.

Requires Python 3.10 or newer. Live verification covered three cameras, including a relayed H.264 stream at 2304×1296 through Home Assistant. The library regression suite contains 331 passing tests.
