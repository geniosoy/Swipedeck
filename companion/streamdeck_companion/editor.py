"""Local web editor for deck.json.

The companion serves a single-page editor (web/) on http://127.0.0.1:8765. The editor reads and writes the deck
file through a small JSON API; the companion's file watcher then pushes the new layout to the board.

Because the deck contains actions that run on this PC (run / open / hotkey ...), writing it must not be possible
from other web pages you happen to visit. Defences:
  * the server listens on 127.0.0.1 only;
  * the Host header must be this server's own address (stops DNS-rebinding pages from talking to it);
  * writes need a random token that is only embedded in the editor page itself, sent as a custom header (a cross-origin
    page cannot read the token, and cannot send the header without a CORS preflight, which is never granted);
  * a browser's Origin header, when present, must be this server's own origin.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from .deck import Deck, DeckError

log = logging.getLogger("editor")

WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 2_000_000
LARGE_LAYOUT_BYTES = 30_000   # the board has only been tested with small layouts; warn beyond this
TOKEN_HEADER = "X-Streamdeck-Token"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".woff": "font/woff", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


# ---- deck file formatting: readable, one key per line ----------------------------------------------

def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _fmt_container(head: dict, list_name: str, items: list[str], indent: str) -> str:
    """{"a": 1, "<list_name>": [\\n  item,\\n  item\\n]} with each item on its own line."""
    head_txt = _compact(head)[1:-1]
    prefix = head_txt + ", " if head_txt else ""
    if not items:
        return "{" + prefix + f'"{list_name}": []' + "}"
    body = ",\n".join(items)
    return "{" + prefix + f'"{list_name}": [\n' + body + f"\n{indent}]" + "}"


def format_deck(data: dict) -> str:
    """Pretty text for a deck: structure indented, every key (and every other small object) on one line."""
    parts = []
    for name, value in data.items():
        if name == "desktops" and isinstance(value, list):
            desktops = []
            for d in value:
                pages = d.get("pages")
                if isinstance(pages, list):
                    page_items = []
                    for p in pages:
                        keys = ["          " + _compact(k) for k in p.get("keys", [])]
                        head = {k: v for k, v in p.items() if k != "keys"}
                        page_items.append("        " + _fmt_container(head, "keys", keys, "        "))
                    head = {k: v for k, v in d.items() if k != "pages"}
                    desktops.append("    " + _fmt_container(head, "pages", page_items, "    "))
                else:
                    desktops.append("    " + _compact(d))
            parts.append('  "desktops": [\n' + ",\n".join(desktops) + "\n  ]")
        else:
            parts.append(f"  {json.dumps(name)}: {_compact(value)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def atomic_write(path: Path, text: str, backup: bool = False) -> None:
    if backup and path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def validate_preset_key(key) -> None:
    """A user preset's key must be a valid deck key; folders are not allowed (they need a page to point at)."""
    if not isinstance(key, dict):
        raise DeckError("preset key must be an object")
    if key.get("folder"):
        raise DeckError("a preset cannot be a folder")
    Deck({"desktops": [{"name": "x", "pages": [{"id": "r", "cols": 1, "rows": 1, "keys": [key]}]}]})


# ---- server -----------------------------------------------------------------------------------------

