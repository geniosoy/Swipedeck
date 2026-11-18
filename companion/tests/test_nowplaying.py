import base64
import io
import unittest

from PIL import Image

from streamdeck_companion.nowplaying import (NowPlayingSync, Snapshot, app_allowed, app_matches, background_color,
                                             choose_session, cover_key, cover_messages, cover_to_rgb565, sanitize)


def png(w, h, color):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class SanitizeTest(unittest.TestCase):
    def test_accents_and_unsupported(self):
        self.assertEqual(sanitize("Canción Ñandú Ç"), "Cancion Nandu C")
        self.assertEqual(sanitize("日本語 song"), "??? song")
        self.assertEqual(sanitize("a\tb"), "a b")
        self.assertEqual(len(sanitize("x" * 500)), 80)
        self.assertEqual(sanitize(None), "")


class CoverTest(unittest.TestCase):
    def test_rgb565_size_and_packing(self):
        data = cover_to_rgb565(png(150, 83, (255, 0, 0)), 16)
        self.assertEqual(len(data), 16 * 16 * 2)
        self.assertEqual(data[:2], (0xF800).to_bytes(2, "little"))  # pure red, little-endian
        green = cover_to_rgb565(png(10, 10, (0, 255, 0)), 4)
        self.assertEqual(green[:2], (0x07E0).to_bytes(2, "little"))
        blue = cover_to_rgb565(png(10, 10, (0, 0, 255)), 4)
        self.assertEqual(blue[:2], (0x001F).to_bytes(2, "little"))

    def test_crops_to_square_center(self):
        im = Image.new("RGB", (40, 20), (0, 0, 0))
        im.paste((255, 255, 255), (10, 0, 30, 20))  # white in the center square
        buf = io.BytesIO()
        im.save(buf, "PNG")
        data = cover_to_rgb565(buf.getvalue(), 4)
        self.assertTrue(all(b == 0xFF for b in data))

    def test_messages_reassemble(self):
        raw = bytes(range(256)) * 50
        msgs = cover_messages("k", raw, 112)
        self.assertEqual(b"".join(base64.b64decode(m["data"]) for m in msgs), raw)
        self.assertEqual([m["off"] for m in msgs], list(range(0, len(raw), 3000)))
        self.assertTrue(all(m["total"] == len(raw) and len(m["data"]) <= 4000 for m in msgs))

    def test_background_is_dark_and_keeps_the_hue(self):
        for color in [(255, 0, 0), (0, 200, 255), (255, 255, 255), (0, 0, 0), (40, 200, 60)]:
            bg = background_color(png(8, 8, color))
            r, g, b = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
            self.assertLessEqual(max(r, g, b), 0x90, (color, bg))  # dark enough for white text
        r, g, b = (int(background_color(png(8, 8, (255, 0, 0)))[i:i + 2], 16) for i in (1, 3, 5))
        self.assertTrue(r > g and r > b)

    def test_messages_carry_background(self):
        self.assertEqual({m["bg"] for m in cover_messages("k", bytes(100), 10, "#112233")}, {"#112233"})

    def test_key_is_stable(self):
        self.assertEqual(cover_key(b"abc"), cover_key(b"abc"))
        self.assertNotEqual(cover_key(b"abc"), cover_key(b"abd"))


def session(app, playing=False, current=False):
    return {"id": app, "playing": playing, "current": current}


