"""The companion: keeps the board connected, syncs the deck, runs the actions of pressed keys."""
from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path
from typing import Callable

from .actions import Executor
from .deck import Deck, DeckError
from .link import open_board
from .nowplaying import COVER_PX_DEFAULT, NowPlayingSync, cover_messages
from .protocol import crc_hex, layout_messages

log = logging.getLogger("companion")

HELLO_TIMEOUT = 15.0   # generous: the board may be booting (just plugged in or flashed); opening the port does not reset it
PING_EVERY = 2.0
SILENCE_LIMIT = 8.0    # no message at all for this long -> treat the link as dead
ACK_TIMEOUT = 5.0


class Companion:
    def __init__(self, deck_path: str | Path, port: str | None = None, executor: Executor | None = None,
                 open_board_fn: Callable = open_board, clock: Callable[[], float] = time.monotonic,
                 reader=None, media: bool = True, stats=None, stats_enabled: bool = True):
        self.deck_path = Path(deck_path)
        self.port = port
        self.executor = executor or Executor()
        self._open_board = open_board_fn
        self._clock = clock
        self.deck: Deck | None = None
        self._deck_mtime = 0.0
        self._stop = False
        self._reader = reader          # MediaReader-like: snapshot(), cover(), command(), seek()
        self._media = media            # False (--no-media) never touches the Windows media API
        self._cover_px = COVER_PX_DEFAULT
        self._stats = stats            # StatsReader-like: set_active(), latest()
        self._stats_enabled = stats_enabled
        self._stats_sent = 0           # sequence number of the last sample sent to the board
        self._active_desktop = 0       # which desktop the board is showing
        self._shown_kind = "keys"      # kind of that desktop: keys | nowplaying | stats
        # What the web editor shows in its status line (read from another thread; plain values only).
        self.status: dict = {"connected": False, "port": None, "firmware": None, "layoutCrc": None,
                             "desktop": 0, "kind": "keys"}
        self.load_deck(first=True)

    # -- deck ----------------------------------------------------------------------------------

    def load_deck(self, first: bool = False) -> bool:
        try:
            deck = Deck.load(self.deck_path)
        except (DeckError, OSError) as e:
            log.error("deck not loaded: %s", e)
            if first:
                raise
            return False
        for w in deck.warnings:
            log.warning("deck: %s", w)
        self.deck = deck
        if self._reader is not None:
            self._reader.set_filter(deck.np_filter)
        self._deck_mtime = self.deck_path.stat().st_mtime
        log.info("deck loaded: %d desktop(s)", len(deck.layout["desktops"]))
        return True

    def _deck_changed(self) -> bool:
        try:
            return self.deck_path.stat().st_mtime != self._deck_mtime
        except OSError:
            return False

    # -- run loop ------------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop = True

    def run_forever(self, retry_seconds: float = 2.0) -> None:
        while not self._stop:
            board = None
            try:
                board = self._open_board(self.port)
                log.info("connected on %s", getattr(board, "port", "?"))
                self.status.update(port=getattr(board, "port", None))
                self.session(board)
            except ConnectionError as e:
                log.warning("link: %s", e)
            finally:
                self.status.update(connected=False, firmware=None)
                if board is not None:
                    board.close()
            if not self._stop:
                time.sleep(retry_seconds)

    def session(self, board) -> None:
        hello = self._handshake(board)
        log.info("board firmware %s, screen %sx%s, layout crc %r", hello.get("firmware"),
                 hello.get("screen", {}).get("w"), hello.get("screen", {}).get("h"), hello.get("layoutCrc"))
        self._cover_px = int(hello.get("coverPx") or COVER_PX_DEFAULT)
        self._active_desktop = int(hello.get("desktop") or 0)
        self._shown_kind = "keys"
        self.status.update(connected=True, firmware=hello.get("firmware"), layoutCrc=hello.get("layoutCrc"))
        self._push_settings(board)
        board_crc = self._sync_layout(board, hello.get("layoutCrc", ""))
        nps = NowPlayingSync()  # a new connection starts from scratch: the board may have lost its state

        last_rx = last_ping = self._clock()
        ping_id = 0
        try:
            while not self._stop:
                msg = board.read()
                now = self._clock()
                if msg is not None:
                    last_rx = now
                    self.handle(msg)
                if now - last_rx > SILENCE_LIMIT:
                    raise ConnectionError("board stopped answering")
                if now - last_ping >= PING_EVERY:
                    ping_id += 1
                    board.send({"type": "ping", "id": ping_id})
                    last_ping = now
                self._update_live(board, nps, now)
                if self._deck_changed() and self.load_deck():
                    self._push_settings(board)
                    board_crc = self._sync_layout(board, board_crc)
        finally:
            self._switch_live(self._shown_kind, "keys", nps)  # the link is gone: stop all background work
            self._shown_kind = "keys"

    # -- protocol steps ------------------------------------------------------------------------

    def _handshake(self, board) -> dict:
        deadline = self._clock() + HELLO_TIMEOUT
        next_hello = 0.0
        while self._clock() < deadline:
            if self._clock() >= next_hello:
                board.send({"type": "hello"})
                next_hello = self._clock() + 0.5
            msg = board.read()
            if msg and msg.get("type") == "hello":
                return msg
        raise ConnectionError("no hello from the board (is the Streamdeck firmware flashed?)")

    def _push_settings(self, board) -> None:
        if self.deck and self.deck.settings:
            board.send({"type": "settings", **self.deck.settings})
            self._wait_for(board, {"settings-ack"})

    def _sync_layout(self, board, board_crc: str) -> str:
        """Sends the layout unless the board already has it. Returns the CRC the board holds afterwards."""
        assert self.deck is not None
        text = self.deck.layout_text()
        crc = crc_hex(text)
        if crc == board_crc:
            log.info("layout already on the board (crc %s)", crc)
            return crc
        msgs = layout_messages(text)
        log.info("sending layout: %d bytes in %d message(s)", len(text.encode("utf-8")), len(msgs))
        for m in msgs:
            board.send(m)
            last = m["seq"] == m["of"] - 1
            reply = self._wait_for(board, {"layout-ack"} if last else {"layout-part", "layout-ack"})
            if reply["type"] == "layout-ack":
                if not reply.get("ok"):
                    raise ConnectionError(f"board rejected the layout: {reply.get('error')}")
                if reply.get("crc") != crc:
                    log.warning("layout crc differs: board %s, pc %s", reply.get("crc"), crc)
                log.info("layout accepted (crc %s)", reply.get("crc"))
                self._active_desktop = 0  # a freshly loaded layout starts on the first desktop
                self.status.update(layoutCrc=crc)
                return crc
        raise ConnectionError("layout transfer ended without an acknowledgement")

    def _wait_for(self, board, types: set[str]) -> dict:
        deadline = self._clock() + ACK_TIMEOUT
        while self._clock() < deadline:
            msg = board.read()
            if msg is None:
                continue
            if msg.get("type") in types:
                return msg
            self.handle(msg)  # key presses etc. that arrive meanwhile are not lost
        raise ConnectionError(f"timeout waiting for {sorted(types)}")

    # -- live screens (now playing, stats): work only while their desktop is visible ----------------

    def _kind_of(self, index: int) -> str:
        desktops = self.deck.layout["desktops"] if self.deck else []
        return desktops[index].get("kind", "keys") if 0 <= index < len(desktops) else "keys"

    def _update_live(self, board, nps: NowPlayingSync, now: float) -> None:
        kind = self._kind_of(self._active_desktop)
        self.status.update(desktop=self._active_desktop, kind=kind)
        if kind != self._shown_kind:
            self._switch_live(self._shown_kind, kind, nps)
            self._shown_kind = kind
        if kind == "nowplaying":
            self._pump_nowplaying(board, nps, now)
        elif kind == "stats":
            self._pump_stats(board)

    def _switch_live(self, old: str, new: str, nps: NowPlayingSync) -> None:
        """Stops the background work of the screen that was left, starts that of the one entered."""
        if old != new:
            log.debug("screen %s -> %s", old, new)
        if old == "nowplaying" and old != new and self._reader is not None:
            self._reader.set_active(False)
        if old == "stats" and old != new and self._stats is not None:
            self._stats.set_active(False)
        if new == "nowplaying" and old != new:
            reader = self._media_reader()
            if reader is not None:
                reader.set_filter(self.deck.np_filter)
                reader.set_active(True)
                nps.resend()  # the board shows what we sent last: send the current state again
        if new == "stats" and old != new:
            stats = self._stats_reader()
            if stats is not None:
                stats.set_active(True)
                self._stats_sent = 0

    def _stats_reader(self):
        if not self._stats_enabled or not (self.deck and self.deck.has_stats):
            return None
        if self._stats is None:
            from .stats import StatsReader

            reader = StatsReader()
            if not reader.start():
                log.warning("Stats disabled: %s", reader.error)
                self._stats_enabled = False
                return None
            self._stats = reader
        return self._stats

    def _pump_stats(self, board) -> None:
        stats = self._stats_reader()
        latest = stats.latest() if stats is not None else None
        if latest is not None and latest[0] != self._stats_sent:
            self._stats_sent = latest[0]
            board.send({"type": "stats", **latest[1]})

    # -- now playing -----------------------------------------------------------------------------

    def _media_reader(self):
        """The media reader, started on first use; None when disabled, unnecessary or unavailable."""
        if not self._media or not (self.deck and self.deck.has_nowplaying):
            return None
        if self._reader is None:
            from .media import MediaReader  # imported late: needs the optional winrt packages

            reader = MediaReader(self._cover_px)
            if not reader.start():
                log.warning("Now playing disabled: %s", reader.error)
                self._media = False
                return None
            self._reader = reader
            log.info("Now playing: watching the Windows media session")
        return self._reader

    def _pump_nowplaying(self, board, nps: NowPlayingSync, now: float) -> None:
        reader = self._media_reader()
        if reader is None or not reader.ready():  # not before the first reading since the screen appeared
            return
        cover = reader.cover()
        snap = dataclasses.replace(reader.snapshot(), cover_key=cover[0] if cover else None)
        action = nps.cover_action(snap)  # cover first, so the image is there when the text changes
        if action == "clear":
            board.send({"type": "cover-clear"})
            nps.cover_sent = ""
        elif action == "send" and cover:
            self._send_cover(board, cover[0], cover[1], cover[2])
            nps.cover_sent = cover[0]
        msg = nps.update(snap, now)
        if msg:
            board.send(msg)

    def _send_cover(self, board, key: str, rgb565: bytes, bg: str) -> None:
        msgs = cover_messages(key, rgb565, self._cover_px, bg)
        log.info("sending cover %s: %d bytes in %d message(s)", key, len(rgb565), len(msgs))
        for i, m in enumerate(msgs):
            board.send(m)
            reply = self._wait_for(board, {"cover-part", "cover-ack"})
            if reply["type"] == "cover-ack":
                if not reply.get("ok", True):
                    log.warning("board rejected the cover: %s", reply.get("error"))
                return

    def _media_command(self, cmd: str) -> None:
        if self._reader is not None and self._reader.command(cmd):
            return
        keys = {"toggle": "play_pause", "play": "play_pause", "pause": "play_pause",
                "next": "next_track", "prev": "prev_track"}.get(cmd)
        if keys:  # no media session (or media disabled): the plain media keys still work for most players
            self.executor.run_async({"type": "hotkey", "keys": keys}, f"media {cmd}")

    # -- events from the board -----------------------------------------------------------------

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "press":
            d, p, k = msg.get("desktop"), msg.get("page"), msg.get("key")
            action = self.deck.action_at(d, p, k) if self.deck else None
            if action is None:
                log.info("key %s/%s/%s (%s) has no action", d, p, k, msg.get("id", ""))
                return
            self.executor.run_async(action, f"{d}/{p}/{k}")
        elif kind == "media":
            self._media_command(str(msg.get("cmd", "")))
        elif kind == "seek":
            if self._reader is not None and "posMs" in msg:
                self._reader.seek(int(msg["posMs"]))
        elif kind == "desktop":
            self._active_desktop = int(msg.get("index", 0))
            log.debug("board: %s", msg)
        elif kind == "nav":
            log.debug("board: %s", msg)
        elif kind == "error":
            log.warning("board error: %s", msg.get("error"))
        # pong / acks need no handling here
