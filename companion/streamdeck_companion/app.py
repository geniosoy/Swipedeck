"""The companion: keeps the board connected, syncs the deck, runs the actions of pressed keys."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from .actions import Executor
from .deck import Deck, DeckError
from .link import open_board
from .protocol import crc_hex, layout_messages

log = logging.getLogger("companion")

HELLO_TIMEOUT = 15.0   # the board reboots when the port is opened; it needs a few seconds
PING_EVERY = 2.0
SILENCE_LIMIT = 8.0    # no message at all for this long -> treat the link as dead
ACK_TIMEOUT = 5.0


class Companion:
    def __init__(self, deck_path: str | Path, port: str | None = None, executor: Executor | None = None,
                 open_board_fn: Callable = open_board, clock: Callable[[], float] = time.monotonic):
        self.deck_path = Path(deck_path)
        self.port = port
        self.executor = executor or Executor()
        self._open_board = open_board_fn
        self._clock = clock
        self.deck: Deck | None = None
        self._deck_mtime = 0.0
        self._stop = False
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
                self.session(board)
            except ConnectionError as e:
                log.warning("link: %s", e)
            finally:
                if board is not None:
                    board.close()
            if not self._stop:
                time.sleep(retry_seconds)

    def session(self, board) -> None:
        hello = self._handshake(board)
        log.info("board firmware %s, screen %sx%s, layout crc %r", hello.get("firmware"),
                 hello.get("screen", {}).get("w"), hello.get("screen", {}).get("h"), hello.get("layoutCrc"))
        self._push_settings(board)
        board_crc = self._sync_layout(board, hello.get("layoutCrc", ""))

        last_rx = last_ping = self._clock()
        ping_id = 0
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
            if self._deck_changed() and self.load_deck():
                self._push_settings(board)
                board_crc = self._sync_layout(board, board_crc)

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
        elif kind in ("nav", "desktop"):
            log.debug("board: %s", msg)
        elif kind == "error":
            log.warning("board error: %s", msg.get("error"))
        # pong / acks need no handling here
