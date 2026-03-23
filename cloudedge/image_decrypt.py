"""Decrypt Meari/CloudEdge ``.jpgx3`` alarm images.

The CloudEdge MQTT push events include a URL to an encrypted JPEG snapshot
captured at the moment of the alarm.  The file uses a simple XOR cipher
derived from the device's serial number (licence ID).

Algorithm (reverse-engineered from ``libmrplayer.so``):

1. Build the seed string ``"{licence_id}|{len(licence_id)}|meari.stream"``.
2. Compute ``MD5(seed)`` and represent it as a **32-char lowercase hex string**.
3. XOR the first ``min(len(data), 1024)`` bytes of the encrypted file with
   the hex string, cycling every 32 bytes.  Bytes beyond 1024 are unchanged.

The companion ``meari.key`` variant (same pattern but with ``"meari.key"``
instead of ``"meari.stream"``) produces the hash embedded in the filename,
which can be used to verify that the correct licence ID was used.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_XOR_LIMIT = 1024


def _meari_md5(licence_id: str, suffix: str) -> str:
    """Return ``MD5("{licence_id}|{len}|{suffix}")`` as a 32-char hex string."""
    seed = f"{licence_id}|{len(licence_id)}|{suffix}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def verify_licence_for_url(url: str, licence_id: str) -> bool:
    """Check whether *licence_id* matches the hash embedded in *url*."""
    expected = _meari_md5(licence_id, "meari.key")
    return expected in url


def decrypt_jpgx3(data: bytes, licence_id: str) -> bytes:
    """Decrypt a ``.jpgx3`` encrypted JPEG image.

    Args:
        data: Raw (encrypted) bytes downloaded from the alarm URL.
        licence_id: The device serial number / licence ID
                    (e.g. ``"ppsl6b9baee1884c4a9b"``).

    Returns:
        Decrypted JPEG bytes.
    """
    key_hex = _meari_md5(licence_id, "meari.stream").encode("ascii")  # 32 bytes
    xor_len = min(len(data), _XOR_LIMIT)

    out = bytearray(data)
    for i in range(xor_len):
        out[i] ^= key_hex[i % 32]

    return bytes(out)


def decrypt_jpgx3_from_url(
    url: str,
    licence_id: str,
    *,
    timeout: int = 15,
    session=None,
) -> Optional[bytes]:
    """Download and decrypt a ``.jpgx3`` alarm image.

    Args:
        url: The full ``.jpgx3`` URL from the MQTT alarm payload.
        licence_id: Device serial number / licence ID.
        timeout: HTTP request timeout in seconds.
        session: Optional ``requests.Session`` to reuse.

    Returns:
        Decrypted JPEG bytes, or ``None`` on failure.
    """
    import requests

    try:
        r = (session or requests).get(url, timeout=timeout)
        r.raise_for_status()
        if len(r.content) < 100:
            _LOGGER.debug("Alarm image too small (%d bytes)", len(r.content))
            return None
    except Exception as exc:
        _LOGGER.debug("Failed to download alarm image: %s", exc)
        return None

    if ".jpgx3" in url or ".jpgx2" in url:
        return decrypt_jpgx3(r.content, licence_id)

    return r.content
