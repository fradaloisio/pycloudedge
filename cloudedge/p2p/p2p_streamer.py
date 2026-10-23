"""P2P streaming engine for CloudPlus / Meari cameras.

Ported from main.py — handles signaling, TURN, ICE, KCP, VVP, and
stream decryption. Designed for use by the HA coordinator.

Usage:
    streamer = P2PStreamer(api, device, on_video=cb, on_audio=cb)
    streamer.run_session()   # blocking — call from a thread
    streamer.request_stop()  # from another thread
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import select
import socket
import struct
import time
import traceback
from collections import deque
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from Crypto.Cipher import DES3

from .meari_signaling import MsgSvrClient
from .root_discovery import discover_msgsvr_endpoints
from .turn_client import (
    TurnClient,
    TurnRenewalRefusedError,
    _build_xts_ice_binding_request,
    _check_integrity,
    _parse_stun,
    _build_stun,
    _decode_xor_address,
    BINDING_INDICATION,
    BINDING_REQUEST,
    BINDING_RESPONSE,
    DATA_INDICATION,
    ATTR_DATA,
    ATTR_XOR_PEER_ADDRESS,
)
from .kcp_tunnel import KcpTunnel, parse_kcp_segments, parse_iva_frame
from ..client import CloudEdgeClient
from ..stream_profiles import select_default_live_stream_id

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VVP (PPStrong Video Protocol) constants
# ---------------------------------------------------------------------------
VVP_MAGIC = 0x56565099
VVP_CMD_START_LIVE = 0x11FF
VVP_CMD_STOP = 0x12FF
VVP_CMD_HEARTBEAT = 0x888E
VVP_HEADER_SIZE = 60

# Stream frame types
STREAM_TYPE_INFO = 0xF9
STREAM_TYPE_AUDIO = 0xFA
STREAM_TYPE_IFRAME = 0xFC
STREAM_TYPE_PFRAME = 0xFD
STREAM_TYPE_PHOTO = 0xFE

# 3DES key for stream decryption
STREAM_ENCRYPT_KEY = b"!mearicloud2.0!"
_SIGNAL_STATUS_RETRY_WINDOW = 20.0
_SIGNAL_STATUS_RETRY_POLL = 1.0
_KCP_GAP_SKIP_DELAY = 5.0
_VIDEO_STALL_RESTART_DELAY = 8.0
_VIDEO_STALL_RESTART_INTERVAL = 10.0
# The Android client keeps the P2P session alive across observed 15-33 second
# pauses. Allow the same recovery window before falling back to a reconnect.
_VIDEO_STALL_END_SESSION = 35.0
_FIRST_VIDEO_TIMEOUT = 30.0
_KCP_RETRANSMIT_INTERVAL = 2.0
# Session keepalives of the vendor app (traffic.pcap, app 6.2.8): a msgsvr
# heartbeat every register-reply "hb" seconds (30), a routed webrtcsvr hello
# about every 21 s and, once ICE nominated a direct peer, a bare STUN Binding
# Indication to it 10 s later and then every 30 s (the camera does the same).
_KEEPALIVE_ENV = "CLOUDEDGE_P2P_KEEPALIVE"
_MSGSVR_HEARTBEAT_INTERVAL = 30.0
_MSGSVR_HELLO_INTERVAL = 21.0
_MSGSVR_DRAIN_INTERVAL = 1.0
_ICE_KEEPALIVE_FIRST_DELAY = 10.0
_ICE_KEEPALIVE_INTERVAL = 30.0
# TURN renewal cadence: re-check every minute while media is direct, retry a
# failed renewal attempt after 5 s.
_TURN_RENEWAL_DEFER = 60.0
_TURN_RENEWAL_RETRY = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_licence_id(sn: str) -> str:
    if not sn:
        return ""
    if len(sn) == 9:
        return "00000000000" + sn
    return sn


def format_sn(sn: str) -> str:
    """Convert snNum to IoT device UUID."""
    if not sn:
        return ""
    if len(sn) == 9:
        return "0000000" + sn
    return sn[4:]


def _keepalive_enabled_from_env() -> bool:
    """Session keepalives stay on unless CLOUDEDGE_P2P_KEEPALIVE disables them."""
    value = os.environ.get(_KEEPALIVE_ENV, "").strip().lower()
    return value not in ("0", "false", "no", "off")


class _SessionKeepalive:
    """Keepalives the vendor app sends while a live view is open (C93, C87).

    Driven from the streaming loops' timers: the msgsvr registration
    heartbeat and the routed hello go out on the signaling connection, whose
    replies are then read without blocking (status pushes stay queued on the
    client); the STUN Binding Indication goes to the nominated direct ICE
    peer.  A signaling failure only stops the signaling keepalives: the media
    session never depends on them.
    """

    def __init__(
        self, sig: Any, *, registered_at: float, heartbeat_interval: Any = None
    ) -> None:
        try:
            interval = float(heartbeat_interval)
        except (TypeError, ValueError):
            interval = _MSGSVR_HEARTBEAT_INTERVAL
        if interval <= 0:
            interval = _MSGSVR_HEARTBEAT_INTERVAL
        self._sig = sig
        self._registered_at = registered_at
        self._heartbeat_interval = interval
        self._heartbeat_at = registered_at + interval
        self._hello_at = registered_at + _MSGSVR_HELLO_INTERVAL
        self._drain_at = registered_at
        self._signaling_alive = sig is not None
        self._ice_keepalive_at: float | None = None

    def tick(
        self,
        now: float,
        turn: Any,
        confirmed_peer: tuple[str, int, bool] | None,
    ) -> None:
        if self._signaling_alive:
            try:
                if now >= self._heartbeat_at:
                    self._sig.send_heartbeat(runtime=now - self._registered_at)
                    self._heartbeat_at = now + self._heartbeat_interval
                if now >= self._hello_at:
                    self._sig.send_hello()
                    self._hello_at = now + _MSGSVR_HELLO_INTERVAL
                if now >= self._drain_at:
                    self._sig.drain_frames()
                    self._drain_at = now + _MSGSVR_DRAIN_INTERVAL
            except Exception as exc:
                self._signaling_alive = False
                _LOGGER.debug("msgsvr keepalives stopped: %s", exc)

        if confirmed_peer is None or not confirmed_peer[2]:
            return
        if self._ice_keepalive_at is None:
            self._ice_keepalive_at = now + _ICE_KEEPALIVE_FIRST_DELAY
        elif now >= self._ice_keepalive_at:
            indication, _ = _build_stun(BINDING_INDICATION, b"")
            try:
                turn.peer_sock.sendto(indication, confirmed_peer[:2])
            except OSError:
                pass
            self._ice_keepalive_at = now + _ICE_KEEPALIVE_INTERVAL


def _dormancy_reported(sig: Any, device_uuid: str) -> bool:
    """Whether the latest msgsvr status for this camera is dormancy (C95).

    A logout from the previous session can still put a battery camera to
    sleep after it answered this offer: dormancy follows a logout by
    1.3-4.7 s (app capture: one push came 235 ms after the answer).  Status
    pushes are queued in ``sig.pending_status`` by the request reads and,
    while the session keepalives run, by ``drain_frames()``.
    """
    # A wake can overtake the previous session's dormancy push. Preserve the
    # queue for other status consumers, but never let an obsolete state
    # cancel an attempt after the camera has reported itself online again.
    for event in reversed(getattr(sig, "pending_status", ())):
        if event.get("uuid") == device_uuid and event.get("status"):
            return event["status"] == "dormancy"
    return False


def _is_private_ip(ip: str) -> bool:
    try:
        parts = ip.split(".")
        if parts[0] == "10":
            return True
        if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            return True
        if parts[0] == "192" and parts[1] == "168":
            return True
        if parts[0] == "127":
            return True
    except (IndexError, ValueError):
        pass
    return False


def _is_direct_peer_path(
    *, remote: bool, via_turn: bool, peer_ip: str, turn_server_ip: str
) -> bool:
    """Return whether a packet arrived directly from a nominated ICE peer."""
    return not remote and not via_turn and peer_ip != turn_server_ip


def _next_turn_renewal(
    turn: TurnClient,
    confirmed_peer: tuple[str, int, bool] | None,
    now: float,
) -> float:
    """Run the TURN renewals that are due and return the next deadline.

    Direct media (C83): the vendor app releases TURN within seconds of the
    first direct KCP.  Renewals are deferred instead, without deallocating
    (the allocation simply expires on the server), so a TURN-only refusal or
    outage cannot end a healthy direct session.  Relayed or not yet
    confirmed media keeps renewing; the next deadline counts from after
    renew_due() returns (C84), so transactions that blocked for seconds do
    not leave the next renewal already overdue.  TurnRenewalRefusedError
    propagates: a refused renewal ends relay sessions only.
    """
    if confirmed_peer is not None and confirmed_peer[2]:
        return now + _TURN_RENEWAL_DEFER
    try:
        delay = turn.renew_due()
    except TurnRenewalRefusedError:
        raise
    except Exception:
        delay = _TURN_RENEWAL_RETRY
    return time.monotonic() + delay


def _send_kcp_datagram(
    turn: TurnClient,
    data: bytes,
    *,
    target_addr: tuple[str, int],
    confirmed_peer: tuple[str, int, bool] | None,
) -> None:
    """Send KCP on the bootstrap or confirmed path, with XTS's relay trailer.

    Direct media has no trailer. Relay peers strip one byte before their KCP
    parser; omitting it truncates the handshake and they never acknowledge it.
    """
    if confirmed_peer is None:
        turn.send_to_peer(*target_addr, data + b"\x00")
        return

    peer_ip, peer_port, is_direct = confirmed_peer
    if is_direct:
        turn.peer_sock.sendto(data, (peer_ip, peer_port))
    else:
        turn.send_to_peer(peer_ip, peer_port, data + b"\x00")


def _strip_relay_kcp_trailer(data: bytes) -> bytes:
    """Remove XTS's extra relay byte only after a complete KCP datagram.

    The Android relay capture includes this byte inside TURN's data length,
    distinct from TURN alignment padding. Its value is not always zero.
    Direct KCP and malformed datagrams must retain strict KCP validation.
    """
    if len(data) > 24 and parse_kcp_segments(data[:-1]):
        return data[:-1]
    return data


def _build_xts_sdp_offer(
    *,
    ice_ufrag: str,
    ice_pwd: str,
    local_ips: list[str],
    peer_local_port: int,
    peer_mapped_ip: str | None,
    peer_mapped_port: int | None,
    relay_ip: str,
    relay_port: int,
    remote: bool,
) -> str:
    """Build the SDP shape emitted by the Android XTS media client."""
    lines = [
        "n=0 0 0 0 0",
        "a=transport:auto",
        f"a=ice-ufrag:{ice_ufrag}",
        f"a=ice-pwd:{ice_pwd}",
        f"m=audio {relay_port} RTP / AVP 0",
        f"c=IN IP4 {relay_ip}",
    ]
    if not remote:
        for local_ip in local_ips:
            foundation = format(int.from_bytes(socket.inet_aton(local_ip), "big"), "x")
            lines.append(
                f"a=candidate:H{foundation} 1 UDP 1694498815 "
                f"{local_ip} {peer_local_port} typ host"
            )
        if peer_mapped_ip and peer_mapped_port:
            foundation_ip = local_ips[0] if local_ips else "0.0.0.0"
            foundation = format(
                int.from_bytes(socket.inet_aton(foundation_ip), "big"), "x"
            )
            lines.append(
                f"a=candidate:S{foundation} 1 UDP 1862270975 "
                f"{peer_mapped_ip} {peer_mapped_port} typ srflx"
            )
    return "\n".join(lines) + "\n"


def _recv_udp_batch(turn, timeout: float, limit: int = 2000):
    """Receive pending packets from TURN and direct-media sockets."""
    sockets = [turn.sock]
    if turn.peer_sock is not None:
        sockets.append(turn.peer_sock)
    packets = [(raw, addr, True) for raw, addr in turn.drain_pending_datagrams(limit)]
    if len(packets) >= limit:
        return packets
    ready, _, _ = select.select(sockets, [], [], 0 if packets else timeout)
    for recv_sock in ready:
        if len(packets) >= limit:
            break
        previous_timeout = recv_sock.gettimeout()
        recv_sock.setblocking(False)
        try:
            for _ in range(limit - len(packets)):
                try:
                    raw, addr = recv_sock.recvfrom(65536)
                except (BlockingIOError, OSError):
                    break
                packets.append((raw, addr, recv_sock is turn.sock))
        finally:
            recv_sock.settimeout(previous_timeout)
    return packets


def _get_local_ips() -> list[str]:
    # Behind a NAT'd container runtime (Colima/Docker Desktop on macOS, some
    # podman/k8s setups) the interface IP is a VM-internal address that the
    # camera cannot route to, while outbound packets leave with the host's LAN
    # IP. Cameras run a strict ICE source check and silently drop traffic from
    # addresses that were not advertised in the SDP offer, so the direct path
    # never confirms and the session dies on the relay bootstrap. Advertising
    # the effective source IP fixes LAN-direct streaming from those hosts.
    advertise = os.environ.get("CLOUDEDGE_P2P_ADVERTISE_IP", "").strip()
    if advertise:
        return [part.strip() for part in advertise.split(",") if part.strip()]
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            ips.append("0.0.0.0")
    return ips


def _resolve_signaling_candidates(
    api: CloudEdgeClient,
    device: dict[str, Any] | None = None,
) -> list[tuple[str, int]]:
    candidates: list[tuple[str, int]] = []

    openapi_base = getattr(api, "OPENAPI_BASE_URL", "")
    mqtt_host = ((getattr(api, "session_data", None) or {}).get("mqtt") or {}).get(
        "mqtt_host", ""
    )
    client_id = (getattr(api, "session_data", None) or {}).get("userID")
    discovered = discover_msgsvr_endpoints(
        platform_domain_hint=mqtt_host,
        openapi_server_hint=openapi_base,
        api_server_hint=getattr(api, "BASE_URL", ""),
        client_id_hint=client_id,
    )
    candidates.extend(discovered)

    def add_region_host(host: str) -> None:
        if not host:
            return
        candidates.append((host, 28974))
        try:
            resolved_ip = socket.gethostbyname(host)
        except socket.gaierror:
            return
        candidates.append((resolved_ip, 28974))

    def add_device_region_hosts() -> None:
        if not device:
            return
        region = str(device.get("device_region") or "").lower()
        icon_url = str(device.get("device_icon_url") or "").lower()
        if "europe/" in region or "meari-eu" in icon_url or "oss-eu" in icon_url:
            add_region_host("euce.mearicloud.com")
        elif "america/" in region:
            add_region_host("usce.mearicloud.com")
        elif "australia/" in region or "asia/" in region or "pacific/" in region:
            add_region_host("usce.mearicloud.com")

    # The signaling port is dynamic: the region hosts below use the legacy
    # fixed port 28974, which is routinely refused/filtered nowadays. Each
    # dead candidate burns ~8-10s per retry loop, so only fall back to the
    # static list when dynamic discovery produced nothing.
    if not discovered:
        openapi_host = urlparse(openapi_base).hostname or ""
        if openapi_host.startswith("openapi-") and openapi_host.endswith(
            ".mearicloud.com"
        ):
            add_region_host(openapi_host[len("openapi-") :])

        if (
            isinstance(mqtt_host, str)
            and mqtt_host.startswith("events-")
            and mqtt_host.endswith(".mearicloud.com")
        ):
            add_region_host(mqtt_host[len("events-") :])

        region_hosts = {
            "eu": "euce.mearicloud.com",
            "us": "usce.mearicloud.com",
            "ap": "usce.mearicloud.com",
        }
        add_region_host(region_hosts.get(getattr(api, "region", ""), ""))

        add_device_region_hosts()

        add_region_host("euce.mearicloud.com")
        candidates.append(("47.254.142.96", 28974))

    unique_candidates: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)
    return unique_candidates


# ---------------------------------------------------------------------------
# VVP packet building
# ---------------------------------------------------------------------------


def build_vvp_auth_md5(
    host_key: str,
    seq: int,
    cmd: int,
    param: int,
    licence_id: str | None = None,
    auth_flag: int = 0,
) -> str:
    password = host_key if auth_flag == 1 else host_key[:16]
    parts = [
        "admin",
        password,
        str(VVP_MAGIC),
        str(seq),
        str(cmd),
        str(param),
        "meari.p2p.ppcs",
    ]
    if licence_id:
        parts.append(licence_id)
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def build_vvp_packet(
    cmd: int,
    seq: int,
    host_key: str,
    param: int = 8,
    channel: int = 0,
    video_id: int = 0,
    quality: int = 0,
    licence_id: str | None = None,
    auth_flag: int = 0,
) -> bytes:
    auth = build_vvp_auth_md5(host_key, seq, cmd, param, licence_id, auth_flag)
    pkt = bytearray(VVP_HEADER_SIZE)
    struct.pack_into(">I", pkt, 0x00, VVP_MAGIC)
    struct.pack_into(">I", pkt, 0x04, 1)
    struct.pack_into(">I", pkt, 0x08, seq)
    struct.pack_into(">I", pkt, 0x0C, cmd)
    pkt[0x10:0x30] = auth.encode("ascii")
    struct.pack_into(">I", pkt, 0x30, param)
    struct.pack_into("<I", pkt, 0x34, channel)
    pkt[0x38] = video_id & 0xFF
    # The Android XTS client leaves the byte following the video id cleared.
    # Some cameras accept 1 here, but stricter firmware acknowledges the KCP
    # segment without accepting the VVP login command.
    pkt[0x39] = 0x00
    pkt[0x3A] = quality & 0xFF
    pkt[0x3B] = 0x00
    return bytes(pkt)


# ---------------------------------------------------------------------------
# Stream frame decryption + parsing
# ---------------------------------------------------------------------------


def _des3_ecb_decrypt_block(data: bytes, key: bytes) -> bytes:
    k = key[:24]
    if len(k) < 24:
        k = k + b"\x00" * (24 - len(k))
    cipher = DES3.new(k, DES3.MODE_ECB)
    return cipher.decrypt(data)


def decrypt_stream_frame(data: bytearray) -> bytearray:
    if len(data) < 4:
        return data
    frame_type = data[3]
    if frame_type == STREAM_TYPE_IFRAME:
        enc_offset, enc_len = 0x30, 0x80
    elif frame_type == STREAM_TYPE_PFRAME:
        enc_offset, enc_len = 0x28, 0x80
    elif frame_type == STREAM_TYPE_AUDIO:
        enc_offset = 0x28
        remaining = len(data) - enc_offset
        enc_len = (remaining // 8) * 8
    else:
        return data
    if enc_len < 8 or len(data) < enc_offset + enc_len:
        return data
    encrypted = bytes(data[enc_offset : enc_offset + enc_len])
    decrypted = _des3_ecb_decrypt_block(encrypted, STREAM_ENCRYPT_KEY)
    data[enc_offset : enc_offset + enc_len] = decrypted
    return data


def parse_stream_frame(data: bytes):
    if len(data) < 8:
        return None
    if data[0] != 0 or data[1] != 0 or data[2] != 1:
        return None
    frame_type = data[3]
    if frame_type == STREAM_TYPE_IFRAME:
        if len(data) < 0x3C:
            return None
        data_len = struct.unpack_from("<I", data, 0x38)[0]
        if data_len > len(data) - 0x3C:
            return None
        payload = data[0x3C : 0x3C + data_len] if data_len > 0 else data[0x3C:]
        return (frame_type, 0x3C, payload)
    elif frame_type == STREAM_TYPE_PFRAME:
        if len(data) < 0x34:
            return None
        data_len = struct.unpack_from("<I", data, 0x30)[0]
        if data_len > len(data) - 0x34:
            return None
        payload = data[0x34 : 0x34 + data_len] if data_len > 0 else data[0x34:]
        return (frame_type, 0x34, payload)
    elif frame_type == STREAM_TYPE_AUDIO:
        if len(data) < 0x34:
            return None
        data_len = struct.unpack_from("<I", data, 0x30)[0]
        if data_len > len(data) - 0x34:
            return None
        payload = data[0x34 : 0x34 + data_len] if data_len > 0 else data[0x34:]
        return (frame_type, 0x34, payload)
    elif frame_type == STREAM_TYPE_INFO:
        if len(data) < 8:
            return None
        data_len = struct.unpack_from("<H", data, 6)[0]
        if data_len > len(data) - 8:
            return None
        payload = data[8 : 8 + data_len] if data_len > 0 else data[8:]
        return (frame_type, 8, payload)
    return None


# ---------------------------------------------------------------------------
# ICE helpers
# ---------------------------------------------------------------------------


def _build_ice_response(
    binding_req: dict, local_ice_pwd: str, peer_ip: str, peer_port: int
) -> bytes:
    """Return the empty Binding Success expected by the XTS ICE peer.

    The Android client does not add XOR-MAPPED-ADDRESS or MESSAGE-INTEGRITY to
    this response.  Matching that wire format is required for the camera to
    nominate the candidate and begin its KCP handshake.
    """
    txn_id = binding_req["txn_id"]
    msg, _ = _build_stun(BINDING_RESPONSE, b"", txn_id)
    return msg


def _send_direct_ice_binding(
    sock,
    peer_ip: str,
    peer_port: int,
    local_ufrag: str,
    remote_ufrag: str,
    remote_pwd: str,
) -> bytes:
    """Send an ICE check on the direct media socket; return its txn id."""
    txn_id = os.urandom(12)
    msg = _build_xts_ice_binding_request(
        local_ufrag, remote_ufrag, remote_pwd, txn_id
    )
    sock.sendto(msg, (peer_ip, peer_port))
    return txn_id


class _MediaSourceFilter:
    """Accept media and ICE datagrams only from expected sources (C80).

    Both P2P sockets are bound to every interface, so without this check any
    host that reaches them could inject KCP (the stream key is a constant)
    or move the confirmed media path to itself.

    * TURN socket: only the TURN server.
    * Direct media socket: the TURN server, the camera's advertised relay
      candidates (exact address) and, as an explicit rule, any port of an
      advertised host or srflx candidate IP or of the camera's WAN IP
      reported by msgsvr: a camera behind a NAT, or one sending from another
      socket, can use a port its candidate does not name.
    * Any other source must prove it is the ICE peer first: a Binding
      Request whose MESSAGE-INTEGRITY verifies with our ice-pwd (as every
      camera check in the app capture does) or a Binding Response to one of
      our own direct checks.  The source is trusted from then on
      (peer-reflexive).

    Everything else is dropped, counted and logged at debug level.
    """

    _MAX_CHECKS = 256
    _MAX_LOGGED_SOURCES = 32

    def __init__(
        self,
        turn: Any,
        candidates: list[dict],
        ice_pwd: str,
        camera_wan_ip: str | None = None,
    ) -> None:
        self._turn_server = (turn.server_ip, turn.server_port)
        self._ice_key = ice_pwd.encode()
        self._trusted_addrs: set[tuple[str, int]] = {self._turn_server}
        self._trusted_ips: set[str] = {camera_wan_ip} if camera_wan_ip else set()
        self._checks: deque = deque(maxlen=self._MAX_CHECKS)
        self._logged_sources: set[tuple[str, int]] = set()
        self.dropped = 0
        for candidate in candidates:
            self.add_candidate(candidate)

    def add_candidate(self, candidate: dict) -> None:
        self._trusted_addrs.add((candidate["ip"], candidate["port"]))
        if candidate.get("type") != "relay":
            self._trusted_ips.add(candidate["ip"])

    def remember_check(self, txn_id: bytes) -> None:
        self._checks.append(txn_id)

    def answers_our_check(self, message: dict) -> bool:
        return message["txn_id"] in self._checks

    def accept(self, raw: bytes, addr: tuple, from_turn_socket: bool) -> bool:
        source = (addr[0], addr[1])
        if from_turn_socket:
            return source == self._turn_server or self._drop(source)
        if source in self._trusted_addrs or source[0] in self._trusted_ips:
            return True
        message = _parse_stun(raw)
        if message is not None and (
            (
                message["type"] == BINDING_REQUEST
                and _check_integrity(raw, self._ice_key)
            )
            or (
                message["type"] == BINDING_RESPONSE
                and self.answers_our_check(message)
            )
        ):
            _LOGGER.debug("ICE peer-reflexive source %s:%s", *source)
            self._trusted_addrs.add(source)
            return True
        return self._drop(source)

    def _drop(self, source: tuple[str, int]) -> bool:
        self.dropped += 1
        if (
            source not in self._logged_sources
            and len(self._logged_sources) < self._MAX_LOGGED_SOURCES
        ):
            self._logged_sources.add(source)
            _LOGGER.debug(
                "Dropped P2P datagram from unexpected source %s:%s "
                "(%d dropped so far)",
                source[0],
                source[1],
                self.dropped,
            )
        return False


# ---------------------------------------------------------------------------
# P2PStreamer — manages one streaming session
# ---------------------------------------------------------------------------


class P2PStreamer:
    """Runs P2P streaming sessions for a CloudPlus camera.

    Callbacks:
      on_video(data: bytes)  — raw HEVC video payload (I/P frame)
      on_audio(data: bytes)  — raw G.711 µ-law audio payload
      on_login()             — called when VVP login succeeds
      on_disconnect()        — called when session ends

    ``manage_stream_switch`` (default ``False``) is a legacy opt-in that writes
    IoT parameter 167 (``FRONT_LIGHT_SWITCH``, the camera's front light /
    floodlight command) to 1 before a session and to 0 after it. Streaming does
    not need it: the vendor app never writes 167 to start live view, and the
    cameras stream the same live window without it. Enabling it turns the light
    on and then forces it off, overwriting the user's own light setting.

    ``keepalive`` sends the session keepalives of the vendor app: the msgsvr
    registration heartbeat (cmd 0xC9, every register-reply ``hb`` seconds),
    a routed webrtcsvr hello about every 21 s and, after ICE nomination, a
    STUN Binding Indication to the direct peer every 30 s.  When not given
    it is on unless the ``CLOUDEDGE_P2P_KEEPALIVE`` environment variable is
    ``0``/``false``/``no``/``off``, so live A/B tests can switch it off
    without code changes.
    """

    def __init__(
        self,
        api: CloudEdgeClient,
        device: dict[str, Any],
        *,
        on_video: Callable[[bytes], None] | None = None,
        on_audio: Callable[[bytes], None] | None = None,
        on_login: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        remote: bool = False,
        video_id: int | None = None,
        manage_stream_switch: bool = False,
        keepalive: bool | None = None,
    ) -> None:
        self._api = api
        self._device = device
        self._sn_num = device.get("serial_number", "")
        self._device_uuid = device.get("device_uuid") or format_sn(self._sn_num)
        self._host_key = device.get("host_key", "")
        self._dev_name = device.get("name", "Camera")

        self.on_video = on_video
        self.on_audio = on_audio
        self.on_login = on_login
        self.on_disconnect = on_disconnect
        self._remote = remote
        self._video_id_auto = video_id is None
        if video_id is None:
            video_id = select_default_live_stream_id(device)
        if not 0 <= video_id <= 0xFF:
            raise ValueError("video_id must be between 0 and 255")
        self._video_id = video_id
        self._manage_stream_switch = manage_stream_switch
        self._keepalive = (
            _keepalive_enabled_from_env() if keepalive is None else bool(keepalive)
        )

        self._running = False
        self._video_count = 0
        self._total_bytes = 0
        self._cloudedge_native_signaling_retry = True
        self._cloudedge_native_region_signaling = True
        self._active_vvp_session: dict[str, Any] | None = None

    def _get_vvp_licence_id(self) -> str | None:
        return self._device.get("relay_license_id") or (
            format_licence_id(self._sn_num) if self._sn_num else None
        )

    def request_stop(self) -> None:
        """Request the streaming loop to stop (thread-safe)."""
        self._running = False

    def _remember_vvp_sequence(self, next_sequence: int) -> None:
        if self._active_vvp_session is not None:
            self._active_vvp_session["next_sequence"] = next_sequence

    def _send_active_vvp_command(self, command: int) -> bool:
        """Send a VVP command on the current KCP session, if one exists."""
        session = self._active_vvp_session
        if not session:
            return False
        sequence = session["next_sequence"]
        packet = build_vvp_packet(
            cmd=command,
            seq=sequence,
            host_key=session["host_key"],
            param=8,
            video_id=session["video_id"],
            licence_id=session["licence_id"],
        )
        try:
            session["kcp"].send_iva_data(packet)
        except Exception as exc:
            _LOGGER.debug("Could not send VVP command 0x%04x: %s", command, exc)
            return False
        session["next_sequence"] = sequence + 1
        return True

    def _restart_active_vvp_stream(self) -> bool:
        """Restart live delivery without tearing down ICE, TURN, or KCP."""
        stopped = self._send_active_vvp_command(VVP_CMD_STOP)
        started = self._send_active_vvp_command(VVP_CMD_START_LIVE)
        return stopped and started

    @property
    def video_count(self) -> int:
        return self._video_count

    # ------------------------------------------------------------------
    # Main entry point — call from a thread
    # ------------------------------------------------------------------

    def run_session(self) -> tuple[int, int]:
        """Run one P2P streaming session. Returns (video_frames, total_bytes).

        Blocks until the session ends (camera sleeps, error, or stop requested).
        For battery cameras call this in a reconnect loop.
        """
        self._running = True
        self._video_count = 0
        self._total_bytes = 0

        last_error: Exception | None = None
        stream_switch_enabled = False
        try:
            refresh_metadata = getattr(
                self._api, "refresh_streaming_metadata", None
            )
            if callable(refresh_metadata):
                refresh_metadata(self._device)
                self._sn_num = self._device.get("serial_number", self._sn_num)
                self._device_uuid = (
                    self._device.get("device_uuid") or format_sn(self._sn_num)
                )
                self._host_key = self._device.get("host_key", self._host_key)
                if self._video_id_auto:
                    self._video_id = select_default_live_stream_id(self._device)

            set_config = getattr(self._api, "set_device_config", None)
            if self._manage_stream_switch and callable(set_config) and self._sn_num:
                try:
                    stream_switch_enabled = bool(
                        set_config(
                            self._sn_num,
                            {"167": 1},
                            auto_wake=False,
                            device_id=self._device.get("device_id"),
                        )
                    )
                except Exception as exc:
                    _LOGGER.debug(
                        "Could not switch front light (IoT 167) on for %s: %s",
                        self._sn_num,
                        exc,
                    )

            signaling_candidates = _resolve_signaling_candidates(
                self._api, self._device
            )
            _LOGGER.debug(
                "P2P signaling candidates for %s: %s",
                self._sn_num,
                signaling_candidates,
            )
            for sig_host, sig_port in signaling_candidates:
                if not self._running:
                    break
                sig = None
                try:
                    _LOGGER.debug("Connecting to signaling %s:%d", sig_host, sig_port)
                    sig = MsgSvrClient(sig_host, sig_port)
                    sig.connect()

                    v, b = self._do_stream(sig)
                    self._video_count = v
                    self._total_bytes = b
                    if v or b or not self._running:
                        return (v, b)
                    last_error = RuntimeError(
                        f"P2P candidate {sig_host}:{sig_port} produced no stream"
                    )
                    _LOGGER.warning("%s", last_error)
                except Exception as exc:
                    last_error = exc
                    if self._running:
                        _LOGGER.warning(
                            "P2P signaling attempt failed via %s:%d: %s",
                            sig_host,
                            sig_port,
                            exc,
                        )
                        _LOGGER.debug("P2P candidate failure", exc_info=True)
                finally:
                    if sig:
                        try:
                            sig.send_logout(self._device_uuid)
                        except Exception:
                            pass
                        sig.close()
            if last_error is not None:
                _LOGGER.error("P2P session error: %s", last_error)
            return (self._video_count, self._total_bytes)
        finally:
            if stream_switch_enabled:
                try:
                    self._api.set_device_config(
                        self._sn_num,
                        {"167": 0},
                        auto_wake=False,
                        device_id=self._device.get("device_id"),
                    )
                except Exception as exc:
                    _LOGGER.debug(
                        "Could not switch front light (IoT 167) off for %s: %s",
                        self._sn_num,
                        exc,
                    )
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    pass

    def _retry_offline_signaling_status(
        self,
        sig: MsgSvrClient,
        device_uuid: str,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        if status.get("status") != "offline":
            return status

        try:
            api_status = self._api.get_device_online_status(self._sn_num)
        except Exception as exc:
            _LOGGER.debug(
                "OpenAPI status recheck failed for %s during P2P startup: %s",
                self._sn_num,
                exc,
            )
            return status

        if api_status == "dormancy":
            updated_status = dict(status)
            updated_status["status"] = "dormancy"
            return updated_status

        if api_status != "online":
            return status

        _LOGGER.debug(
            "Signaling status is offline for %s while OpenAPI is online; "
            "retrying signaling status for %.0fs",
            self._sn_num,
            _SIGNAL_STATUS_RETRY_WINDOW,
        )
        deadline = time.monotonic() + _SIGNAL_STATUS_RETRY_WINDOW
        while time.monotonic() < deadline and self._running:
            time.sleep(_SIGNAL_STATUS_RETRY_POLL)
            try:
                retry_status = sig.query_device_status(device_uuid)
            except Exception:
                continue
            retry_name = retry_status.get("status", "unknown")
            if retry_name != "offline":
                _LOGGER.debug(
                    "Signaling status recovered for %s: %s",
                    self._sn_num,
                    retry_name,
                )
                return retry_status

        return status

    def _wake_dormant_device(
        self,
        sig: MsgSvrClient,
        device_uuid: str,
        status: dict[str, Any],
    ) -> dict[str, Any] | None:
        keepalive = status.get("contact", {}).get("keepalive", {})
        if keepalive:
            local_ips = _get_local_ips()
            try:
                sig.send_wake_connect(device_uuid, keepalive, local_ips, 16685)
            except OSError as exc:
                # Never let the signaling wake block the HTTP wake below.
                _LOGGER.debug("Signaling wake failed for %s: %s", self._sn_num, exc)

        try:
            self._api.wake_device(self._sn_num, self._device.get("device_id", 0))
        except Exception:
            pass

        if keepalive:
            online_status = sig.wait_for_status(device_uuid, "online", timeout=30)
            if online_status:
                return online_status

        if self._api.wait_for_online(self._sn_num, timeout=30.0, poll_interval=2.0):
            try:
                status = sig.query_device_status(device_uuid)
            except Exception:
                return {"status": "online"}

            return self._retry_offline_signaling_status(sig, device_uuid, status)

        return None

    # ------------------------------------------------------------------
    # Internal streaming pipeline
    # ------------------------------------------------------------------

    def _do_stream(self, sig: MsgSvrClient) -> tuple[int, int]:
        """Internal: full P2P pipeline. Returns (video_count, bytes)."""
        api = self._api
        device_uuid = self._device_uuid
        host_key = self._host_key
        sn_num = self._sn_num
        remote = self._remote

        # Register
        client_id_val = api.session_data.get("userID", "")
        reg = sig.register(
            client_id=client_id_val,
            brand="77",
            country=api.country_code,
        )
        keepalive = None
        if self._keepalive:
            keepalive = _SessionKeepalive(
                sig,
                registered_at=time.monotonic(),
                heartbeat_interval=reg.get("hb") if isinstance(reg, dict) else None,
            )

        # Hello webrtcsvr
        sig.webrtc_hello_full()

        # Query device status
        status = self._retry_offline_signaling_status(
            sig,
            device_uuid,
            sig.query_device_status(device_uuid),
        )
        dev_status = status.get("status", "unknown")
        dev_contact = status.get("contact", {})
        dev_nat = status.get("nat", {})

        # Wake if dormant
        if dev_status == "dormancy":
            _LOGGER.info("Camera dormant, waking...")
            online_status = self._wake_dormant_device(sig, device_uuid, status)
            if online_status:
                dev_status = "online"
                dev_contact = online_status.get("contact", dev_contact)
                dev_nat = online_status.get("nat", dev_nat)
            else:
                _LOGGER.warning("Camera did not come online")
                return (0, 0)

        if dev_status != "online":
            _LOGGER.warning("Camera not online (status=%s)", dev_status)
            return (0, 0)

        # Request TURN credentials
        coturn = sig.request_coturn(device_uuid)
        coturn_ip = coturn.get("coturn_ip", "")
        coturn_port = coturn.get("coturn_port", 9100)
        coturn_user = coturn.get("username", "")
        coturn_pwd = coturn.get("pwd", "")

        # Allocate TURN relay.  The try/finally starts at the first acquired
        # resource (turn.connect() opens both UDP sockets) so a failure in
        # allocate(), peer_stun_binding() or the streaming loop itself can
        # never leak the sockets to the garbage collector.
        turn = TurnClient(coturn_ip, coturn_port, coturn_user, coturn_pwd)
        self._active_vvp_session = None
        try:
            turn.connect()
            if not turn.allocate():
                _LOGGER.error("TURN allocation failed")
                return (0, 0)
            if not remote and not turn.peer_stun_binding():
                _LOGGER.warning(
                    "Could not discover the dedicated media socket's public "
                    "mapping; continuing with host and TURN paths"
                )

            return self._stream_with_turn(
                sig,
                turn,
                device_uuid,
                host_key,
                sn_num,
                dev_nat,
                coturn_ip,
                remote,
                keepalive,
            )
        finally:
            self._send_active_vvp_command(VVP_CMD_STOP)
            self._active_vvp_session = None
            turn.close()

    def _stream_with_turn(
        self,
        sig,
        turn,
        device_uuid,
        host_key,
        sn_num,
        dev_nat,
        coturn_ip,
        remote,
        keepalive: _SessionKeepalive | None = None,
    ) -> tuple[int, int]:
        """SDP exchange, ICE, KCP, VVP — the core streaming loop."""
        api = self._api
        local_ips = _get_local_ips()
        ice_ufrag = os.urandom(4).hex()
        ice_pwd = os.urandom(12).hex()

        # Android advertises the relay only through m=/c=.  ICE host/srflx
        # candidates belong to its separate media socket; it does not append a
        # relay candidate disguised as srflx.
        sdp = _build_xts_sdp_offer(
            ice_ufrag=ice_ufrag,
            ice_pwd=ice_pwd,
            local_ips=local_ips,
            peer_local_port=turn.peer_local_port,
            peer_mapped_ip=turn.peer_mapped_ip,
            peer_mapped_port=turn.peer_mapped_port,
            relay_ip=turn.relay_ip,
            relay_port=turn.relay_port,
            remote=remote,
        )

        # Send SDP offer
        answer = sig.send_offer(device_uuid, sdp)

        # Parse camera SDP answer
        camera_sdp = answer.get("sdp", "")
        camera_ufrag = ""
        camera_pwd = ""
        camera_candidates: list[dict] = []
        camera_sdp_ip = ""
        camera_sdp_port = 0

        for line in camera_sdp.replace("\\n", "\n").split("\n"):
            line = line.strip()
            if line.startswith("a=ice-ufrag:"):
                camera_ufrag = line.split(":", 1)[1].strip()
            elif line.startswith("a=ice-pwd:"):
                camera_pwd = line.split(":", 1)[1].strip()
            elif line.startswith("c=IN IP4 "):
                camera_sdp_ip = line.split("c=IN IP4 ")[1].strip()
            elif line.startswith("m=audio "):
                try:
                    camera_sdp_port = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
            elif line.startswith("a=candidate:"):
                parts = line.split()
                if len(parts) >= 8:
                    cand = {"ip": parts[4], "port": int(parts[5]), "type": parts[7]}
                    for i in range(8, len(parts) - 1, 2):
                        if parts[i] == "raddr":
                            cand["raddr"] = parts[i + 1]
                        elif parts[i] == "rport":
                            cand["rport"] = int(parts[i + 1])
                    camera_candidates.append(cand)

        # Synthesize relay candidate from SDP c=/m=
        has_relay = any(c["type"] == "relay" for c in camera_candidates)
        if not has_relay and camera_sdp_ip and camera_sdp_port:
            camera_candidates.append(
                {
                    "ip": camera_sdp_ip,
                    "port": camera_sdp_port,
                    "type": "relay",
                }
            )

        # Read trickled candidates
        try:
            sig.sock.settimeout(2.0)
            for _ in range(5):
                try:
                    extra = sig._recv_webrtc_content()
                    if isinstance(extra, dict):
                        extra_sdp = extra.get("sdp", "")
                        extra_cand = extra.get("candidate", {})
                        if extra_sdp:
                            for ln in extra_sdp.replace("\\n", "\n").split("\n"):
                                ln = ln.strip()
                                if ln.startswith("a=candidate:"):
                                    pts = ln.split()
                                    if len(pts) >= 8:
                                        camera_candidates.append(
                                            {
                                                "ip": pts[4],
                                                "port": int(pts[5]),
                                                "type": pts[7],
                                            }
                                        )
                        if extra_cand and isinstance(extra_cand, dict):
                            cip = extra_cand.get("ip")
                            cport = extra_cand.get("port")
                            if cip and cport:
                                camera_candidates.append(
                                    {
                                        "ip": cip,
                                        "port": int(cport),
                                        "type": extra_cand.get("type", "relay"),
                                    }
                                )
                            elif extra_cand.get("state") == "completed":
                                break
                except socket.timeout:
                    break
                except Exception:
                    break
            sig.sock.settimeout(10.0)
        except Exception:
            pass

        # A logout from the previous session can still put a battery camera
        # to sleep after it answered this offer (app capture: dormancy push
        # 235 ms after the answer).  Stop early so the caller re-wakes it.
        if _dormancy_reported(sig, device_uuid):
            _LOGGER.info("Camera reported dormancy during negotiation; ending attempt")
            return (0, 0)

        # TURN permissions + channel binds
        camera_wan_ip = dev_nat.get("wan_ip", "")
        perm_ips = {c["ip"] for c in camera_candidates}
        if camera_wan_ip:
            perm_ips.add(camera_wan_ip)
        perm_ips.add(coturn_ip)
        turn.drain_socket()
        for pip in perm_ips:
            turn.create_permission(pip)
        turn.drain_socket()
        for c in camera_candidates:
            turn.channel_bind(c["ip"], c["port"])
        try:
            turn.refresh()
        except TurnRenewalRefusedError as exc:
            # One-off during setup; if the server keeps refusing, the
            # periodic renewal in the streaming loops ends the session.
            _LOGGER.debug("Initial TURN refresh refused: %s", exc)

        # Send candidate_complete
        cand_resp = sig.send_candidate_complete(device_uuid)
        if isinstance(cand_resp, dict):
            cr_sdp = cand_resp.get("sdp", "")
            if cr_sdp:
                for ln in cr_sdp.replace("\\n", "\n").split("\n"):
                    ln = ln.strip()
                    if ln.startswith("a=candidate:"):
                        pts = ln.split()
                        if len(pts) >= 8:
                            tc = {"ip": pts[4], "port": int(pts[5]), "type": pts[7]}
                            if tc not in camera_candidates:
                                camera_candidates.append(tc)
                                turn.create_permission(tc["ip"])
                                turn.channel_bind(tc["ip"], tc["port"])

        # Pick target candidate
        camera_relay = camera_srflx = camera_host = None
        for c in camera_candidates:
            if c["type"] == "relay":
                camera_relay = c
            elif c["type"] == "srflx":
                camera_srflx = c
            elif c["type"] == "host":
                camera_host = c
        target_candidate = camera_relay or camera_srflx or camera_host
        if not target_candidate:
            _LOGGER.error("No camera candidate found")
            return (0, 0)
        target_ip = target_candidate["ip"]
        target_port = target_candidate["port"]

        # ICE + KCP + VVP combined phase
        camera_addrs = {(c["ip"], c["port"]) for c in camera_candidates}
        confirmed_peer: list = [None]
        direct_send_addrs: set[tuple[str, int]] = set()
        source_filter = _MediaSourceFilter(
            turn, camera_candidates, ice_pwd, camera_wan_ip
        )

        def _udp_send(data):
            # Match the Android SDK bootstrap: send IVA/KCP through the single
            # negotiated TURN candidate until the camera answers with KCP.  ICE
            # checks still probe every candidate, but duplicating the same KCP
            # sequence number over host, srflx and relay paths can make stricter
            # cameras ACK the handshake and then discard the VVP login.
            try:
                _send_kcp_datagram(
                    turn,
                    data,
                    target_addr=(target_ip, target_port),
                    confirmed_peer=confirmed_peer[0],
                )
            except Exception:
                pass

        kcp = KcpTunnel(_udp_send)

        def _send_ice_checks():
            for c in camera_candidates:
                try:
                    turn.send_ice_binding(
                        c["ip"], c["port"], ice_ufrag, camera_ufrag, camera_pwd
                    )
                except OSError:
                    pass
                if not remote and c.get("type") != "relay":
                    try:
                        source_filter.remember_check(
                            _send_direct_ice_binding(
                                turn.peer_sock,
                                c["ip"],
                                c["port"],
                                ice_ufrag,
                                camera_ufrag,
                                camera_pwd,
                            )
                        )
                    except OSError:
                        pass

        _send_ice_checks()

        # VVP login
        licence_id = self._get_vvp_licence_id()
        vvp_seq = 0
        vvp_login = build_vvp_packet(
            cmd=VVP_CMD_START_LIVE,
            seq=vvp_seq,
            host_key=host_key,
            param=8,
            video_id=self._video_id,
            licence_id=licence_id,
        )
        vvp_seq += 1
        kcp.send_handshake()
        kcp.send_iva_data(vvp_login)
        self._active_vvp_session = {
            "kcp": kcp,
            "host_key": host_key,
            "licence_id": licence_id,
            "video_id": self._video_id,
            "next_sequence": vvp_seq,
        }

        # State
        ice_deadline = time.monotonic() + 30
        ice_resend_at = time.monotonic() + 2
        retransmit_at = time.monotonic() + _KCP_RETRANSMIT_INTERVAL
        heartbeat_at = time.monotonic() + 3
        iva_heartbeat_at = time.monotonic() + 3
        turn_renewal_at = time.monotonic() + 60
        stun_keepalive_at = time.monotonic() + 5

        ice_count = 0
        confirmed_addr = None
        request_addrs: set = set()
        got_iva_handshake = False
        login_ok = False
        direct_addr = None
        stream_frame_count = 0
        stream_video_count = 0
        stream_total_bytes = 0
        stream_start_time = time.monotonic()
        last_video_time = None
        last_kcp_data_time = None
        last_skip_time = 0.0
        last_stream_restart_time = 0.0
        kcp_push_count = 0

        def _handle_stream_payload(payload):
            nonlocal stream_frame_count, stream_video_count, stream_total_bytes
            nonlocal last_video_time
            if not payload or len(payload) < 4:
                return True
            if payload[0] != 0 or payload[1] != 0 or payload[2] != 1:
                return True
            frame_type = payload[3]
            stream_frame_count += 1
            if frame_type in (
                STREAM_TYPE_IFRAME,
                STREAM_TYPE_PFRAME,
                STREAM_TYPE_AUDIO,
            ):
                decrypted = decrypt_stream_frame(bytearray(payload))
                parsed = parse_stream_frame(bytes(decrypted))
            else:
                parsed = parse_stream_frame(payload)
            if parsed:
                ftype, _, media_data = parsed
                if ftype in (STREAM_TYPE_IFRAME, STREAM_TYPE_PFRAME):
                    stream_video_count += 1
                    stream_total_bytes += len(media_data)
                    last_video_time = time.monotonic()
                    if self.on_video:
                        self.on_video(media_data)
                    return True
                elif ftype == STREAM_TYPE_AUDIO:
                    if self.on_audio:
                        self.on_audio(media_data)
                    return True
            return True

        def _drain_kcp_queue():
            while True:
                queued = kcp.poll_data()
                if not queued:
                    break
                qpayload = queued
                if len(queued) >= 20 and queued[0] == 0xFF and queued[1] == 0x01:
                    qiva = parse_iva_frame(queued)
                    if qiva:
                        qt, _, _, qp = qiva
                        if qt == 0x7012:
                            continue
                        qpayload = qp
                if qpayload:
                    _handle_stream_payload(qpayload)

        _packet_buf: deque = deque()

        while self._running and time.monotonic() < ice_deadline:
            now = time.monotonic()

            if (
                login_ok
                and not stream_video_count
                and now - stream_start_time >= _FIRST_VIDEO_TIMEOUT
            ):
                _LOGGER.info(
                    "No video after VVP login for %.0fs; ending session",
                    now - stream_start_time,
                )
                return (stream_video_count, stream_total_bytes)

            video_idle_since = (
                last_video_time if last_video_time is not None else stream_start_time
            )
            if login_ok and now - video_idle_since > _VIDEO_STALL_END_SESSION:
                _LOGGER.info(
                    "Video stalled for %.0fs (live window ended); ending session "
                    "for reconnect",
                    now - video_idle_since,
                )
                break

            # STOP/START only recovers a stream that was flowing (as in
            # 0.1.11): before the first frame the camera may still be
            # starting its encoder, and _FIRST_VIDEO_TIMEOUT ends the attempt.
            if (
                login_ok
                and last_video_time is not None
                and now - video_idle_since > _VIDEO_STALL_RESTART_DELAY
                and now - last_stream_restart_time > _VIDEO_STALL_RESTART_INTERVAL
            ):
                restarted = self._restart_active_vvp_stream()
                if self._active_vvp_session is not None:
                    vvp_seq = self._active_vvp_session["next_sequence"]
                if restarted:
                    last_stream_restart_time = now
                    _LOGGER.info(
                        "Restarted stalled live stream in the existing P2P session"
                    )

            if now >= retransmit_at:
                kcp.retransmit_unacked()
                retransmit_at = now + _KCP_RETRANSMIT_INTERVAL
            if not login_ok and now >= ice_resend_at:
                _send_ice_checks()
                ice_resend_at = now + 2
            # No extra "gap nudge" ACKs: like the Android client, ACK each
            # received PUSH once with its echoed ts.  Unsolicited ACKs feed the
            # camera's ikcp a bogus RTT sample and inflate its RTO.
            if (
                login_ok
                and now - video_idle_since > _KCP_GAP_SKIP_DELAY
                and now - last_skip_time > _KCP_GAP_SKIP_DELAY
                and kcp.skip_gap()
            ):
                last_skip_time = now
                kcp.flush_acks()
                _drain_kcp_queue()

            # Heartbeats
            if login_ok and now >= heartbeat_at:
                hb = build_vvp_packet(
                    cmd=VVP_CMD_HEARTBEAT,
                    seq=vvp_seq,
                    host_key=host_key,
                    param=8,
                    licence_id=licence_id,
                )
                kcp.send_iva_data(hb)
                vvp_seq += 1
                self._remember_vvp_sequence(vvp_seq)
                heartbeat_at = now + 10

            if login_ok and now >= iva_heartbeat_at:
                kcp.send_handshake()
                iva_heartbeat_at = now + 3

            if login_ok and now >= turn_renewal_at:
                try:
                    # Renews the allocation, permissions (300 s) and channel
                    # bindings (600 s) that are due — RFC 8656 §9/§12 — while
                    # media is relayed.
                    turn_renewal_at = _next_turn_renewal(
                        turn, confirmed_peer[0], now
                    )
                except TurnRenewalRefusedError as exc:
                    _LOGGER.warning(
                        "TURN refused a session renewal (%s); ending P2P "
                        "session so the caller can rebuild it",
                        exc,
                    )
                    return (stream_video_count, stream_total_bytes)

            if not login_ok and now >= stun_keepalive_at:
                try:
                    keepalive_msg, _ = _build_stun(BINDING_REQUEST, b"")
                    turn.peer_sock.sendto(
                        keepalive_msg, (turn.server_ip, turn.server_port)
                    )
                except Exception:
                    pass
                stun_keepalive_at = now + 10

            if keepalive is not None:
                keepalive.tick(now, turn, confirmed_peer[0])
            # The same race after the trickle wait: the keepalives read msgsvr
            # about once a second, so a dormancy push that lands while the
            # camera stays silent ends the attempt instead of the ICE deadline.
            if not login_ok and _dormancy_reported(sig, device_uuid):
                _LOGGER.info(
                    "Camera reported dormancy before the VVP login; ending attempt"
                )
                return (0, 0)

            # Batch drain
            if not _packet_buf:
                kcp.flush_acks()
                _packet_buf.extend(_recv_udp_batch(turn, 0.5))
                if not _packet_buf:
                    continue

            raw, addr, from_turn_socket = _packet_buf.popleft()
            if len(raw) < 4:
                continue
            if not source_filter.accept(raw, addr, from_turn_socket):
                continue

            data = raw
            source_addr = addr
            via_turn = from_turn_socket

            # Unwrap TURN framing
            if from_turn_socket and (raw[0] & 0xC0) == 0x40:
                ch_num, length = struct.unpack(">HH", raw[:4])
                data = raw[4 : 4 + length]
                peer = turn.reverse_channels.get(ch_num)
                if peer:
                    source_addr = peer
                via_turn = True
                inner_stun = _parse_stun(data)
                if inner_stun:
                    if inner_stun["type"] == BINDING_REQUEST:
                        pip, pport = peer if peer else (addr[0], addr[1])
                        resp = _build_ice_response(inner_stun, ice_pwd, pip, pport)
                        try:
                            turn.send_to_peer(pip, pport, resp)
                        except OSError:
                            pass
                        ice_count += 1
                        request_addrs.add((pip, pport))
                        continue
                    elif inner_stun["type"] == BINDING_RESPONSE:
                        pip, pport = peer if peer else (addr[0], addr[1])
                        confirmed_addr = (pip, pport)
                        ice_count += 1
                        kcp.retransmit_unacked()
                        continue
            elif raw[0:2] != b"\xff\x01":
                msg = _parse_stun(raw)
                if msg:
                    if from_turn_socket and msg["type"] == DATA_INDICATION:
                        inner_data = msg["attrs"].get(ATTR_DATA, b"")
                        pip, pport = None, None
                        if ATTR_XOR_PEER_ADDRESS in msg["attrs"]:
                            pip, pport = _decode_xor_address(
                                msg["attrs"][ATTR_XOR_PEER_ADDRESS]
                            )
                        inner_msg = _parse_stun(inner_data)
                        if inner_msg and inner_msg["type"] == BINDING_REQUEST:
                            resp = _build_ice_response(inner_msg, ice_pwd, pip, pport)
                            try:
                                turn.send_to_peer(pip, pport, resp)
                            except OSError:
                                pass
                            ice_count += 1
                            request_addrs.add((pip, pport))
                            if (
                                pip
                                and pport
                                and (pip, pport) != (target_ip, target_port)
                            ):
                                target_ip, target_port = pip, pport
                                if (pip, pport) not in turn.channels:
                                    turn.channel_bind(pip, pport)
                            continue
                        elif inner_msg and inner_msg["type"] == BINDING_RESPONSE:
                            confirmed_addr = (pip, pport) if pip else addr
                            ice_count += 1
                            if pip and pport:
                                target_ip, target_port = pip, pport
                                if (pip, pport) not in turn.channels:
                                    turn.channel_bind(pip, pport)
                            continue
                        data = inner_data
                        if pip:
                            source_addr = (pip, pport)
                            if (pip, pport) != (target_ip, target_port):
                                target_ip, target_port = pip, pport
                                if (pip, pport) not in turn.channels:
                                    turn.create_permission(pip)
                                    turn.channel_bind(pip, pport)
                        via_turn = True
                    elif msg["type"] == BINDING_REQUEST:
                        resp = _build_ice_response(msg, ice_pwd, addr[0], addr[1])
                        try:
                            if _is_direct_peer_path(
                                remote=remote,
                                via_turn=from_turn_socket,
                                peer_ip=addr[0],
                                turn_server_ip=turn.server_ip,
                            ):
                                direct_send_addrs.add(addr)
                                turn.peer_sock.sendto(resp, addr)
                            else:
                                turn.send_to_peer(addr[0], addr[1], resp)
                        except OSError:
                            pass
                        ice_count += 1
                        request_addrs.add(addr)
                        continue
                    elif msg["type"] == BINDING_RESPONSE:
                        if addr[0] == turn.server_ip:
                            continue
                        if not source_filter.answers_our_check(msg):
                            # Only an answer to one of our own checks may
                            # nominate the direct path.
                            continue
                        confirmed_addr = addr
                        if not remote and addr[0] != coturn_ip:
                            direct_send_addrs.add(addr)
                            direct_addr = addr
                            # ICE nomination is enough to select the media
                            # path. Waiting for an inbound KCP PUSH creates a
                            # deadlock on cameras that only answer KCP after
                            # the client retransmits its bootstrap directly.
                            confirmed_peer[0] = (addr[0], addr[1], True)
                            kcp.retransmit_unacked()
                        continue
                    else:
                        continue

            # KCP processing
            if via_turn:
                data = _strip_relay_kcp_trailer(data)
            kcp_segments = parse_kcp_segments(data)
            if kcp_segments:
                result = kcp.process_input(data)
                kcp.flush_acks()
                push_count = sum(
                    segment["cmd"] == 81 for segment in kcp_segments
                )
                if push_count:
                    kcp_push_count += push_count
                    last_kcp_data_time = time.monotonic()
                    if source_addr:
                        cp_ip, cp_port = source_addr
                        is_direct = _is_direct_peer_path(
                            remote=remote,
                            via_turn=via_turn,
                            peer_ip=cp_ip,
                            turn_server_ip=turn.server_ip,
                        )
                        confirmed_peer[0] = (cp_ip, cp_port, is_direct)
                if result:
                    rtype, rdata = result
                    if rtype == "handshake":
                        got_iva_handshake = True
                        if not direct_addr:
                            if confirmed_addr:
                                direct_addr = confirmed_addr
                            else:
                                matching = request_addrs & camera_addrs
                                if matching:
                                    direct_addr = matching.pop()
                            if direct_addr and direct_addr[0] != coturn_ip:
                                direct_send_addrs.add(direct_addr)
                    elif rtype == "data" and rdata:
                        payload = rdata
                        if len(rdata) >= 20 and rdata[0] == 0xFF and rdata[1] == 0x01:
                            iva = parse_iva_frame(rdata)
                            if iva:
                                tmark, _, _, ipayload = iva
                                if tmark == 0x7012:
                                    continue
                                payload = ipayload
                        if payload:
                            if not login_ok:
                                login_ok = True
                                stream_start_time = time.monotonic()
                                if self.on_login:
                                    self.on_login()
                                hb = build_vvp_packet(
                                    cmd=VVP_CMD_HEARTBEAT,
                                    seq=vvp_seq,
                                    host_key=host_key,
                                    param=8,
                                    licence_id=licence_id,
                                )
                                kcp.send_iva_data(hb)
                                vvp_seq += 1
                                self._remember_vvp_sequence(vvp_seq)
                            ice_deadline = max(ice_deadline, time.monotonic() + 30)
                            _handle_stream_payload(payload)
                        _drain_kcp_queue()

                # Stall recovery + deadline management
                if login_ok:
                    kcp_alive = (
                        last_kcp_data_time and time.monotonic() - last_kcp_data_time < 20
                    )
                    if not kcp_alive and last_kcp_data_time:
                        break
                    if last_video_time and time.monotonic() - last_video_time < 10:
                        ice_deadline = max(ice_deadline, time.monotonic() + 15)
                    elif kcp_alive:
                        ice_deadline = max(ice_deadline, time.monotonic() + 10)
                continue

            # Raw IVA frame
            if len(data) >= 2 and data[0] == 0xFF and data[1] == 0x01:
                iva = parse_iva_frame(data)
                if iva and iva[0] == 0x7012:
                    got_iva_handshake = True
                continue

        # Connection loop done — enter continuation receiver if we got video
        if not login_ok:
            last_kcp_age = (
                round(time.monotonic() - last_kcp_data_time, 1)
                if last_kcp_data_time
                else None
            )
            candidate_summary = [
                f"{candidate.get('type', 'unknown')}@"
                f"{candidate.get('ip', '?')}:{candidate.get('port', '?')}"
                for candidate in camera_candidates
            ]
            _LOGGER.warning(
                "VVP login failed: video_id=%s candidates=%s target=%s:%s "
                "ice_events=%s ice_confirmed=%s iva_handshake=%s "
                "kcp_pushes=%s kcp_acks=%s kcp_unacked=%s "
                "last_kcp_age=%s confirmed_peer=%s direct_peer=%s "
                "direct_send_addrs=%s kcp_acked_sns=%s",
                self._video_id,
                candidate_summary,
                target_ip,
                target_port,
                ice_count,
                confirmed_addr,
                got_iva_handshake,
                kcp_push_count,
                len(kcp.acked_sns),
                len(kcp.sent_segments),
                last_kcp_age,
                confirmed_peer[0],
                direct_addr,
                sorted(direct_send_addrs),
                sorted(kcp.acked_sns),
            )
            return (stream_video_count, stream_total_bytes)

        if (
            last_video_time
            and time.monotonic() - last_video_time > _VIDEO_STALL_END_SESSION
        ):
            return (stream_video_count, stream_total_bytes)

        # Continuation receiver
        v2, b2 = self._receive_stream(
            turn,
            kcp,
            ice_pwd,
            host_key,
            licence_id,
            vvp_seq,
            stream_frame_count,
            stream_video_count,
            stream_total_bytes,
            stream_start_time,
            last_video_time=last_video_time,
            last_kcp_data_time=last_kcp_data_time,
            last_stream_restart_time=last_stream_restart_time,
            turn_renewal_at=turn_renewal_at,
            confirmed_peer=confirmed_peer[0],
            source_filter=source_filter,
            keepalive=keepalive,
        )
        return (v2, b2)

    # ------------------------------------------------------------------
    # Continuation receiver
    # ------------------------------------------------------------------

    def _receive_stream(
        self,
        turn,
        kcp,
        ice_pwd,
        host_key,
        licence_id,
        vvp_seq_start,
        frame_count_start,
        video_count_start,
        bytes_start,
        start_time,
        *,
        last_video_time: float | None = None,
        last_kcp_data_time: float | None = None,
        last_stream_restart_time: float | None = None,
        turn_renewal_at: float | None = None,
        confirmed_peer: tuple[str, int, bool] | None = None,
        source_filter: _MediaSourceFilter | None = None,
        keepalive: _SessionKeepalive | None = None,
    ) -> tuple[int, int]:
        """Continue receiving video after the connection loop.

        The connection loop hands over its stall clocks (last video frame,
        last KCP PUSH, last VVP restart) and TURN renewal deadline so the
        hand-over does not restart them (C106, C75): a camera that went
        silent ends the session about 30 s after its last frame, not 50 s.
        It also hands over the media path it confirmed (``confirmed_peer``,
        None when unknown), its source filter (None accepts every source)
        and its session keepalives.  Clocks left as None start now.
        """
        frame_count = frame_count_start
        video_frame_count = video_count_start
        total_bytes = bytes_start
        if last_video_time is None and video_frame_count:
            last_video_time = time.monotonic()
        if last_kcp_data_time is None:
            last_kcp_data_time = time.monotonic()
        last_skip_time = 0.0
        if last_stream_restart_time is None:
            last_stream_restart_time = 0.0
        vvp_seq = vvp_seq_start
        last_heartbeat = time.monotonic()
        last_iva_heartbeat = time.monotonic()
        if turn_renewal_at is None:
            turn_renewal_at = time.monotonic() + 60
        retransmit_at = time.monotonic() + _KCP_RETRANSMIT_INTERVAL

        def _process_kcp_message(msg_data):
            nonlocal frame_count, video_frame_count, total_bytes, last_video_time
            payload = msg_data
            if len(msg_data) >= 20 and msg_data[0] == 0xFF and msg_data[1] == 0x01:
                iva = parse_iva_frame(msg_data)
                if iva:
                    tmark, _, _, ipayload = iva
                    if tmark == 0x7012:
                        return True
                    payload = ipayload
            if not payload or len(payload) < 4:
                return True
            if payload[0] == 0 and payload[1] == 0 and payload[2] == 1:
                frame_type = payload[3]
                frame_count += 1
                if frame_type in (
                    STREAM_TYPE_IFRAME,
                    STREAM_TYPE_PFRAME,
                    STREAM_TYPE_AUDIO,
                ):
                    decrypted = decrypt_stream_frame(bytearray(payload))
                    parsed = parse_stream_frame(bytes(decrypted))
                else:
                    parsed = parse_stream_frame(payload)
                if parsed:
                    ftype, _, media_data = parsed
                    if ftype in (STREAM_TYPE_IFRAME, STREAM_TYPE_PFRAME):
                        video_frame_count += 1
                        total_bytes += len(media_data)
                        last_video_time = time.monotonic()
                        if self.on_video:
                            self.on_video(media_data)
                        return True
                    elif ftype == STREAM_TYPE_AUDIO:
                        if self.on_audio:
                            self.on_audio(media_data)
                        return True
            return True

        # Drain queued KCP messages
        while True:
            queued = kcp.poll_data()
            if not queued:
                break
            _process_kcp_message(queued)

        # Send initial heartbeat
        if host_key:
            hb = build_vvp_packet(
                cmd=VVP_CMD_HEARTBEAT,
                seq=vvp_seq,
                host_key=host_key,
                param=8,
                licence_id=licence_id,
            )
            kcp.send_iva_data(hb)
            vvp_seq += 1
            self._remember_vvp_sequence(vvp_seq)

        _recv_buf: deque = deque()
        timeout_count = 0

        while self._running:
            # TURN renewals must run every pass, not only when the receive
            # buffer happens to be empty: continuous packet flow used to
            # starve them until the allocation, permissions and channel
            # bindings expired (RFC 8656 §9/§12).
            now = time.monotonic()
            if not video_frame_count and now - start_time >= _FIRST_VIDEO_TIMEOUT:
                _LOGGER.info(
                    "No video after VVP login for %.0fs; ending session",
                    now - start_time,
                )
                break
            video_idle_since = (
                last_video_time if last_video_time is not None else start_time
            )
            if now - video_idle_since > _VIDEO_STALL_END_SESSION:
                _LOGGER.info(
                    "Video stalled for %.0fs; ending session for reconnect",
                    now - video_idle_since,
                )
                break
            if (
                last_video_time is not None
                and now - video_idle_since > _VIDEO_STALL_RESTART_DELAY
                and now - last_stream_restart_time > _VIDEO_STALL_RESTART_INTERVAL
            ):
                if self._restart_active_vvp_stream():
                    last_stream_restart_time = now
                    vvp_seq = self._active_vvp_session["next_sequence"]
            if now >= retransmit_at:
                kcp.retransmit_unacked()
                retransmit_at = now + _KCP_RETRANSMIT_INTERVAL
            if (
                now - video_idle_since > _KCP_GAP_SKIP_DELAY
                and now - last_skip_time > _KCP_GAP_SKIP_DELAY
                and kcp.skip_gap()
            ):
                last_skip_time = now
                kcp.flush_acks()
                while (message := kcp.poll_data()) is not None:
                    _process_kcp_message(message)
            if now >= turn_renewal_at:
                try:
                    turn_renewal_at = _next_turn_renewal(turn, confirmed_peer, now)
                except TurnRenewalRefusedError as exc:
                    _LOGGER.warning(
                        "TURN refused a session renewal (%s); ending P2P "
                        "session so the caller can rebuild it",
                        exc,
                    )
                    return (video_frame_count, total_bytes)
            if keepalive is not None:
                keepalive.tick(now, turn, confirmed_peer)

            if not _recv_buf:
                kcp.flush_acks()
                _recv_buf.extend(_recv_udp_batch(turn, 0.5))
                if not _recv_buf:
                    now = time.monotonic()
                    if host_key and now - last_heartbeat >= 10:
                        hb = build_vvp_packet(
                            cmd=VVP_CMD_HEARTBEAT,
                            seq=vvp_seq,
                            host_key=host_key,
                            param=8,
                            licence_id=licence_id,
                        )
                        kcp.send_iva_data(hb)
                        vvp_seq += 1
                        self._remember_vvp_sequence(vvp_seq)
                        last_heartbeat = now
                    if now - last_iva_heartbeat >= 3:
                        kcp.send_handshake()
                        last_iva_heartbeat = now
                    if (
                        now - start_time > 10
                        and video_frame_count == 0
                        and frame_count == 0
                    ):
                        timeout_count += 1
                        if timeout_count >= 60:
                            break
                    if last_kcp_data_time and now - last_kcp_data_time > 20:
                        _LOGGER.debug("No KCP data for 20s, ending session")
                        break
                    continue

            raw, addr, from_turn_socket = _recv_buf.popleft()
            timeout_count = 0
            if len(raw) < 4:
                continue
            if source_filter is not None and not source_filter.accept(
                raw, addr, from_turn_socket
            ):
                continue

            # Unwrap TURN framing
            data = raw
            if from_turn_socket and (raw[0] & 0xC0) == 0x40:
                ch, length = struct.unpack(">HH", raw[:4])
                data = raw[4 : 4 + length]
                inner_stun = _parse_stun(data)
                if inner_stun:
                    if inner_stun["type"] == BINDING_REQUEST and ice_pwd:
                        peer = turn.reverse_channels.get(ch)
                        pip, pport = peer if peer else (addr[0], addr[1])
                        resp = _build_ice_response(inner_stun, ice_pwd, pip, pport)
                        try:
                            turn.send_to_peer(pip, pport, resp)
                        except OSError:
                            pass
                        continue
                    elif inner_stun["type"] == BINDING_RESPONSE:
                        continue
            elif raw[0:2] != b"\xff\x01":
                msg = _parse_stun(raw)
                if msg:
                    if from_turn_socket and msg["type"] == DATA_INDICATION:
                        inner = msg["attrs"].get(ATTR_DATA, b"")
                        pip, pport = None, None
                        if ATTR_XOR_PEER_ADDRESS in msg["attrs"]:
                            pip, pport = _decode_xor_address(
                                msg["attrs"][ATTR_XOR_PEER_ADDRESS]
                            )
                        inner_msg = _parse_stun(inner)
                        if (
                            inner_msg
                            and inner_msg["type"] == BINDING_REQUEST
                            and ice_pwd
                        ):
                            resp = _build_ice_response(inner_msg, ice_pwd, pip, pport)
                            try:
                                turn.send_to_peer(pip, pport, resp)
                            except OSError:
                                pass
                            continue
                        data = inner
                    elif msg["type"] == BINDING_REQUEST and ice_pwd:
                        resp = _build_ice_response(msg, ice_pwd, addr[0], addr[1])
                        try:
                            if not from_turn_socket:
                                turn.peer_sock.sendto(resp, addr)
                            else:
                                turn.send_to_peer(addr[0], addr[1], resp)
                        except OSError:
                            pass
                        continue
                    else:
                        continue

            if len(data) < 4:
                continue

            # KCP processing
            if from_turn_socket:
                data = _strip_relay_kcp_trailer(data)
            segments = parse_kcp_segments(data)
            if segments:
                if any(segment["cmd"] == 81 for segment in segments):
                    last_kcp_data_time = time.monotonic()
                result = kcp.process_input(data)
                kcp.flush_acks()
                if result:
                    rtype, rdata = result
                    if rtype == "data" and rdata:
                        _process_kcp_message(rdata)
                        while True:
                            q = kcp.poll_data()
                            if not q:
                                break
                            _process_kcp_message(q)
                now = time.monotonic()
                if last_kcp_data_time and now - last_kcp_data_time > 20:
                    break
            elif data[0] == 0xFF and data[1] == 0x01:
                kcp.process_input(data)

            # Heartbeats
            now = time.monotonic()
            if host_key and now - last_heartbeat >= 10:
                hb = build_vvp_packet(
                    cmd=VVP_CMD_HEARTBEAT,
                    seq=vvp_seq,
                    host_key=host_key,
                    param=8,
                    licence_id=licence_id,
                )
                kcp.send_iva_data(hb)
                vvp_seq += 1
                self._remember_vvp_sequence(vvp_seq)
                last_heartbeat = now
            if now - last_iva_heartbeat >= 3:
                kcp.send_handshake()
                last_iva_heartbeat = now

        return (video_frame_count, total_bytes)