class PlayerFilterTest(unittest.TestCase):
    def test_matching_is_by_name_part_and_case_insensitive(self):
        self.assertTrue(app_matches("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", ["spotify"]))
        self.assertTrue(app_matches("TIDAL.exe", ["Tidal"]))
        self.assertFalse(app_matches("Chrome", ["tidal", "spotify"]))
        self.assertFalse(app_matches("Chrome", [""]))     # an empty pattern matches nothing (not everything)
        self.assertFalse(app_matches("", ["x"]))

    def test_modes(self):
        self.assertTrue(app_allowed("Chrome", {}))
        self.assertTrue(app_allowed("Chrome", {"mode": "all", "apps": ["chrome"]}))
        self.assertTrue(app_allowed("Chrome", {"mode": "only", "apps": ["chrome"]}))
        self.assertFalse(app_allowed("Spotify.exe", {"mode": "only", "apps": ["chrome"]}))
        self.assertFalse(app_allowed("Chrome", {"mode": "except", "apps": ["chrome"]}))
        self.assertTrue(app_allowed("Spotify.exe", {"mode": "except", "apps": ["chrome"]}))
        self.assertFalse(app_allowed("Anything", {"mode": "only", "apps": []}))   # whitelist of nothing: nothing
        self.assertTrue(app_allowed("Anything", {"mode": "except", "apps": []}))  # blacklist of nothing: everything

    def test_choose_session(self):
        chrome, spotify, tidal = "Chrome", "Spotify.exe", "TIDAL.exe"
        alls = {"mode": "all", "apps": []}
        self.assertIsNone(choose_session([], alls))
        # a playing player beats the system's "current" one
        c = [session(chrome, current=True), session(spotify, playing=True)]
        self.assertEqual(choose_session(c, alls), 1)
        # the whitelist hides the others even when they play
        self.assertEqual(choose_session(c, {"mode": "only", "apps": ["chrome"]}), 0)
        self.assertEqual(choose_session(c, {"mode": "except", "apps": ["spotify"]}), 0)
        self.assertIsNone(choose_session(c, {"mode": "only", "apps": ["tidal"]}))   # allowed player is not running
        # two players playing: the one already shown stays, then the system's current one
        c = [session(spotify, playing=True), session(tidal, playing=True, current=True)]
        self.assertEqual(choose_session(c, alls, previous=spotify), 0)
        self.assertEqual(choose_session(c, alls, previous=None), 1)
        self.assertEqual(choose_session(c, alls, previous="Gone.exe"), 1)
        # nothing playing: keep the one shown, then the current one, then the first
        c = [session(chrome), session(spotify, current=True), session(tidal)]
        self.assertEqual(choose_session(c, alls, previous=tidal), 2)
        self.assertEqual(choose_session(c, alls), 1)
        self.assertEqual(choose_session([session(chrome), session(tidal)], alls), 0)
        # ...but a previous player the filter now forbids is not kept
        self.assertEqual(choose_session(c, {"mode": "except", "apps": ["tidal"]}, previous=tidal), 1)
        # a paused allowed player is shown when the others (not allowed) are the ones playing
        c = [session(chrome, playing=True), session(tidal)]
        self.assertEqual(choose_session(c, {"mode": "only", "apps": ["tidal"]}), 1)


class SyncTest(unittest.TestCase):
    def snap(self, **kw):
        base = dict(state="playing", title="T", artist="A", pos_ms=10_000, dur_ms=200_000, can_seek=True)
        base.update(kw)
        return Snapshot(**base)

    def test_first_message_then_quiet_while_playing_normally(self):
        s = NowPlayingSync()
        self.assertIsNotNone(s.update(self.snap(), 0))
        # 3 s later the player is at 13 s: exactly what the board predicts -> nothing to send
        self.assertIsNone(s.update(self.snap(pos_ms=13_000), 3))
        self.assertIsNone(s.update(self.snap(pos_ms=13_400), 3.1))

    def test_seek_and_pause_and_track_change_are_sent(self):
        s = NowPlayingSync()
        s.update(self.snap(), 0)
        self.assertEqual(s.update(self.snap(pos_ms=90_000), 1)["posMs"], 90_000)       # seek
        self.assertEqual(s.update(self.snap(pos_ms=91_000, state="paused"), 2)["state"], "paused")
        self.assertIsNone(s.update(self.snap(pos_ms=91_000, state="paused"), 30))       # paused: static
        self.assertEqual(s.update(self.snap(pos_ms=0, title="Next"), 31)["title"], "Next")

    def test_periodic_resync_while_playing(self):
        s = NowPlayingSync()
        s.update(self.snap(), 0)
        self.assertIsNone(s.update(self.snap(pos_ms=19_000), 9))
        self.assertIsNotNone(s.update(self.snap(pos_ms=20_000), 10))

    def test_cover_actions(self):
        s = NowPlayingSync()
        self.assertEqual(s.cover_action(self.snap(cover_key=None)), "none")
        self.assertEqual(s.cover_action(self.snap(cover_key="a")), "send")
        s.cover_sent = "a"
        self.assertEqual(s.cover_action(self.snap(cover_key="a")), "none")
        self.assertEqual(s.cover_action(self.snap(cover_key="b")), "send")
        self.assertEqual(s.cover_action(self.snap(cover_key=None)), "clear")
        s.cover_sent = ""
        self.assertEqual(s.cover_action(self.snap(cover_key=None)), "none")

    def test_shuffle_and_repeat_changes_are_sent(self):
        s = NowPlayingSync()
        s.update(self.snap(can_shuffle=True, can_repeat=True), 0)
        m = s.update(self.snap(can_shuffle=True, can_repeat=True, shuffle=True, pos_ms=11_000), 1)
        self.assertTrue(m["shuffle"])
        m = s.update(self.snap(can_shuffle=True, can_repeat=True, shuffle=True, repeat=2, pos_ms=12_000), 2)
        self.assertEqual((m["repeat"], m["canShuffle"], m["canRepeat"]), (2, True, True))

    def test_cannot_seek_without_duration(self):
        m = NowPlayingSync.message(self.snap(dur_ms=0))
        self.assertFalse(m["canSeek"])


if __name__ == "__main__":
    unittest.main()