class EditorServer:
    def __init__(self, deck_path: str | Path, host: str = "127.0.0.1", port: int = 8765,
                 status_fn: Callable[[], dict] | None = None, media_sources_fn: Callable[[], list] | None = None):
        self.deck_path = Path(deck_path)
        self._media_sources_fn = media_sources_fn
        self.presets_user_path = self.deck_path.with_name("presets.user.json")
        self.token = secrets.token_urlsafe(24)
        self._status_fn = status_fn or (lambda: {})
        self._write_lock = threading.Lock()
        self._httpd = ThreadingHTTPServer((host, port), self._handler_class())
        self._httpd.daemon_threads = True
        self.host, self.port = host, self._httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> str:
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="editor", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # -- request handling ----------------------------------------------------------------------

    def _handler_class(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "StreamdeckEditor"

            def log_message(self, fmt, *args):  # noqa: A003 - quiet: debug level only
                log.debug("%s " + fmt, self.address_string(), *args)

            # helpers
            def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", CSP)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj):
                self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), CONTENT_TYPES[".json"])

            def _guard(self, write: bool) -> bool:
                host = self.headers.get("Host", "")
                if host not in server.allowed_hosts:
                    self._json(403, {"error": "bad host"})
                    return False
                if write:
                    origin = self.headers.get("Origin")
                    if origin is not None and origin != f"http://{host}":
                        self._json(403, {"error": "bad origin"})
                        return False
                    supplied = self.headers.get(TOKEN_HEADER, "").encode("utf-8", "replace")
                    if not secrets.compare_digest(supplied, server.token.encode("utf-8")):
                        self._json(403, {"error": "missing or wrong token (reload the editor page)"})
                        return False
                return True

            def _read_body(self):
                """The request body, or None if it is missing or too large. It is read before the request is
                checked: answering (403 ...) while the client is still sending makes the OS reset the connection,
                and the client would never see the error message."""
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if not 0 < length <= MAX_BODY:
                    return None
                return self.rfile.read(length)

            def _parse_json(self, raw):
                if raw is None:
                    self._json(413, {"error": "body missing or too large"})
                    return None
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    self._json(400, {"error": f"invalid JSON: {e}"})
                    return None

            # routes
            def do_GET(self):  # noqa: N802
                if not self._guard(write=False):
                    return
                path = unquote(urlparse(self.path).path)
                if path in ("/", "/index.html"):
                    html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", server.token)
                    return self._send(200, html.encode("utf-8"), CONTENT_TYPES[".html"])
                if path == "/api/deck":
                    try:
                        return self._send(200, server.deck_path.read_bytes(), CONTENT_TYPES[".json"])
                    except OSError as e:
                        return self._json(500, {"error": str(e)})
                if path == "/api/presets":
                    return self._json(200, server.presets())
                if path == "/api/status":
                    return self._json(200, server._status_fn())
                if path == "/api/media-sources":
                    return self._media_sources()
                return self._static(path)

            def _media_sources(self):
                """The media players Windows knows about right now (for the Now playing filter in the editor)."""
                if server._media_sources_fn is None:
                    return self._json(501, {"error": "listing media players is not available here"})
                try:
                    return self._json(200, {"sources": server._media_sources_fn()})
                except Exception as e:  # winrt missing, not Windows, API hiccup: the editor shows the message
                    log.warning("media sources: %s", e)
                    return self._json(501, {"error": f"could not read the media players: {e}"})

            def _static(self, path: str):
                target = (WEB / path.lstrip("/")).resolve()
                try:
                    target.relative_to(WEB.resolve())
                except ValueError:
                    return self._json(404, {"error": "not found"})
                ctype = CONTENT_TYPES.get(target.suffix.lower())
                if ctype is None or not target.is_file():
                    return self._json(404, {"error": "not found"})
                self._send(200, target.read_bytes(), ctype)

            def do_PUT(self):  # noqa: N802
                raw = self._read_body()
                if not self._guard(write=True):
                    return
                path = unquote(urlparse(self.path).path)
                body = self._parse_json(raw)
                if body is None:
                    return
                if path == "/api/deck":
                    status, result = server.save_deck(body)
                elif path == "/api/presets/user":
                    status, result = server.save_user_presets(body)
                else:
                    status, result = 404, {"error": "not found"}
                self._json(status, result)

        return Handler

    # -- API operations (also used by the tests) -------------------------------------------------

    def save_deck(self, data) -> tuple[int, dict]:
        if not isinstance(data, dict):
            return 400, {"error": "the deck must be a JSON object"}
        try:
            deck = Deck(data)
        except (DeckError, AttributeError, TypeError, KeyError) as e:
            return 400, {"error": str(e)}
        size = len(deck.layout_text().encode("utf-8"))
        warnings = list(deck.warnings)
        if size > LARGE_LAYOUT_BYTES:
            warnings.append(f"the layout is {size} bytes; big layouts are untested on the board")
        with self._write_lock:
            atomic_write(self.deck_path, format_deck(data), backup=True)
        log.info("deck saved from the editor (%d bytes of layout)", size)
        return 200, {"ok": True, "warnings": warnings, "layoutBytes": size}

    def presets(self) -> dict:
        builtin = json.loads((WEB / "presets.json").read_text(encoding="utf-8"))
        user: list = []
        if self.presets_user_path.exists():
            try:
                user = json.loads(self.presets_user_path.read_text(encoding="utf-8")).get("presets", [])
            except (OSError, json.JSONDecodeError, AttributeError):
                log.warning("could not read %s", self.presets_user_path)
        return {"builtin": builtin["categories"], "user": user}

    def save_user_presets(self, data) -> tuple[int, dict]:
        presets = data.get("presets") if isinstance(data, dict) else None
        if not isinstance(presets, list) or len(presets) > 200:
            return 400, {"error": "expected {\"presets\": [...]} with at most 200 entries"}
        clean = []
        for i, p in enumerate(presets):
            if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip():
                return 400, {"error": f"preset {i}: needs a name"}
            try:
                validate_preset_key(p.get("key"))
            except (DeckError, AttributeError, TypeError) as e:
                return 400, {"error": f"preset '{p['name']}': {e}"}
            clean.append({"name": p["name"].strip()[:40], "key": p["key"]})
        with self._write_lock:
            atomic_write(self.presets_user_path,
                         json.dumps({"presets": clean}, indent=1, ensure_ascii=False) + "\n")
        return 200, {"ok": True, "count": len(clean)}
