"""Windows media session (SMTC, the thing behind the volume popup) via the winrt bindings.

A background thread polls the current session twice a second; the rest of the program only reads
`snapshot()` / `cover()` and calls `command()` / `seek()`. Everything Windows-specific stays here.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

from .nowplaying import COVER_PX_DEFAULT, Snapshot, cover_key, cover_to_rgb565

log = logging.getLogger("media")

POLL_S = 0.5
COVER_RETRIES = 12  # some players publish the thumbnail a moment after the title


class MediaReader:
    def __init__(self, cover_px: int = COVER_PX_DEFAULT):
        self.cover_px = cover_px
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._cover: tuple[str, bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error: str | None = None

    # -- public (thread-safe) ------------------------------------------------------------------

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="media", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self.error is None

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snap

    def cover(self) -> tuple[str, bytes] | None:
        """(id, little-endian RGB565 bytes) of the current track's cover, or None."""
        with self._lock:
            return self._cover

    def command(self, cmd: str) -> bool:
        """play | pause | toggle | next | prev on the current session. False if there is no session."""
        return self._submit(self._command(cmd))

    def seek(self, ms: int) -> bool:
        return self._submit(self._seek(ms))

    # -- internals -----------------------------------------------------------------------------

    def _submit(self, coro) -> bool:
        if not self._loop or self.snapshot().state == "none":
            coro.close()
            return False
        asyncio.run_coroutine_threadsafe(coro, self._loop)
        return True

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:  # import failure, WinRT error ... the deck keeps working without media
            self.error = str(e)
            log.error("media session reader stopped: %s", e)
            self._ready.set()

    async def _main(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        self._loop = asyncio.get_running_loop()
        self._manager = await Manager.request_async()
        self._track = None
        self._cover_tries = 0
        self._ready.set()
        while not self._stop.is_set():
            try:
                await self._refresh()
            except Exception as e:
                log.debug("refresh failed: %s", e)
            await asyncio.sleep(POLL_S)

    def _session(self):
        return self._manager.get_current_session()

    async def _command(self, cmd: str) -> None:
        s = self._session()
        if s is None:
            return
        action = {"play": s.try_play_async, "pause": s.try_pause_async, "toggle": s.try_toggle_play_pause_async,
                  "next": s.try_skip_next_async, "prev": s.try_skip_previous_async}.get(cmd)
        if action:
            await action()
            await asyncio.sleep(0.25)  # let the player update, then show it right away
            await self._refresh()

    async def _seek(self, ms: int) -> None:
        s = self._session()
        if s is None:
            return
        start = s.get_timeline_properties().start_time
        target = int((start.total_seconds() * 1000 + ms) * 10_000)  # SMTC uses 100 ns ticks
        await s.try_change_playback_position_async(target)
        await asyncio.sleep(0.25)
        await self._refresh()

    async def _refresh(self) -> None:
        s = self._session()
        if s is None:
            with self._lock:
                self._snap, self._cover, self._track = Snapshot(), None, None
            return
        props = await s.try_get_media_properties_async()
        tl = s.get_timeline_properties()
        info = s.get_playback_info()
        ctl = info.controls

        status = int(info.playback_status)  # 3 stopped, 4 playing, 5 paused (closed/opened/changing = 0..2)
        state = {4: "playing", 5: "paused", 3: "stopped"}.get(status, "stopped" if status < 3 else "none")
        rate = info.playback_rate or 1.0
        start_ms = tl.start_time.total_seconds() * 1000
        dur_ms = max(0.0, tl.end_time.total_seconds() * 1000 - start_ms)
        pos_ms = tl.position.total_seconds() * 1000 - start_ms
        if state == "playing" and tl.last_updated_time.year > 1970:
            # The timeline is a snapshot taken at last_updated_time: advance it to now.
            elapsed = (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
            pos_ms += max(0.0, elapsed) * 1000 * rate
        if dur_ms:
            pos_ms = min(pos_ms, dur_ms)

        track = (s.source_app_user_model_id, props.title, props.artist, props.album_title)
        if track != self._track:
            self._track, self._cover_tries = track, 0
            with self._lock:
                self._cover = None  # never show the previous track's cover
        if self._cover is None and self._cover_tries < COVER_RETRIES and props.thumbnail is not None:
            self._cover_tries += 1
            cover = await self._read_cover(props.thumbnail)
            with self._lock:
                self._cover = cover
        elif self._cover is None:
            self._cover_tries += 1

        with self._lock:
            self._snap = Snapshot(
                state=state, title=props.title or "", artist=props.artist or "", album=props.album_title or "",
                pos_ms=int(max(0.0, pos_ms)), dur_ms=int(dur_ms), rate=float(rate),
                can_seek=bool(ctl.is_playback_position_enabled), can_prev=bool(ctl.is_previous_enabled),
                can_next=bool(ctl.is_next_enabled),
                can_play=bool(ctl.is_play_enabled or ctl.is_pause_enabled or ctl.is_play_pause_toggle_enabled),
                cover_key=self._cover[0] if self._cover else None)

    async def _read_cover(self, thumbnail) -> tuple[str, bytes] | None:
        from winrt.windows.storage.streams import Buffer, InputStreamOptions

        try:
            stream = await thumbnail.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 8_000_000:
                return None
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            data = bytes(memoryview(buf))
            return cover_key(data), cover_to_rgb565(data, self.cover_px)
        except Exception as e:
            log.debug("cover not readable: %s", e)
            return None
