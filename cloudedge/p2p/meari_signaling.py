#!/usr/bin/env python3
"""
Meari msgsvr signaling protocol client.

Reverse-engineered from libppsdk.so (Ghidra decompilation).
Protocol: TCP with 8-byte header + 3DES-ECB encrypted JSON + checksum + tail.

Wire frame format:
  [0]    0xE6         magic
  [1]    node         0xA1=client_send
  [2]    method       0xB1=direct, 0xB2=routed
  [3]    cmd          0xC1=register, 0xC6=webrtc, 0xC7=status, 0xC9=heartbeat, 0xD1=connect
  [4]    type         0xD3 (default)
  [5]    0xE6         magic
  [6]    len_lo       payload length low byte
  [7]    len_hi|enc   payload length high 7 bits + bit7=encrypted
  [8..N] payload      3DES-ECB encrypted JSON (PKCS5 padded)
  [N+1]  checksum     sum of all payload bytes & 0xFF
  [N+2]  0x9D         tail marker

Encryption: 3DES ECB with key "__#!HIRCloud5.0!#__" (padded to 24 bytes with zeros).
"""

import base64
import collections
import json
import socket
import time
import uuid as uuid_mod

from Crypto.Cipher import DES3

# 3DES ECB key for msgsvr protocol
MSGSVR_KEY = b"__#!HIRCloud5.0!#__\x00\x00\x00\x00\x00"  # 24 bytes

# Frame constants
MAGIC = 0xE6
TAIL = 0x9D

# Node values (byte1)
NODE_CLIENT = 0xA1

# Method values (byte2)
METHOD_DIRECT = 0xB1
METHOD_ROUTED = 0xB2

# Cmd values (byte3)
CMD_REGISTER = 0xC1
CMD_WEBRTC = 0xC6
CMD_STATUS = 0xC7
CMD_HEARTBEAT = 0xC9
CMD_CONNECT = 0xD1

# Type (byte4) - always 0xD3 in all captured sessions
TYPE_DEFAULT = 0xD3


def _des3_encrypt(data: bytes) -> bytes:
    """3DES ECB encrypt with PKCS5 padding."""
    pad_len = 8 - (len(data) % 8)
    padded = data + bytes([pad_len] * pad_len)
    cipher = DES3.new(MSGSVR_KEY, DES3.MODE_ECB)
    return cipher.encrypt(padded)


def _des3_decrypt(data: bytes) -> bytes:
    """3DES ECB decrypt and remove PKCS5 padding."""
    if len(data) == 0:
        return b""
    # Truncate to block boundary
    data = data[:len(data) - (len(data) % 8)]
    if len(data) == 0:
        return b""
    cipher = DES3.new(MSGSVR_KEY, DES3.MODE_ECB)
    decrypted = cipher.decrypt(data)
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 8 and all(b == pad_len for b in decrypted[-pad_len:]):
        return decrypted[:-pad_len]
    return decrypted.rstrip(b'\x00')


def _build_frame(node, method, cmd, payload_json, encrypt=True):
    """Build a complete msgsvr wire frame."""
    payload = payload_json.encode('utf-8')

    if encrypt:
        payload = _des3_encrypt(payload)

    payload_len = len(payload)
    header = bytearray(8)
    header[0] = MAGIC
    header[1] = node
    header[2] = method
    header[3] = cmd
    header[4] = TYPE_DEFAULT
    header[5] = MAGIC
    header[6] = payload_len & 0xFF
    header[7] = ((payload_len >> 8) & 0x7F) | (0x80 if encrypt else 0x00)

    # The app and msgsvr use the additive checksum (as root_discovery does).
    checksum = sum(payload) & 0xFF

    return bytes(header) + payload + bytes([checksum, TAIL])


def _take_frame(buffer):
    """Remove and parse the first complete frame in ``buffer``, or None."""
    if len(buffer) < 8:
        return None
    if buffer[0] != MAGIC or buffer[5] != MAGIC:
        raise ValueError(f"Invalid header magic: {bytes(buffer[:8]).hex()}")
    # Header, payload, checksum byte and tail byte.
    end = 8 + (buffer[6] | ((buffer[7] & 0x7F) << 8)) + 2
    if len(buffer) < end:
        return None
    header, rest = bytes(buffer[:8]), bytes(buffer[8:end])
    del buffer[:end]
    return _parse_frame(header, rest)


