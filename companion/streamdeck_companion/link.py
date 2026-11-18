"""USB serial link to the board: JSON lines in both directions."""
from __future__ import annotations

import json
import logging
import time

import serial
from serial.tools import list_ports

log = logging.getLogger("link")

ESP32S3_USB_VID, ESP32S3_USB_PID = 0x303A, 0x1001  # native USB Serial/JTAG of the ES3C28P


def find_port() -> str | None:
    """First native-USB ESP32-S3 port. The handshake (hello) confirms it really is the deck."""
    for p in list_ports.comports():
        if p.vid == ESP32S3_USB_VID and p.pid == ESP32S3_USB_PID:
            return p.device
    return None


class SerialBoard:
    """Same interface the Companion uses in tests: send(dict), read() -> dict | None, close()."""

    def __init__(self, port: str):
        self.port = port
        self._ser = serial.Serial(port, 115200, timeout=0.05, write_timeout=2)
        self._buf = bytearray()

    def send(self, msg: dict) -> None:
        try:
            self._ser.write((json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8"))
        except (serial.SerialException, OSError) as e:
            raise ConnectionError(f"write failed: {e}") from None

    def read(self) -> dict | None:
        """Next JSON message, or None if nothing complete arrived within ~50 ms. Non-JSON lines
        (boot messages, the debug console) are logged and skipped."""
        try:
            data = self._ser.read(self._ser.in_waiting or 1)
        except (serial.SerialException, OSError) as e:
            raise ConnectionError(f"read failed: {e}") from None
        if data:
            self._buf += data
        while b"\n" in self._buf:
            line, _, rest = self._buf.partition(b"\n")
            self._buf = bytearray(rest)
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            if text[0] == "{":
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    log.debug("bad json from board: %r", text[:120])
            else:
                log.debug("board: %s", text)
        return None

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


def open_board(port: str | None) -> SerialBoard:
    target = port or find_port()
    if not target:
        raise ConnectionError("no board found (looking for USB 303A:1001)")
    try:
        return SerialBoard(target)
    except (serial.SerialException, OSError) as e:
        raise ConnectionError(f"cannot open {target}: {e}") from None
