"""What the PC is doing, for the deck: which app is in front, whether the session is locked, whether the speakers or
the microphone are muted, which apps are running. Used for switching desktops by the active app, keys that show a
state, and turning the board's screen off while the PC is locked.

The pure helpers at the top are what the deck file and the tests use. `PcWatcher` reads Windows on a thread of its own
(COM for the audio state is set up on that thread only: doing it on another thread of this program once stopped the
Windows media calls from completing, see appicon.shell_icon).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger("companion.pcwatch")

STATE_SOURCES = ("mute", "mic_mute")   # plus "app:<program name>": that program is running
POLL_S = 0.5
MUTE_EVERY_S = 1.0
PROCESSES_EVERY_S = 2.0


# ---- pure helpers -----------------------------------------------------------------------------------------

def normalize_app(name: str) -> str:
    """A program name the way rules compare it: lower case, no spaces around it, no ".exe" ("OBS64.exe" -> "obs64")."""
    name = str(name).strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def check_state_source(source) -> str:
    """The source of a key's state, checked and normalised ("mute", "mic_mute" or "app:<name>"). Raises ValueError."""
    if source in STATE_SOURCES:
        return source
    if isinstance(source, str) and source.startswith("app:") and normalize_app(source[4:]):
        return "app:" + normalize_app(source[4:])
    raise ValueError(f"state source must be one of {', '.join(STATE_SOURCES)} or app:<program name>, not {source!r}")


@dataclass(frozen=True)
class PcState:
    foreground: str | None = None      # normalised name of the program in front, None = unknown
    locked: bool = False               # the Windows session is locked
    mute: bool | None = None           # speakers muted (None = unknown / not watched)
    mic_mute: bool | None = None       # default microphone muted
    running: frozenset = field(default_factory=frozenset)  # normalised names of running programs (only the watched ones)


def match_desktop(auto_apps: list, app: str | None) -> int | None:
    """The desktop to show for this foreground program: auto_apps is [(desktop index, {names})]; the first match wins."""
    if not app:
        return None
    for index, names in auto_apps:
        if app in names:
            return index
    return None


def key_states_on(key_states: list, pc: PcState) -> list[str]:
    """Which keys show their "on" look now, as sorted "desktop/page/key" strings. key_states: [(d, p, k, source)]."""
    on = []
    for d, p, k, source in key_states:
        if source == "mute":
            active = pc.mute is True
        elif source == "mic_mute":
            active = pc.mic_mute is True
        else:
            active = source[4:] in pc.running
        if active:
            on.append(f"{d}/{p}/{k}")
    return sorted(on)


# ---- Windows readers (each returns None when it cannot tell; none of them raises) ----------------------------

def foreground_app() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        import psutil

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return normalize_app(psutil.Process(pid.value).name()) if pid.value else None
    except Exception as e:
        log.debug("foreground app: %s", e)
        return None


def session_locked() -> bool | None:
    """True while the lock screen (the secure desktop) is the one receiving input."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP: refused on the secure desktop
        if not desktop:
            return True
        user32.CloseDesktop(ctypes.c_void_p(desktop))
        return False
    except Exception as e:
        log.debug("session lock: %s", e)
        return None


def _guid(text: str):
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(text)
    return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def _vcall(obj, index: int, *argtypes):
    """The COM method at `index` of the interface `obj` (a c_void_p), as a callable taking the arguments after `this`."""
    import ctypes

    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
    address = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    function = prototype(address)
    return lambda *args: function(obj, *args)


def endpoint_muted(capture: bool) -> bool | None:
    """Whether the default speakers (or, with capture=True, the default microphone) are muted. Windows Core Audio through
    ctypes (no extra package). Needs COM initialised on the calling thread (PcWatcher does that)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import POINTER, byref, c_int, c_void_p, wintypes

    held = []

    def release():
        for obj in reversed(held):
            _vcall(obj, 2)()  # IUnknown::Release

    try:
        ole32 = ctypes.windll.ole32
        enumerator = c_void_p()
        clsid, iid = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E"), _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
        if ole32.CoCreateInstance(byref(clsid), None, 23, byref(iid), byref(enumerator)) != 0:  # CLSCTX_ALL
            return None
        held.append(enumerator)
        device = c_void_p()
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(dataFlow: eRender 0 / eCapture 1, role: eConsole 0)
        if _vcall(enumerator, 4, c_int, c_int, POINTER(c_void_p))(1 if capture else 0, 0, byref(device)) != 0:
            return None
        held.append(device)
        volume = c_void_p()
        iid_volume = _guid("5CDF2C82-841E-4546-9722-0CF74078229A")  # IAudioEndpointVolume
        # IMMDevice::Activate(iid, clsctx, params, out interface)
        if _vcall(device, 3, POINTER(type(iid_volume)), wintypes.DWORD, c_void_p, POINTER(c_void_p))(byref(iid_volume), 23, None, byref(volume)) != 0:
            return None
        held.append(volume)
        muted = wintypes.BOOL()
        if _vcall(volume, 15, POINTER(wintypes.BOOL))(byref(muted)) != 0:  # IAudioEndpointVolume::GetMute
            return None
        return bool(muted.value)
    except Exception as e:
        log.debug("audio mute: %s", e)
        return None
    finally:
        try:
            release()
        except Exception:
            pass