def _parse_frame(header, rest):
    """Parse a frame from its header and payload + checksum + tail bytes."""
    node = header[1]
    method = header[2]
    cmd = header[3]

    payload_len = header[6] | ((header[7] & 0x7F) << 8)
    is_encrypted = bool(header[7] & 0x80)
    payload = rest[:payload_len]

    if is_encrypted and payload_len > 0:
        payload = _des3_decrypt(payload)

    try:
        json_data = json.loads(payload.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        json_data = {"_raw": payload.hex()[:200]}

    return {
        'node': node,
        'method': method,
        'cmd': cmd,
        'encrypted': is_encrypted,
        'json': json_data,
    }


class MsgSvrClient:
    """Meari signaling (msgsvr) protocol client.

    Handles registration, device status queries, wake, TURN credential
    negotiation, and SDP offer/answer exchange via webrtcsvr.
    """

    def __init__(self, server_host, server_port=28974):
        self.server_host = server_host
        self.server_port = server_port
        self.sock = None
        self.uuid = None
        self.token = None
        self.webrtcsvr = None  # {ip, port, domain, tag}
        # Unsolicited msgsvr status pushes (cmd 0xC7) that arrived while a
        # request was waiting for a different reply.
        self.pending_status = collections.deque(maxlen=16)
        # Bytes received but not yet parsed into a frame.  Every read goes
        # through this buffer, so a frame split across TCP segments (or cut
        # by a timeout) is completed by the next read instead of desyncing.
        self._rx = bytearray()

    def connect(self):
        """TCP connect to signaling server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10.0)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.connect((self.server_host, self.server_port))

    def _send(self, method, cmd, payload_json):
        """Build and send a frame."""
        frame = _build_frame(NODE_CLIENT, method, cmd, payload_json)
        self.sock.sendall(frame)

    def _recv(self):
        """Receive one frame (each socket read honours the socket timeout)."""
        while True:
            frame = _take_frame(self._rx)
            if frame is not None:
                return frame
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("msgsvr closed the connection")
            self._rx += chunk

    def _queue_status(self, frame):
        """Keep an unsolicited status push (cmd 0xC7) for later consumers."""
        data = frame.get("json")
        if frame["cmd"] == CMD_STATUS and isinstance(data, dict):
            self.pending_status.append(data)

    def _recv_cmd(self, expected_cmd, max_frames=16):
        """Receive the next frame for ``expected_cmd``.

        Status pushes are queued for later consumers; other unrelated
        frames (late connect acks, heartbeat replies) are dropped.
        """
        for _ in range(max_frames):
            resp = self._recv()
            if resp["cmd"] == expected_cmd:
                return resp
            self._queue_status(resp)
        raise RuntimeError(f"No reply for cmd 0x{expected_cmd:02x}")

    def drain_frames(self, max_frames=64):
        """Consume the frames that already arrived, without blocking.

        Used while a session streams: status pushes are queued in
        ``pending_status``; keepalive replies (heartbeat, hello) and other
        frames are dropped.  Returns the number of frames consumed and
        raises ConnectionError when msgsvr closed the connection.
        """
        previous_timeout = self.sock.gettimeout()
        self.sock.settimeout(0.0)
        try:
            while True:
                try:
                    chunk = self.sock.recv(4096)
                except (BlockingIOError, InterruptedError, socket.timeout):
                    break
                if not chunk:
                    raise ConnectionError("msgsvr closed the connection")
                self._rx += chunk
                if len(self._rx) >= 65536:
                    break
        finally:
            self.sock.settimeout(previous_timeout)
        consumed = 0
        while consumed < max_frames:
            frame = _take_frame(self._rx)
            if frame is None:
                break
            self._queue_status(frame)
            consumed += 1
        return consumed

    def _send_webrtc(self, inner_json, to_webrtcsvr=True):
        """Send a webrtcsvr-routed message with base64-encoded content."""
        content = base64.b64encode(
            json.dumps(inner_json, separators=(",", ":")).encode()
        ).decode()

        msg = {
            "from": {
                "node": "client",
                "domain": self.uuid,
                "transport": "tcp",
                "type": "binary",
            },
        }
        if to_webrtcsvr and self.webrtcsvr:
            msg["to"] = {
                "node": "webrtcsvr",
                "domain": self.webrtcsvr["domain"],
                "transport": "tcp",
                "type": "binary",
                "ip": self.webrtcsvr["ip"],
                "port": self.webrtcsvr["port"],
            }
        else:
            msg["node"] = "webrtcsvr"

        msg["content"] = content
        method = METHOD_ROUTED if to_webrtcsvr and self.webrtcsvr else METHOD_DIRECT
        self._send(method, CMD_WEBRTC,
                   json.dumps(msg, separators=(",", ":")))

    def _recv_webrtc_content(self):
        """Receive a webrtcsvr response and decode the inner content."""
        resp = self._recv_cmd(CMD_WEBRTC)
        if resp["json"] and "content" in resp["json"]:
            inner_b64 = resp["json"]["content"]
            return json.loads(base64.b64decode(inner_b64))
        return resp["json"]

    # ---- High-level protocol steps ----

    def register(self, client_id, brand="77", app_ver="5.9.2a16", country="FR"):
        """Step 1: Register with signaling server. Returns uuid and token."""
        msg = json.dumps({
            "action": "register",
            "transport": "tcp",
            "type": "binary",
            "ver": 15340,
            "runtime": 0,
            "extra_params": {
                "brand": brand,
                "clientid": str(client_id),
                "v": app_ver,
                "c": country,
            },
        }, separators=(",", ":"))

        self._send(METHOD_DIRECT, CMD_REGISTER, msg)
        resp = self._recv()
        data = resp["json"]

        if data.get("result") != "OK":
            raise RuntimeError(f"Register failed: {data}")

        self.uuid = data["uuid"]
        self.token = data["token"]
        return data

    def send_heartbeat(self, runtime=0):
        """Registration keepalive (cmd 0xC9), every register-reply ``hb`` s.

        Same body as the app's: the register reply's uuid and token, the
        register ``ver`` and the seconds since registration.  msgsvr answers
        on cmd 0xC9; drain_frames() discards the reply.
        """
        self._send(METHOD_DIRECT, CMD_HEARTBEAT, json.dumps({
            "uuid": self.uuid,
            "token": self.token,
            "ver": 15340,
            "runtime": int(runtime),
        }, separators=(",", ":")))

    def send_hello(self):
        """Routed webrtcsvr hello, the app's ~21 s keepalive while streaming.

        msgsvr answers with a hello carrying a fresh tag; drain_frames()
        discards the reply.
        """
        self._send_webrtc({"cmd": "hello"})

    def webrtc_hello(self):
        """Step 2: Hello to webrtcsvr, get tag for later SDP exchange."""
        self._send_webrtc({"cmd": "hello"}, to_webrtcsvr=False)
        inner = self._recv_webrtc_content()

        # The response's outer "from" has the webrtcsvr address
        # We need to re-read from the raw frame to get the outer envelope
        # Actually, _recv_webrtc_content already decoded the inner content
        # We need to capture the outer frame too.
        # Let's redo this properly.
        return inner

    def webrtc_hello_full(self):
        """Step 2: Hello to webrtcsvr, capturing full response."""
        content = base64.b64encode(b'{"cmd":"hello"}').decode()
        msg = json.dumps({
            "from": {
                "node": "client",
                "domain": self.uuid,
                "transport": "tcp",
                "type": "binary",
            },
            "node": "webrtcsvr",
            "content": content,
        }, separators=(",", ":"))

        self._send(METHOD_DIRECT, CMD_WEBRTC, msg)
        resp = self._recv()
        data = resp["json"]

        # Extract webrtcsvr info from outer "from" field
        from_info = data.get("from", {})
        inner = json.loads(base64.b64decode(data.get("content", "")))

        self.webrtcsvr = {
            "ip": from_info.get("ip"),
            "port": from_info.get("port"),
            "domain": from_info.get("domain", "webrtcsvr.eu"),
            "tag": inner.get("tag", ""),
        }
        return inner

    def query_device_status(self, device_uuid):
        """Step 3: Query device status. Returns status dict."""
        sid = uuid_mod.uuid4().hex[:16]
        msg = json.dumps({
            "action": "status",
            "uuid": device_uuid,
            "sid": sid,
        }, separators=(",", ":"))

        self._send(METHOD_DIRECT, CMD_STATUS, msg)
        resp = self._recv()
        return resp["json"]

    def send_wake_connect(self, device_uuid, device_contact, local_ips, local_port):
        """Step 4: Send connection/wake request to device."""
        # The status reply's keepalive names the camera's NAT server
        # (node natsvr); the app addresses the camera itself and only reuses
        # the keepalive endpoint.
        device_contact = {
            "node": "dev",
            "domain": device_uuid,
            "transport": device_contact.get("transport", "tcp"),
            "type": device_contact.get("type", "binary"),
            "ip": device_contact.get("ip"),
            "port": device_contact.get("port"),
        }
        sid = uuid_mod.uuid4().hex[:16]
        msg = json.dumps({
            "sid": sid,
            "uuid": self.uuid,
            "params": {
                "sid": uuid_mod.uuid4().hex[:8] + "00000001",
                "local": {
                    "ip": local_ips,
                    "port": local_port,
                },
                "attach": {
                    "awaken_type": 1,
                },
            },
            "contact": device_contact,
        }, separators=(",", ":"))

        self._send(METHOD_DIRECT, CMD_CONNECT, msg)
        try:
            resp = self._recv()
        except socket.timeout:
            return None
        if resp["cmd"] == CMD_STATUS and isinstance(resp.get("json"), dict):
            self.pending_status.append(resp["json"])
        return resp["json"]

    def request_coturn(self, device_uuid):
        """Step 5: Request TURN credentials via webrtcsvr."""
        sid = f"{self.uuid}:01"
        self._send_webrtc({
            "cmd": "option",
            "sid": sid,
            "caller": self.uuid,
            "callee": device_uuid,
            "method": "coturn",
        })
        return self._recv_webrtc_content()

    def send_offer(self, device_uuid, sdp):
        """Step 6: Send SDP offer with ICE candidates."""
        sid = f"{self.uuid}:01"
        tag = self.webrtcsvr["tag"] + "01"

        self._send_webrtc({
            "cmd": "offer",
            "sid": sid,
            "caller": self.uuid,
            "callee": device_uuid,
            "channel": 0,
            "stream_type": 1,
            "sdp": sdp,
            "tag": tag,
        })
        return self._recv_webrtc_content()

    def send_candidate_complete(self, device_uuid):
        """Step 7: Signal ICE candidate negotiation complete."""
        sid = f"{self.uuid}:01"
        self._send_webrtc({
            "cmd": "candidate",
            "sid": sid,
            "caller": self.uuid,
            "callee": device_uuid,
            "candidate": {
                "cmd": "nego",
                "state": "completed",
                "mode": 1,
            },
        })
        return self._recv_webrtc_content()

    def send_logout(self, device_uuid):
        """Disconnect session."""
        sid = f"{self.uuid}:01"
        self._send_webrtc({
            "cmd": "logout",
            "sid": sid,
            "caller": self.uuid,
            "callee": device_uuid,
        })

    def wait_for_status(self, device_uuid, target="online", timeout=30):
        """Wait for device status to change (e.g., dormancy -> online)."""
        while self.pending_status:
            data = self.pending_status.popleft()
            if (data.get("action") == "status" and data.get("uuid") == device_uuid
                    and data.get("status", "") == target):
                return data
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(1, deadline - time.time()))
            try:
                resp = self._recv()
                data = resp["json"]
                if (data.get("action") == "status" and
                        data.get("uuid") == device_uuid):
                    status = data.get("status", "")
                    if status == target:
                        return data
            except socket.timeout:
                continue
        return None

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
