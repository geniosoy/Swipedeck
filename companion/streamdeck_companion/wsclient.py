"""A small WebSocket client (RFC 6455) for text messages, standard library only. Used for OBS (obs.py).

It does what one short conversation needs: the opening handshake (with an optional sub-protocol), masked text frames
out, text frames in (also in fragments), ping answered with pong, close. No TLS (OBS listens on plain ws:// on the
local machine), no extensions, no compression.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC 6455, section 1.3
MAX_MESSAGE = 4 * 1024 * 1024
MAX_HANDSHAKE = 16 * 1024

TEXT, BINARY, CLOSE, PING, PONG, CONTINUATION = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0


class WebSocketError(ConnectionError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code  # the status code of the server's close frame, when it sent one


def accept_key(key: str) -> str:
    """The Sec-WebSocket-Accept a server must answer for this Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def encode_frame(opcode: int, payload: bytes, mask: bool = True) -> bytes:
    """One final (unfragmented) frame. Clients must mask; the server side of the tests does not."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    flag = 0x80 if mask else 0
    if n < 126:
        head += bytes([flag | n])
    elif n < 1 << 16:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


class WebSocket:
    def __init__(self, host: str, port: int, path: str = "/", subprotocol: str | None = None, timeout: float = 5.0,
                 sock: socket.socket | None = None):
        self.host, self.port, self.path, self.subprotocol, self.timeout = host, port, path, subprotocol, timeout
        self._sock = sock
        self._buffer = b""

    # -- opening and closing ------------------------------------------------------------------

    def connect(self) -> "WebSocket":
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            except OSError as e:
                raise WebSocketError(f"could not connect to {self.host}:{self.port}: {e}") from None
        self._sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [f"GET {self.path} HTTP/1.1", f"Host: {self.host}:{self.port}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if self.subprotocol:
            lines.append(f"Sec-WebSocket-Protocol: {self.subprotocol}")
        self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        head = self._read_until(b"\r\n\r\n", MAX_HANDSHAKE)
        status, *header_lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in status + " ":
            self.close()
            raise WebSocketError(f"the server did not accept the WebSocket connection ({status.strip()})")
        headers = {}
        for line in header_lines:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept_key(key):
            self.close()
            raise WebSocketError("the server's WebSocket handshake answer is wrong")
        if self.subprotocol and headers.get("sec-websocket-protocol") != self.subprotocol:
            self.close()
            raise WebSocketError(f"the server does not speak {self.subprotocol}")
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.sendall(encode_frame(CLOSE, struct.pack(">H", 1000)))
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> "WebSocket":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- messages -----------------------------------------------------------------------------

    def send_text(self, text: str) -> None:
        if self._sock is None:
            raise WebSocketError("not connected")
        self._sock.sendall(encode_frame(TEXT, text.encode("utf-8")))

    def recv_text(self) -> str:
        """The next text message (ping is answered, other control frames are handled); raises WebSocketError on close or timeout."""
        message, started = b"", False
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == PING:
                self._sock.sendall(encode_frame(PONG, payload))
            elif opcode == PONG:
                continue
            elif opcode == CLOSE:
                self.close()
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                raise WebSocketError("the server closed the connection", code)
            elif opcode in (TEXT, BINARY) and not started:
                message, started = payload, True
                if opcode == BINARY:
                    raise WebSocketError("unexpected binary message")
            elif opcode == CONTINUATION and started:
                message += payload
            else:
                raise WebSocketError(f"unexpected frame (opcode {opcode})")
            if len(message) > MAX_MESSAGE:
                raise WebSocketError("message too large")
            if fin and started and opcode in (TEXT, CONTINUATION):
                return message.decode("utf-8")

    # -- internals ----------------------------------------------------------------------------

    def _read_exact(self, n: int) -> bytes:
        while len(self._buffer) < n:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                raise WebSocketError("timed out waiting for the server") from None
            except OSError as e:
                raise WebSocketError(f"connection lost: {e}") from None
            if not chunk:
                raise WebSocketError("the server closed the connection")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def _read_until(self, marker: bytes, limit: int) -> bytes:
        while marker not in self._buffer:
            if len(self._buffer) > limit:
                raise WebSocketError("handshake answer too long")
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise WebSocketError("timed out waiting for the server") from None
            except OSError as e:
                raise WebSocketError(f"connection lost: {e}") from None
            if not chunk:
                raise WebSocketError("the server closed the connection")
            self._buffer += chunk
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read_exact(2)
        fin, opcode, masked, n = bool(b0 & 0x80), b0 & 0x0F, bool(b1 & 0x80), b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read_exact(8))[0]
        if n > MAX_MESSAGE:
            raise WebSocketError("message too large")
        key = self._read_exact(4) if masked else b""
        payload = self._read_exact(n)
        if masked:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload
