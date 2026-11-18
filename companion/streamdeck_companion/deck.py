"""Deck file: layout (sent to the board) + actions (run here on the PC) + settings."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .actions import validate_action

# Icon names the firmware knows (kIcons in src/ui.cpp; tests/test_icons.py keeps both in sync).
ICONS = {
    "audio", "video", "list", "ok", "close", "power", "settings", "home", "download", "upload", "refresh",
    "mute", "volume_down", "volume_up", "image", "edit", "prev", "next", "stop", "play", "pause", "eject",
    "left", "right", "up", "down", "plus", "minus", "eye", "eye_off", "warning", "shuffle", "loop",
    "directory", "file", "call", "cut", "copy", "paste", "save", "bell", "keyboard", "gps", "wifi",
    "bluetooth", "trash", "usb", "charge", "battery", "drive", "backspace",
}

MAX_DESKTOPS, MAX_PAGES = 8, 16
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class DeckError(ValueError):
    pass


class Deck:
    """A validated deck. `layout` is what the board receives; actions stay on the PC."""

    def __init__(self, data: dict):
        self.settings: dict = {}
        self.warnings: list[str] = []
        self.has_nowplaying = False  # a desktop with "kind": "nowplaying" (live media screen)
        self._actions: dict[tuple[int, int, int], dict] = {}
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

    def action_at(self, desktop: int, page: int, key: int) -> dict | None:
        return self._actions.get((desktop, page, key))

    # -- internals ---------------------------------------------------------------------------

    def _build(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise DeckError("deck must be a JSON object")
        shared = data.get("actions", {})
        if not isinstance(shared, dict):
            raise DeckError("'actions' must be an object (id -> action)")
        for aid, act in shared.items():
            self._check_action(act, f"actions[{aid!r}]")

        settings = data.get("settings", {})
        if not isinstance(settings, dict):
            raise DeckError("'settings' must be an object")
        for name, lo, hi in (("volume", 0, 100), ("brightness", 5, 255)):
            if name in settings and not (isinstance(settings[name], int) and lo <= settings[name] <= hi):
                raise DeckError(f"settings.{name} must be an integer {lo}..{hi}")
        if "sounds" in settings and not isinstance(settings["sounds"], bool):
            raise DeckError("settings.sounds must be true or false")
        self.settings = {k: settings[k] for k in ("volume", "sounds", "brightness") if k in settings}

        desktops = data.get("desktops")
        if not isinstance(desktops, list) or not desktops:
            raise DeckError("'desktops' must be a non-empty list")
        if len(desktops) > MAX_DESKTOPS:
            raise DeckError(f"at most {MAX_DESKTOPS} desktops")

        out_desktops = []
        for di, d in enumerate(desktops):
            where = f"desktops[{di}]"
            name = d.get("name", f"Desktop {di + 1}")
            kind = d.get("kind", "keys")
            if kind not in ("keys", "nowplaying"):
                raise DeckError(f"{where}: kind must be 'keys' or 'nowplaying'")
            if kind == "nowplaying":
                if self.has_nowplaying:
                    raise DeckError(f"{where}: only one 'nowplaying' desktop is supported")
                self.has_nowplaying = True
                out_desktops.append({"name": name, "kind": "nowplaying"})
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
            return out
        action = k.get("action")
        if action is None and out.get("id") in shared:
            action = shared[out["id"]]
        if action is not None:
            self._check_action(action, f"{where}.action")
            self._actions[(di, pi, ki)] = action
        else:
            self.warnings.append(f"{where}: '{out.get('label', '?')}' has no action")
        return out
