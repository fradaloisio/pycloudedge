"""Discover the regional Meari MsgSvr signaling cluster."""

from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any
from urllib.parse import urlparse

from .meari_signaling import (
    CMD_STATUS,
    MAGIC,
    METHOD_DIRECT,
    NODE_CLIENT,
    TAIL,
    TYPE_DEFAULT,
    _des3_decrypt,
    _des3_encrypt,
)

_LOGGER = logging.getLogger(__name__)

ROOT_DISCOVERY_PORT = 9253
ROOT_DISCOVERY_VERSION = 15259
DEFAULT_PLATFORM_DOMAINS = (
    "euce.mearicloud.com",
    "usce.mearicloud.com",
    "asce.mearicloud.com",
    "cnce.mearicloud.com",
)


def _extract_host(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        return raw
    try:
        return (urlparse(raw).hostname or "").lower()
    except ValueError:
        return ""


def _build_discovery_frame(payload: dict[str, Any]) -> bytes:
    """Build the native UDP root-discovery frame.

    Root discovery uses the same encrypted MsgSvr envelope as TCP signaling,
    but the native client emits the additive payload checksum here.
    """
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encrypted = _des3_encrypt(encoded)
    payload_len = len(encrypted)
    header = bytes(
        (
            MAGIC,
            NODE_CLIENT,
            METHOD_DIRECT,
            CMD_STATUS,
            TYPE_DEFAULT,
            MAGIC,
            payload_len & 0xFF,
            ((payload_len >> 8) & 0x7F) | 0x80,
        )
    )
    return header + encrypted + bytes((sum(encrypted) & 0xFF, TAIL))


def _parse_discovery_frame(data: bytes) -> dict[str, Any] | None:
    if len(data) < 10 or data[0] != MAGIC or data[5] != MAGIC:
        return None
    payload_len = data[6] | ((data[7] & 0x7F) << 8)
    frame_len = payload_len + 10
    if len(data) < frame_len or data[frame_len - 1] != TAIL:
        return None
    encrypted = data[8 : 8 + payload_len]
    checksum = data[8 + payload_len]
    xor_checksum = 0
    for byte in encrypted:
        xor_checksum ^= byte
    if checksum not in {sum(encrypted) & 0xFF, xor_checksum}:
        return None
    try:
        payload = _des3_decrypt(encrypted) if data[7] & 0x80 else encrypted
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _query_root(
    endpoint: tuple[str, int],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any] | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(_build_discovery_frame(payload), endpoint)
        response, _ = sock.recvfrom(4096)
        return _parse_discovery_frame(response)
    except OSError:
        return None
    finally:
        sock.close()


def _region_code(*hints: Any) -> str:
    for hint in hints:
        host = _extract_host(hint).replace("-", ".")
        for part in host.split("."):
            if part in {"eu", "us", "as", "cn"}:
                return part
            if part in {"euce", "usce", "asce", "cnce"}:
                return part[:2]
    return ""


def _platform_domains(*hints: Any) -> list[str]:
    domains: list[str] = []
    for hint in hints:
        host = _extract_host(hint)
        if host.startswith("openapi-"):
            host = host[len("openapi-") :]
        elif host.startswith("events-"):
            host = host[len("events-") :]
        if host.endswith(".mearicloud.com") and host not in domains:
            domains.append(host)
    for domain in DEFAULT_PLATFORM_DOMAINS:
        if domain not in domains:
            domains.append(domain)
    return domains


def _contact_endpoints(response: dict[str, Any]) -> list[tuple[str, int]]:
    contact = response.get("contact")
    if not isinstance(contact, dict):
        return []
    if str(contact.get("transport") or "tcp").lower() != "tcp":
        return []
    try:
        port = int(contact.get("port") or 0)
    except (TypeError, ValueError):
        return []
    if not 1 <= port <= 65535:
        return []

    hosts: list[str] = []
    contact_ip = _extract_host(contact.get("ip"))
    if contact_ip:
        hosts.append(contact_ip)
    contact_host = _extract_host(
        response.get("host") or contact.get("host") or contact.get("domain")
    )
    if contact_host:
        try:
            for info in socket.getaddrinfo(contact_host, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in hosts:
                    hosts.append(ip)
        except socket.gaierror:
            pass
    return [(host, port) for host in hosts]


def discover_msgsvr_endpoints(
    *,
    platform_domain_hint: Any = None,
    openapi_server_hint: Any = None,
    api_server_hint: Any = None,
    client_id_hint: Any = None,
    timeout: float = 0.7,
) -> list[tuple[str, int]]:
    """Resolve dynamic TCP MsgSvr endpoints through UDP port 9253."""
    code = _region_code(
        api_server_hint,
        platform_domain_hint,
        openapi_server_hint,
    )
    domains = _platform_domains(platform_domain_hint, openapi_server_hint)
    if code:
        preferred = f"{code}ce.mearicloud.com"
        if preferred in domains:
            domains.remove(preferred)
        domains.insert(0, preferred)

    timestamp = time.time()
    body: dict[str, Any] = {
        "action": "conf",
        "ver": ROOT_DISCOVERY_VERSION,
        "t": f"{int(timestamp)}.{int((timestamp % 1) * 1000)}",
    }
    if client_id_hint:
        body["uuid"] = str(client_id_hint)

    endpoints: list[tuple[str, int]] = []
    for domain in domains:
        try:
            root_ips = [
                info[4][0]
                for info in socket.getaddrinfo(domain, None, socket.AF_INET)
            ]
        except socket.gaierror:
            continue
        for root_ip in dict.fromkeys(root_ips):
            response = _query_root(
                (root_ip, ROOT_DISCOVERY_PORT),
                body,
                timeout,
            )
            if response is None and code:
                response = _query_root(
                    (root_ip, ROOT_DISCOVERY_PORT),
                    {**body, "domain": code},
                    timeout,
                )
            if response:
                for endpoint in _contact_endpoints(response):
                    if endpoint not in endpoints:
                        endpoints.append(endpoint)
            if endpoints:
                _LOGGER.debug(
                    "Root-discovered MsgSvr endpoints via %s: %s",
                    domain,
                    endpoints,
                )
                return endpoints
    return endpoints
