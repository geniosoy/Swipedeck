"""What a key press does on the PC.

Action objects (JSON, see deck.json):
  {"type":"hotkey","keys":"ctrl+shift+s"}            press the combination (also media/volume keys)
  {"type":"text","text":"git status\\n"}              type text ("\\n" = Enter)
  {"type":"open","target":"https://..."}             open a file, folder, URL or URI with the default app
  {"type":"run","command":["wt"]}                    start a program ("command" may be a string with "shell":true)
  {"type":"lock"}                                    lock the workstation
  {"type":"multi","steps":[ ...actions..., {"wait":0.3} ]}
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Callable

from .keys import EXTENDED, parse_hotkey

log = logging.getLogger("actions")

ACTION_TYPES = {"hotkey", "text", "open", "run", "lock", "multi"}


def validate_action(action: dict, where: str = "action") -> None:
    """Raises ValueError with a readable message when an action is malformed."""
    if not isinstance(action, dict):
        raise ValueError(f"{where}: must be an object")
    kind = action.get("type")
    if kind not in ACTION_TYPES:
        raise ValueError(f"{where}: unknown type {kind!r} (use one of {sorted(ACTION_TYPES)})")
    if kind == "hotkey":
        try:
            parse_hotkey(str(action.get("keys", "")))
        except ValueError as e:
            raise ValueError(f"{where}: {e}") from None
    elif kind == "text":
        if not isinstance(action.get("text"), str) or not action["text"]:
            raise ValueError(f"{where}: 'text' must be a non-empty string")
    elif kind == "open":
        if not isinstance(action.get("target"), str) or not action["target"]:
            raise ValueError(f"{where}: 'target' must be a non-empty string")
    elif kind == "run":
        cmd = action.get("command")
        ok = (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)) or (
            isinstance(cmd, str) and cmd)
        if not ok:
            raise ValueError(f"{where}: 'command' must be a list of strings or a string")
        if isinstance(cmd, str) and not action.get("shell"):
            raise ValueError(f"{where}: a string 'command' needs \"shell\": true (or give a list)")
    elif kind == "multi":
        steps = action.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"{where}: 'steps' must be a non-empty list")
        for i, step in enumerate(steps):
            if isinstance(step, dict) and set(step) == {"wait"}:
                if not isinstance(step["wait"], (int, float)) or not 0 <= step["wait"] <= 30:
                    raise ValueError(f"{where}.steps[{i}]: wait must be 0..30 seconds")
            else:
                validate_action(step, f"{where}.steps[{i}]")


# ---- Windows input injection (ctypes SendInput) -------------------------------------------------

def _make_sendinput():
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4

    def send(events: list[tuple[int, int, int]]) -> None:
        """events: (vk, scan, flags). Sent in one call so the sequence is not interleaved."""
        arr = (INPUT * len(events))()
        for i, (vk, scan, flags) in enumerate(events):
            arr[i].type = 1  # INPUT_KEYBOARD
            arr[i].ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        if user32.SendInput(len(events), arr, ctypes.sizeof(INPUT)) != len(events):
            raise OSError(f"SendInput failed ({ctypes.get_last_error()})")

    def key_event(vk: int, up: bool) -> tuple[int, int, int]:
        flags = (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
        return (vk, 0, flags)

    def hotkey(codes: list[int]) -> None:
        send([key_event(c, False) for c in codes] + [key_event(c, True) for c in reversed(codes)])

    def text(s: str) -> None:
        events = []
        for ch in s.replace("\r\n", "\n"):
            if ch == "\n":
                events += [key_event(0x0D, False), key_event(0x0D, True)]
                continue
            for unit in _utf16_units(ch):
                events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
        for i in range(0, len(events), 64):
            send(events[i:i + 64])

    def lock() -> None:
        if not user32.LockWorkStation():
            raise OSError("LockWorkStation failed")

    return hotkey, text, lock


def _utf16_units(ch: str) -> list[int]:
    b = ch.encode("utf-16-le")
    return [int.from_bytes(b[i:i + 2], "little") for i in range(0, len(b), 2)]


class Executor:
    """Runs actions. `dry_run` only logs. Slow actions run on their own thread (see run_async)."""

    def __init__(self, dry_run: bool = False, backend: tuple[Callable, Callable, Callable] | None = None):
        self.dry_run = dry_run
        self._backend = backend  # (hotkey(codes), text(str), lock()) - injectable for tests
        self._lock = threading.RLock()  # one action at a time so key sequences never interleave

    def _input(self):
        if self._backend is None:
            if sys.platform != "win32":
                raise OSError("key injection is only implemented for Windows")
            self._backend = _make_sendinput()
        return self._backend

    def run_async(self, action: dict, label: str = "") -> threading.Thread:
        t = threading.Thread(target=self._safe_run, args=(action, label), daemon=True)
        t.start()
        return t

    def _safe_run(self, action: dict, label: str) -> None:
        try:
            self.run(action)
        except Exception as e:  # an action failing must never kill the companion
            log.error("action %s failed: %s", label or action.get("type"), e)

    def run(self, action: dict) -> None:
        kind = action["type"]
        log.info("run %s", action)
        if self.dry_run:
            return
        with self._lock:
            if kind == "hotkey":
                self._input()[0](parse_hotkey(action["keys"]))
            elif kind == "text":
                self._input()[1](action["text"])
            elif kind == "lock":
                self._input()[2]()
            elif kind == "open":
                os.startfile(action["target"])  # type: ignore[attr-defined]  (Windows only)
            elif kind == "run":
                cmd = action["command"]
                subprocess.Popen(cmd, shell=bool(action.get("shell")), cwd=action.get("cwd") or None,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif kind == "multi":
                for step in action["steps"]:
                    if set(step) == {"wait"}:
                        time.sleep(step["wait"])
                    else:
                        self.run(step)  # re-entrant lock: the whole sequence stays uninterrupted
