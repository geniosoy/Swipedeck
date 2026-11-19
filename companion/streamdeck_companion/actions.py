"""What a key press does on the PC.

Action objects (JSON, see deck.json):
  {"type":"hotkey","keys":"ctrl+shift+s"}            press the combination (also media/volume keys)
  {"type":"text","text":"git status\\n"}              type text ("\\n" = Enter)
  {"type":"open","target":"https://..."}             open a file, folder, URL or URI with the default app
  {"type":"run","command":["wt"]}                    start a program ("command" may be a string with "shell":true)
  {"type":"lock"}                                    lock the workstation
  {"type":"multi","steps":[ ...actions..., {"wait":0.3} ]}
  {"type":"http","method":"POST","url":"http://homeassistant.local:8123/api/services/light/toggle",
   "headers":{"Authorization":"Bearer ${HA_TOKEN}"},"json":{"entity_id":"light.desk"}}   send an HTTP request
  {"type":"obs","request":"SetCurrentProgramScene","data":{"sceneName":"Game"}}          control OBS (obs-websocket 5)
${NAME} in the url, headers, body and obs data is replaced by the environment variable NAME when the action runs, so a
token or password does not have to be written into deck.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .keys import EXTENDED, parse_hotkey

log = logging.getLogger("actions")

ACTION_TYPES = {"hotkey", "text", "open", "run", "lock", "multi", "http", "obs"}
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
OBS_REQUEST_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,79}$")
ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value, env=None):
    """Replaces ${NAME} by the environment variable NAME in every string of `value` (a string, list or dict).
    Raises ValueError, naming the variable, when one is not set."""
    env = os.environ if env is None else env
    if isinstance(value, str):
        def sub(m):
            if m.group(1) not in env:
                raise ValueError(f"the environment variable {m.group(1)} is not set")
            return env[m.group(1)]

        return ENV_RE.sub(sub, value)
    if isinstance(value, list):
        return [expand_env(v, env) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v, env) for k, v in value.items()}
    return value


def safe_url(url: str) -> str:
    """scheme://host[:port]/path: what goes into the log (a token may sit in the query string or the user info)."""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    return f"{p.scheme}://{host}{':' + str(p.port) if p.port else ''}{p.path}"


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
    elif kind == "http":
        url = action.get("url")
        if not (isinstance(url, str) and re.match(r"^https?://\S+$", url)):
            raise ValueError(f"{where}: 'url' must start with http:// or https://")
        if str(action.get("method", "GET")).upper() not in HTTP_METHODS:
            raise ValueError(f"{where}: 'method' must be one of {', '.join(HTTP_METHODS)}")
        headers = action.get("headers", {})
        if not (isinstance(headers, dict) and len(headers) <= 20
                and all(isinstance(k, str) and HEADER_NAME_RE.match(k) and isinstance(v, str) and "\n" not in v and "\r" not in v
                        for k, v in headers.items())):
            raise ValueError(f"{where}: 'headers' must be an object of up to 20 header names and one-line text values")
        if "json" in action and "body" in action:
            raise ValueError(f"{where}: give 'json' or 'body', not both")
        if "json" in action and not isinstance(action["json"], (dict, list)):
            raise ValueError(f"{where}: 'json' must be an object or a list")
        if "body" in action and not (isinstance(action["body"], str) and len(action["body"]) <= 100_000):
            raise ValueError(f"{where}: 'body' must be text")
        timeout = action.get("timeout", 5)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError(f"{where}: 'timeout' must be 1..30 seconds")
    elif kind == "obs":
        if not (isinstance(action.get("request"), str) and OBS_REQUEST_RE.match(action["request"])):
            raise ValueError(f"{where}: 'request' must be an obs-websocket request name, like ToggleRecord")
        if "data" in action and not isinstance(action["data"], dict):
            raise ValueError(f"{where}: 'data' must be an object")
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
        self.obs: dict = {}      # connection to OBS from deck.json ("obs": host, port, password); set by Companion.load_deck
        self._backend = backend  # (hotkey(codes), text(str), lock()) - injectable for tests
        self._lock = threading.RLock()  # one action at a time so key sequences never interleave

    def configure(self, obs: dict | None = None) -> None:
        self.obs = dict(obs or {})

    def _input(self):
        if self._backend is None:
            if sys.platform != "win32":
                raise OSError("key injection is only implemented for Windows")
            self._backend = _make_sendinput()
        return self._backend

    @staticmethod
    def _loggable(action: dict) -> dict:
        """The action for the log: an HTTP request without its headers, body and query (they may hold a token)."""
        if action.get("type") == "http":
            return {"type": "http", "method": str(action.get("method", "GET")).upper(), "url": safe_url(str(action.get("url", "")))}
        return action

    def _http(self, action: dict) -> None:
        url = expand_env(action["url"])
        method = str(action.get("method", "GET")).upper()
        headers = dict(expand_env(action.get("headers", {})))
        data = None
        if "json" in action:
            data = json.dumps(expand_env(action["json"])).encode("utf-8")
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/json"
        elif "body" in action:
            data = expand_env(action["body"]).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        shown = safe_url(url)
        try:
            with urllib.request.urlopen(request, timeout=action.get("timeout", 5)) as response:
                response.read(65536)
                log.info("http %s %s -> %s", method, shown, response.status)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {shown} answered {e.code} {e.reason}") from None
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"{method} {shown} failed: {getattr(e, 'reason', e)}") from None

    def _obs(self, action: dict) -> None:
        from .obs import obs_request

        answer = obs_request(expand_env(self.obs), action["request"], expand_env(action.get("data")))
        log.info("obs %s ok%s", action["request"], f" -> {answer}" if answer else "")

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
        log.info("run %s", self._loggable(action))
        if self.dry_run:
            return
        if kind in ("http", "obs"):  # over the network: they must not hold up key presses behind the one lock
            self._http(action) if kind == "http" else self._obs(action)
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
