"""The volume mixer screen: the master volume and the volume of each program that plays sound (Windows Core Audio through
ctypes, no extra package).

`WindowsAudio` talks to Windows; `AudioMixer` runs it on a thread of its own (COM is set up on that thread only, see
pcwatch.py for why), keeps the latest reading, and applies the changes the board asks for. Both only work while the
mixer screen is visible.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time

from .pcwatch import _guid, _vcall, normalize_app

log = logging.getLogger("companion.mixer")

MAX_APPS = 4            # faders next to the master one
POLL_S = 0.25
IID_MANAGER2 = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"      # IAudioSessionManager2
IID_CONTROL2 = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"      # IAudioSessionControl2
IID_SIMPLE_VOLUME = "87CE5498-68D6-44E5-9215-6DA47EF883D8"  # ISimpleAudioVolume
IID_ENDPOINT_VOLUME = "5CDF2C82-841E-4546-9722-0CF74078229A"  # IAudioEndpointVolume
CLSID_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"


# ---- pure helpers -----------------------------------------------------------------------------------------

def display_name(program: str) -> str:
    """"chrome" -> "Chrome", "spotify" -> "Spotify" (what the fader is labelled with)."""
    name = normalize_app(program)
    return (name[:1].upper() + name[1:]) if name else "?"


def pick_apps(programs: list[dict], pins: list[str], limit: int = MAX_APPS) -> list[dict]:
    """Which programs get a fader. `programs`: [{"id", "vol", "mute", "active"}] (one per program); `pins`: program ids the
    user always wants first (when they are playing sound at all). Then the ones that are making sound now, then the rest
    alphabetically; at most `limit`."""
    by_id = {p["id"]: p for p in programs}
    chosen = [by_id[i] for i in dict.fromkeys(pins) if i in by_id]
    rest = [p for p in programs if p not in chosen]
    rest.sort(key=lambda p: (not p["active"], p["id"]))
    return [{"id": p["id"], "name": display_name(p["id"]), "vol": p["vol"], "mute": p["mute"]} for p in (chosen + rest)[:limit]]


def clamp_percent(value) -> int:
    return max(0, min(100, int(round(float(value)))))


# ---- Windows ------------------------------------------------------------------------------------------------

class WindowsAudio:
    """One reading / one change at a time; every COM object is released before returning. Call it only from the thread
    that did CoInitializeEx (AudioMixer's)."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("the volume mixer needs Windows")

    # -- plumbing

    def _query(self, obj, iid_text):
        import ctypes
        from ctypes import POINTER, byref, c_void_p

        iid = _guid(iid_text)
        out = c_void_p()
        if _vcall(obj, 0, POINTER(type(iid)), POINTER(c_void_p))(byref(iid), byref(out)) != 0:  # IUnknown::QueryInterface
            return None
        return out

    def _release(self, *objects):
        for obj in objects:
            if obj is not None and obj.value:
                try:
                    _vcall(obj, 2)()
                except Exception:
                    pass

    def _device(self):
        """The default output device (IMMDevice) as a c_void_p, or None."""
        import ctypes
        from ctypes import POINTER, byref, c_int, c_void_p

        enumerator = c_void_p()
        clsid, iid = _guid(CLSID_ENUMERATOR), _guid(IID_ENUMERATOR)
        if ctypes.windll.ole32.CoCreateInstance(byref(clsid), None, 23, byref(iid), byref(enumerator)) != 0:
            return None
        device = c_void_p()
        ok = _vcall(enumerator, 4, c_int, c_int, POINTER(c_void_p))(0, 0, byref(device)) == 0  # eRender, eConsole
        self._release(enumerator)
        return device if ok else None

    def _activate(self, device, iid_text):
        from ctypes import POINTER, byref, c_void_p
        from ctypes import wintypes

        iid = _guid(iid_text)
        out = c_void_p()
        if _vcall(device, 3, POINTER(type(iid)), wintypes.DWORD, c_void_p, POINTER(c_void_p))(byref(iid), 23, None, byref(out)) != 0:
            return None
        return out

    def _program_sessions(self, device):
        """[(program id, ISimpleAudioVolume, is_active)] for every session of every program (system sounds are left out).
        The caller releases the volume interfaces."""
        from ctypes import POINTER, byref, c_int, c_void_p
        from ctypes import wintypes

        import psutil

        manager = self._activate(device, IID_MANAGER2)
        if manager is None:
            return [], []
        held, found = [manager], []
        enumerator = c_void_p()
        if _vcall(manager, 5, POINTER(c_void_p))(byref(enumerator)) == 0:  # GetSessionEnumerator
            held.append(enumerator)
            count = c_int()
            _vcall(enumerator, 3, POINTER(c_int))(byref(count))
            for i in range(count.value):
                control = c_void_p()
                if _vcall(enumerator, 4, c_int, POINTER(c_void_p))(i, byref(control)) != 0:
                    continue
                control2 = self._query(control, IID_CONTROL2)
                simple = self._query(control, IID_SIMPLE_VOLUME)
                state = c_int()
                _vcall(control, 3, POINTER(c_int))(byref(state))  # GetState: 1 = active
                pid = wintypes.DWORD()
                system = control2 is None or _vcall(control2, 15)() == 0  # IsSystemSoundsSession: S_OK = yes
                if control2 is not None:
                    _vcall(control2, 14, POINTER(wintypes.DWORD))(byref(pid))  # GetProcessId
                self._release(control, control2)
                name = ""
                if pid.value and not system and simple is not None:
                    try:
                        name = normalize_app(psutil.Process(pid.value).name())
                    except Exception:
                        name = ""
                if name:
                    found.append((name, simple, state.value == 1))
                else:
                    self._release(simple)
        return held, found

    # -- reading and changing

    def read(self) -> dict:
        """{"master": {"vol", "mute"}, "programs": [{"id", "vol", "mute", "active"}]} (vol in percent)."""
        from ctypes import POINTER, byref, c_float
        from ctypes import wintypes

        device = self._device()
        if device is None:
            raise OSError("no audio output device")
        held = [device]
        try:
            master = {"vol": 0, "mute": False}
            endpoint = self._activate(device, IID_ENDPOINT_VOLUME)
            if endpoint is not None:
                held.append(endpoint)
                level, muted = c_float(), wintypes.BOOL()
                _vcall(endpoint, 9, POINTER(c_float))(byref(level))   # GetMasterVolumeLevelScalar
                _vcall(endpoint, 15, POINTER(wintypes.BOOL))(byref(muted))  # GetMute
                master = {"vol": clamp_percent(level.value * 100), "mute": bool(muted.value)}
            managers, sessions = self._program_sessions(device)
            held.extend(managers)
            grouped: dict[str, dict] = {}
            for name, simple, active in sessions:
                level, muted = c_float(), wintypes.BOOL()
                _vcall(simple, 4, POINTER(c_float))(byref(level))        # GetMasterVolume
                _vcall(simple, 6, POINTER(wintypes.BOOL))(byref(muted))  # GetMute
                g = grouped.setdefault(name, {"id": name, "vol": 0, "mute": True, "active": False})
                g["vol"] = max(g["vol"], clamp_percent(level.value * 100))  # a program with several sessions: the loudest
                g["mute"] = g["mute"] and bool(muted.value)
                g["active"] = g["active"] or active
                held.append(simple)
            return {"master": master, "programs": list(grouped.values())}
        finally:
            self._release(*held)

    def set_volume(self, target: str, percent) -> None:
        self._change(target, volume=clamp_percent(percent) / 100.0)

    def set_mute(self, target: str, mute: bool) -> None:
        self._change(target, mute=bool(mute))

    def _change(self, target: str, volume: float | None = None, mute: bool | None = None) -> None:
        from ctypes import c_float, c_int, c_void_p

        device = self._device()
        if device is None:
            raise OSError("no audio output device")
        held = [device]
        try:
            if target == "master":
                endpoint = self._activate(device, IID_ENDPOINT_VOLUME)
                if endpoint is None:
                    raise OSError("no master volume")
                held.append(endpoint)
                if volume is not None:
                    _vcall(endpoint, 7, c_float, c_void_p)(c_float(volume), None)  # SetMasterVolumeLevelScalar
                if mute is not None:
                    _vcall(endpoint, 14, c_int, c_void_p)(int(mute), None)         # SetMute
                return
            managers, sessions = self._program_sessions(device)
            held.extend(managers)
            for name, simple, _active in sessions:
                held.append(simple)
                if name != target:
                    continue
                if volume is not None:
                    _vcall(simple, 3, c_float, c_void_p)(c_float(volume), None)  # ISimpleAudioVolume::SetMasterVolume
                if mute is not None:
                    _vcall(simple, 5, c_int, c_void_p)(int(mute), None)          # SetMute
        finally:
            self._release(*held)


# ---- the thread ------------------------------------------------------------------------------------------------

class AudioMixer:
    """Reads the volumes (about four times a second while active) and applies changes on a thread of its own.
    `backend` is a WindowsAudio-like object (read / set_volume / set_mute); tests pass a fake."""

    def __init__(self, backend=None):
        self._backend = backend
        self._lock = threading.Lock()
        self._pins: list[str] = []
        self._active = threading.Event()
        self._stop = threading.Event()
        self._commands: queue.Queue = queue.Queue(maxsize=64)
        self._snapshot: dict | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def configure(self, pins: list[str]) -> None:
        with self._lock:
            self._pins = [normalize_app(p) for p in pins]

    def start(self) -> bool:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="mixer", daemon=True)
            self._thread.start()
        return self.error is None

    def stop(self) -> None:
        self._stop.set()

    def set_active(self, on: bool) -> None:
        if on:
            with self._lock:
                self._snapshot = None  # an older reading would be stale
            self._active.set()
        else:
            self._active.clear()

    def latest(self) -> tuple[int, dict] | None:
        """(sequence number, snapshot) of the newest reading, or None before the first one. The number changes when the
        volumes, the mute states or the faders change."""
        with self._lock:
            return (self._seq, self._snapshot) if self._snapshot is not None else None

    def set_volume(self, target: str, percent) -> None:
        self._submit(("volume", target, percent))

    def set_mute(self, target: str, mute: bool) -> None:
        self._submit(("mute", target, mute))

    def _submit(self, command) -> None:
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            log.debug("mixer command dropped (queue full)")

    def _run(self) -> None:
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED, for this thread only
            except Exception:
                pass
        if self._backend is None:
            try:
                self._backend = WindowsAudio()
            except OSError as e:
                self.error = str(e)
                return
        while not self._stop.is_set():
            if not self._active.is_set():
                self._drain_discarding()
                self._stop.wait(0.1)
                continue
            try:
                self._apply_commands()
                self._publish(self._backend.read())
            except Exception as e:  # a hiccup of the audio service: try again next time
                log.debug("mixer: %s", e)
            self._stop.wait(POLL_S)

    def _drain_discarding(self) -> None:
        while not self._commands.empty():
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break

    def _apply_commands(self) -> None:
        latest: dict = {}
        while True:  # a fader drag sends many values: only the newest per target and kind matters
            try:
                kind, target, value = self._commands.get_nowait()
            except queue.Empty:
                break
            latest[(kind, target)] = value
        for (kind, target), value in latest.items():
            try:
                (self._backend.set_volume if kind == "volume" else self._backend.set_mute)(target, value)
            except Exception as e:
                log.warning("mixer: could not change %s: %s", target, e)

    def _publish(self, reading: dict) -> None:
        with self._lock:
            pins = list(self._pins)
        snapshot = {"master": reading["master"], "apps": pick_apps(reading["programs"], pins)}
        with self._lock:
            if snapshot != self._snapshot:
                self._snapshot = snapshot
                self._seq += 1