def running_apps() -> frozenset:
    """Normalised names of all running programs."""
    try:
        import psutil

        return frozenset(normalize_app(p.info["name"]) for p in psutil.process_iter(["name"]) if p.info.get("name"))
    except Exception as e:
        log.debug("running apps: %s", e)
        return frozenset()


def windowed_apps() -> list[str]:
    """Names of the programs that have a visible window with a title (what you would think of as "open apps"), sorted.
    The editor offers these when you choose which app switches to a desktop."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        import psutil

        user32 = ctypes.windll.user32
        pids: set[int] = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pids.add(pid.value)
            return True

        user32.EnumWindows(visit, 0)
        names = set()
        for pid in pids:
            try:
                names.add(normalize_app(psutil.Process(pid).name()))
            except Exception:
                continue
        return sorted(names - {""})
    except Exception as e:
        log.debug("windowed apps: %s", e)
        return []


# ---- the watcher ------------------------------------------------------------------------------------------

class PcWatcher:
    """Polls the PC on a thread of its own, only for what `configure` says is needed (nothing is read otherwise)."""

    def __init__(self, readers: dict | None = None):
        self._readers = {"foreground": foreground_app, "locked": session_locked,
                         "mute": lambda: endpoint_muted(False), "mic": lambda: endpoint_muted(True),
                         "running": running_apps, **(readers or {})}
        self._lock = threading.Lock()
        self._wants = {"foreground": False, "lock": False, "mute": False, "mic": False, "apps": frozenset()}
        self._state = PcState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()   # a first reading has been taken

    def configure(self, foreground: bool = False, lock: bool = False, mute: bool = False, mic: bool = False,
                  apps=frozenset()) -> None:
        with self._lock:
            self._wants = {"foreground": foreground, "lock": lock, "mute": mute, "mic": mic, "apps": frozenset(apps)}

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="pcwatch", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> PcState | None:
        """The latest reading, or None before the first one."""
        return self._state if self._ready.is_set() else None

    def _run(self) -> None:
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED, for this thread only
            except Exception:
                pass
        last_mute = last_procs = 0.0
        locked_before = False
        mute = mic = None
        running = frozenset()
        while not self._stop.is_set():
            with self._lock:
                wants = dict(self._wants)
            now = time.monotonic()
            try:
                foreground = self._readers["foreground"]() if wants["foreground"] else None
                locked = False
                if wants["lock"]:
                    reading = self._readers["locked"]()
                    locked = bool(reading) and locked_before  # locked only after two readings in a row (UAC prompts flash by)
                    locked_before = bool(reading)
                if (wants["mute"] or wants["mic"]) and now - last_mute >= MUTE_EVERY_S:
                    last_mute = now
                    mute = self._readers["mute"]() if wants["mute"] else None
                    mic = self._readers["mic"]() if wants["mic"] else None
                if not (wants["mute"] or wants["mic"]):
                    mute = mic = None
                if wants["apps"] and now - last_procs >= PROCESSES_EVERY_S:
                    last_procs = now
                    running = frozenset(self._readers["running"]()) & wants["apps"]
                if not wants["apps"]:
                    running = frozenset()
                self._state = PcState(foreground=foreground, locked=locked, mute=mute, mic_mute=mic, running=running)
                self._ready.set()
            except Exception as e:  # a failing reader must never stop the thread
                log.debug("pc watch: %s", e)
            self._stop.wait(POLL_S)
