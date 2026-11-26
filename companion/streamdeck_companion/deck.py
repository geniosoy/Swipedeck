"""Deck file: layout (sent to the board) + actions (run here on the PC) + settings."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .actions import validate_action
from .nowplaying import normalize_filter
from .pcwatch import check_state_source, normalize_app

# Icon names the firmware knows (kIcons in src/ui.cpp; tests/test_icons.py keeps both in sync).
ICONS = {
    "audio", "video", "list", "ok", "close", "power", "settings", "home", "download", "upload", "refresh",
    "mute", "volume_down", "volume_up", "image", "edit", "prev", "next", "stop", "play", "pause", "eject",
    "left", "right", "up", "down", "plus", "minus", "eye", "eye_off", "warning", "shuffle", "loop",
    "directory", "file", "call", "cut", "copy", "paste", "save", "bell", "keyboard", "gps", "wifi",
    "bluetooth", "trash", "usb", "charge", "battery", "drive", "backspace",
}

MAX_DESKTOPS, MAX_PAGES = 8, 16

# Options of the two live screens (see deck.json). Defaults apply when "options" is missing or partial.
NP_CONTROLS = ("full", "basic")
STATS_STYLES = ("gauges", "bars", "tiles")
LIVE_KINDS = ("nowplaying", "stats", "mixer", "clock")   # desktops without keys that the companion feeds; one of each at most
MIXER_MAX_PINS = 4
STAT_ITEMS = ("cpu", "gpu", "ram", "vram", "disk", "net")
SOUND_THEMES = ("classic", "soft", "click", "chirp")
SWIPE_LENGTHS = ("short", "normal", "long")
# General settings of the board: name -> (kind, default, extra). Ints carry (low, high); "theme" is one of SOUND_THEMES.
SETTINGS_SPEC = {
    "volume": ("int", 70, (0, 100)),
    "sounds": ("bool", True, None),            # key press sounds
    "soundTheme": ("theme", "classic", None),
    "navSounds": ("bool", True, None),         # folder / back sounds
    "brightness": ("int", 255, (5, 255)),
    "dimAfter": ("int", 0, (0, 3600)),         # seconds without a touch before the screen dims (0 = never)
    "sleepAfter": ("int", 0, (0, 3600)),       # ... before the backlight goes off (0 = never)
    "flip": ("bool", False, None),             # screen upside down
    "startDesktop": ("int", 0, (0, MAX_DESKTOPS - 1)),
    "keyRadius": ("int", 12, (0, 24)),
    "keyGap": ("int", 6, (2, 12)),
    "swipe": ("swipe", "normal", None),        # how far / hard a swipe must be to pass to another desktop
    "wrap": ("bool", False, None),             # endless scrolling: swiping past the last desktop wraps to the first
    "sleepWithPc": ("bool", False, None),      # screen off while the PC is locked, asleep or not talking to the board
}


def default_settings() -> dict:
    return {name: default for name, (_kind, default, _extra) in SETTINGS_SPEC.items()}


def check_settings(settings) -> dict:
    """The settings object of a deck (or of an editor request), checked; only the keys that are present are returned."""
    if not isinstance(settings, dict):
        raise DeckError("'settings' must be an object")
    out = {}
    for name, (kind, _default, extra) in SETTINGS_SPEC.items():
        if name not in settings:
            continue
        v = settings[name]
        if kind == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not extra[0] <= v <= extra[1]:
                raise DeckError(f"settings.{name} must be an integer {extra[0]}..{extra[1]}")
        elif kind == "bool":
            if not isinstance(v, bool):
                raise DeckError(f"settings.{name} must be true or false")
        else:
            choices = SWIPE_LENGTHS if kind == "swipe" else SOUND_THEMES
            if v not in choices:
                raise DeckError(f"settings.{name} must be one of {', '.join(choices)}")
        out[name] = v
    return out


def check_obs(obs) -> dict:
    """The top-level "obs" object of a deck: how to reach OBS's WebSocket server. Only the fields that are present are
    returned (the defaults, 127.0.0.1:4455 and no password, are applied when an obs action runs). The password may be
    "${OBS_PASSWORD}": the environment variable is read when the action runs."""
    if obs is None:
        return {}
    if not isinstance(obs, dict):
        raise DeckError("'obs' must be an object")
    out = {}
    for name in obs:
        if name not in ("host", "port", "password"):
            raise DeckError(f"obs.{name}: unknown field (use host, port, password)")
    if "host" in obs:
        if not (isinstance(obs["host"], str) and 0 < len(obs["host"]) <= 200 and not any(c.isspace() for c in obs["host"])):
            raise DeckError("obs.host must be a host name or address, like 127.0.0.1")
        out["host"] = obs["host"]
    if "port" in obs:
        if isinstance(obs["port"], bool) or not isinstance(obs["port"], int) or not 1 <= obs["port"] <= 65535:
            raise DeckError("obs.port must be an integer 1..65535")
        out["port"] = obs["port"]
    if "password" in obs:
        if not (isinstance(obs["password"], str) and len(obs["password"]) <= 200):
            raise DeckError("obs.password must be text (or ${OBS_PASSWORD} to read an environment variable)")
        out["password"] = obs["password"]
    return out


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class DeckError(ValueError):
    pass


class Deck:
    """A validated deck. `layout` is what the board receives; actions stay on the PC."""

    def __init__(self, data: dict):
        self.settings: dict = {}
        self.warnings: list[str] = []
        self.has_nowplaying = False  # a desktop with "kind": "nowplaying" (live media screen)
        self.has_stats = False       # a desktop with "kind": "stats" (PC statistics)
        self.has_mixer = False       # a desktop with "kind": "mixer" (volume faders)
        self.has_clock = False       # a desktop with "kind": "clock" (clock, timer, weather)
        self.mixer_pins: list[str] = []          # programs the mixer always shows first (PC only)
        self.clock_place: dict | None = None     # {"name", "lat", "lon"} for the weather (PC only)
        self.clock_units = "c"                   # "c" or "f"
        self.clock_weather = False               # the clock screen shows the weather (it is on and a place is chosen)
        # Which media players the Now playing screen may show; stays on the PC (the board never sees it).
        self.np_filter: dict = {"allow": [], "block": []}
        self.np_app_icon = False     # show the icon of the playing app (the companion draws and sends it)
        self._actions: dict[tuple[int, int, int], dict] = {}
        self._holds: dict[tuple[int, int, int], dict] = {}      # the action of a key when it is held
        # Desktops that come to the front when one of their programs is the active window: [(desktop index, {names})].
        # Like the actions, this stays on the PC.
        self.auto_apps: list[tuple[int, set]] = []
        # Keys whose look follows a state of the PC: [(desktop, page, key, source)] (see pcwatch.check_state_source)
        self.key_states: list[tuple[int, int, int, str]] = []
        self.obs: dict = {}          # how to reach OBS for "obs" actions (see check_obs); PC only
        self.layout = self._build(data)

    @classmethod
    def load(cls, path: str | Path) -> "Deck":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise DeckError(f"{path}: invalid JSON ({e})") from None
        return cls(data)

    def layout_text(self) -> str:
        """The exact text sent to the board (and hashed for the CRC)."""
        return json.dumps(self.layout, separators=(",", ":"), ensure_ascii=False)

    def effective_settings(self) -> dict:
        """Every setting with its value: what deck.json says, else the default."""
        return {**default_settings(), **self.settings}

    def action_at(self, desktop: int, page: int, key: int) -> dict | None:
        return self._actions.get((desktop, page, key))

    def hold_action_at(self, desktop: int, page: int, key: int) -> dict | None:
        return self._holds.get((desktop, page, key))

    # -- internals ---------------------------------------------------------------------------

    def _build(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise DeckError("deck must be a JSON object")
        shared = data.get("actions", {})
        if not isinstance(shared, dict):
            raise DeckError("'actions' must be an object (id -> action)")
        for aid, act in shared.items():
            self._check_action(act, f"actions[{aid!r}]")

        self.settings = check_settings(data.get("settings", {}))
        self.obs = check_obs(data.get("obs"))

        desktops = data.get("desktops")
        if not isinstance(desktops, list) or not desktops:
            raise DeckError("'desktops' must be a non-empty list")
        if len(desktops) > MAX_DESKTOPS:
            raise DeckError(f"at most {MAX_DESKTOPS} desktops")
        if self.settings.get("startDesktop", 0) >= len(desktops):
            self.warnings.append(f"settings.startDesktop is {self.settings['startDesktop']} but there are only "
                                 f"{len(desktops)} desktop(s); the first one is used")

        out_desktops = []
        for di, d in enumerate(desktops):
            where = f"desktops[{di}]"
            name = d.get("name", f"Desktop {di + 1}")
            kind = d.get("kind", "keys")
            if kind not in ("keys",) + LIVE_KINDS:
                raise DeckError(f"{where}: kind must be 'keys', 'nowplaying', 'stats', 'mixer' or 'clock'")
            self._auto_apps(d.get("apps"), di, where, desktops)
            if kind in LIVE_KINDS:
                if getattr(self, f"has_{kind}"):
                    raise DeckError(f"{where}: only one '{kind}' desktop is supported")
                setattr(self, f"has_{kind}", True)
                out_desktops.append({"name": name, "kind": kind, "options": self._live_options(kind, d.get("options"), where)})
                continue
            pages = d.get("pages")
            if not isinstance(pages, list) or not pages:
                raise DeckError(f"{where}: 'pages' must be a non-empty list")
            if len(pages) > MAX_PAGES:
                raise DeckError(f"{where}: at most {MAX_PAGES} pages")
            page_ids = [p.get("id", "") for p in pages]
            out_pages = []
            for pi, p in enumerate(pages):
                pw = f"{where}.pages[{pi}]"
                cols, rows = p.get("cols", 4), p.get("rows", 3)
                if not (isinstance(cols, int) and 1 <= cols <= 6 and isinstance(rows, int) and 1 <= rows <= 5):
                    raise DeckError(f"{pw}: cols must be 1..6 and rows 1..5")
                keys = p.get("keys", [])
                if not isinstance(keys, list):
                    raise DeckError(f"{pw}: 'keys' must be a list")
                if len(keys) > cols * rows:
                    self.warnings.append(f"{pw}: {len(keys)} keys but only {cols * rows} slots, extra keys dropped")
                    keys = keys[:cols * rows]
                out_keys = []
                for ki, k in enumerate(keys):
                    out_keys.append(self._key(k, di, pi, ki, f"{pw}.keys[{ki}]", page_ids, shared))
                out_pages.append({"id": p.get("id", ""), "title": p.get("title", name), "cols": cols,
                                  "rows": rows, "keys": out_keys})
            out_desktops.append({"name": name, "pages": out_pages})
        return {"desktops": out_desktops}

    def _auto_apps(self, apps, di: int, where: str, desktops: list) -> None:
        """`apps` of a desktop: programs that bring this desktop to the front when their window is the active one."""
        if apps is None:
            return
        if not (isinstance(apps, list) and len(apps) <= 10
                and all(isinstance(a, str) and 0 < len(normalize_app(a)) and len(a) <= 60 for a in apps)):
            raise DeckError(f"{where}.apps must be a list of up to 10 program names, like [\"obs64.exe\", \"code\"]")
        names = {normalize_app(a) for a in apps}
        taken = {n: i for i, ns in self.auto_apps for n in ns}
        for n in sorted(names & set(taken)):
            winner = desktops[taken[n]].get("name", f"Desktop {taken[n] + 1}")
            self.warnings.append(f'{where}.apps: "{n}" already switches to "{winner}".')
        if names:
            self.auto_apps.append((di, names))

    def _state(self, state, di: int, pi: int, ki: int, where: str) -> dict:
        """A key's `state`: {"source": ..., "color"?, "icon"?, "label"?}. Returns the look the board shows while the
        source is on (a color is always filled in) and remembers the source on the PC."""
        if not isinstance(state, dict):
            raise DeckError(f"{where}.state must be an object")
        for extra in sorted(set(state) - {"source", "color", "icon", "label"}):
            self.warnings.append(f"{where}.state: unknown field '{extra}' ignored")
        try:
            source = check_state_source(state.get("source"))
        except ValueError as e:
            raise DeckError(f"{where}.state: {e}") from None
        look = {}
        if "color" in state:
            if not (isinstance(state["color"], str) and COLOR_RE.match(state["color"])):
                raise DeckError(f"{where}.state: color must look like #RRGGBB")
            look["color"] = state["color"]
        for field in ("icon", "label"):
            if field in state:
                if not isinstance(state[field], str):
                    raise DeckError(f"{where}.state: '{field}' must be a string")
                look[field] = state[field]
        if look.get("icon") and look["icon"] not in ICONS:
            self.warnings.append(f"{where}.state: unknown icon '{look['icon']}' (shows a warning symbol)")
        look.setdefault("color", "#2EA043" if source.startswith("app:") else "#B3261E")  # green: running, red: muted
        self.key_states.append((di, pi, ki, source))
        return look

    def _live_options(self, kind: str, opts, where: str) -> dict:
        """Validates a live screen's options. Returns what the board needs; the player filter stays on the PC."""
        if opts is None:
            opts = {}
        if not isinstance(opts, dict):
            raise DeckError(f"{where}.options must be an object")
        known = {"nowplaying": {"filter", "tint", "album", "controls", "appIcon"}, "stats": {"style", "items"},
                 "mixer": {"apps"}, "clock": {"hour12", "seconds", "timer", "weather", "place", "units"}}[kind]
        for extra in sorted(set(opts) - known):
            self.warnings.append(f"{where}.options: unknown option '{extra}' ignored")

        def flag(name: str, default: bool) -> bool:
            value = opts.get(name, default)
            if not isinstance(value, bool):
                raise DeckError(f"{where}.options.{name} must be true or false")
            return value

        def choice(name: str, allowed: tuple, default: str) -> str:
            value = opts.get(name, default)
            if value not in allowed:
                raise DeckError(f"{where}.options.{name} must be one of {', '.join(allowed)}")
            return value

        if kind == "nowplaying":
            flt = opts.get("filter", {})
            if not isinstance(flt, dict):
                raise DeckError(f"{where}.options.filter must be an object")
            legacy = ("mode" in flt) or ("apps" in flt)  # the older form: {"mode": "all|only|except", "apps": [...]}
            if legacy and flt.get("mode", "all") not in ("all", "only", "except"):
                raise DeckError(f"{where}.options.filter.mode must be one of all, only, except")
            lists = {"apps": flt.get("apps", [])} if legacy else {"allow": flt.get("allow", []), "block": flt.get("block", [])}
            for name, names in lists.items():
                if not (isinstance(names, list) and len(names) <= 30 and all(isinstance(a, str) and 0 < len(a) <= 80 for a in names)):
                    raise DeckError(f"{where}.options.filter.{name} must be a list of up to 30 names")
            self.np_filter = normalize_filter(flt)
            both = {a.lower() for a in self.np_filter["allow"]} & {b.lower() for b in self.np_filter["block"]}
            if both:
                self.warnings.append(f"{where}.options.filter: {', '.join(sorted(both))} is on both lists (it will never be shown)")
            self.np_app_icon = flag("appIcon", False)
            return {"tint": flag("tint", True), "album": flag("album", True), "controls": choice("controls", NP_CONTROLS, "full"),
                    "appIcon": self.np_app_icon}

        if kind == "mixer":
            pins = opts.get("apps", [])
            if not (isinstance(pins, list) and len(pins) <= MIXER_MAX_PINS
                    and all(isinstance(a, str) and 0 < len(normalize_app(a)) <= 60 for a in pins)):
                raise DeckError(f"{where}.options.apps must be a list of up to {MIXER_MAX_PINS} program names")
            self.mixer_pins = list(dict.fromkeys(normalize_app(a) for a in pins))
            return {}  # the board always shows the master fader and the programs the PC sends

        if kind == "clock":
            place = opts.get("place")
            self.clock_place = self._place(place, where) if place is not None else None
            self.clock_units = choice("units", ("c", "f"), "c")
            weather = flag("weather", True)
            if weather and self.clock_place is None:
                self.warnings.append(f"{where}.options: weather is on but no place is chosen, so the weather is not shown")
            self.clock_weather = weather and self.clock_place is not None
            return {"hour12": flag("hour12", False), "seconds": flag("seconds", False), "timer": flag("timer", True),
                    "weather": self.clock_weather}

        items = opts.get("items", list(STAT_ITEMS))
        if not (isinstance(items, list) and items and len(items) == len(set(items)) and all(i in STAT_ITEMS for i in items)):
            raise DeckError(f"{where}.options.items must be a non-empty list without repeats of: {', '.join(STAT_ITEMS)}")
        return {"style": choice("style", STATS_STYLES, "gauges"), "items": list(items)}

    @staticmethod
    def _place(place, where: str) -> dict:
        """The `place` option of the clock screen: {"name", "lat", "lon"}."""
        def number(name, low, high):
            v = place.get(name) if isinstance(place, dict) else None
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
                raise DeckError(f"{where}.options.place.{name} must be a number {low}..{high}")
            return round(float(v), 4)

        if not isinstance(place, dict):
            raise DeckError(f"{where}.options.place must be an object like {{\"name\": \"Paris\", \"lat\": 48.85, \"lon\": 2.35}}")
        name = place.get("name", "")
        if not (isinstance(name, str) and 0 < len(name) <= 60):
            raise DeckError(f"{where}.options.place.name must be 1 to 60 characters")
        return {"name": name, "lat": number("lat", -90, 90), "lon": number("lon", -180, 180)}

    def _check_action(self, act, where: str) -> None:
        try:
            validate_action(act, where)
        except ValueError as e:
            raise DeckError(str(e)) from None

    def _key(self, k: dict, di: int, pi: int, ki: int, where: str, page_ids: list, shared: dict) -> dict:
        if not isinstance(k, dict):
            raise DeckError(f"{where}: must be an object")
        if not k:
            return {}
        out = {}
        for field in ("label", "icon", "id"):
            if field in k:
                if not isinstance(k[field], str):
                    raise DeckError(f"{where}: '{field}' must be a string")
                out[field] = k[field]
        if "color" in k:
            if not (isinstance(k["color"], str) and COLOR_RE.match(k["color"])):
                raise DeckError(f"{where}: color must look like #RRGGBB")
            out["color"] = k["color"]
        if out.get("icon") and out["icon"] not in ICONS:
            self.warnings.append(f"{where}: unknown icon '{out['icon']}' (shows a warning symbol)")
        folder = k.get("folder")
        if folder:
            if folder not in page_ids:
                raise DeckError(f"{where}: folder '{folder}' is not a page id of this desktop")
            out["folder"] = folder
            if any(k.get(f) not in (None, False) for f in ("hold", "repeat", "state")):
                self.warnings.append(f"{where}: a folder ignores 'hold', 'repeat' and 'state'")
            return out
        action = k.get("action")
        if action is None and out.get("id") in shared:
            action = shared[out["id"]]
        if action is not None:
            self._check_action(action, f"{where}.action")
            self._actions[(di, pi, ki)] = action
        else:
            self.warnings.append(f"{where}: This key does nothing. Pick an action.")
        repeat, hold = k.get("repeat", False), k.get("hold")
        if not isinstance(repeat, bool):
            raise DeckError(f"{where}: 'repeat' must be true or false")
        if hold is not None and repeat:
            raise DeckError(f"{where}: a key can have 'hold' or 'repeat', not both")
        if hold is not None:  # a second action, run when the key is held
            if isinstance(hold, str):
                if hold not in shared:
                    raise DeckError(f"{where}.hold: there is no shared action {hold!r}")
                hold = shared[hold]
            self._check_action(hold, f"{where}.hold")
            self._holds[(di, pi, ki)] = hold
            out["hold"] = True
        if repeat:
            out["repeat"] = True  # holding the key presses it again and again (volume keys)
        if k.get("state") is not None:
            out["on"] = self._state(k["state"], di, pi, ki, where)
        return out
