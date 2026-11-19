import json
import unittest
from pathlib import Path

from streamdeck_companion.deck import Deck, DeckError
from streamdeck_companion.protocol import crc_hex, fnv1a32, layout_messages

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "deck.json"  # a frozen demo deck, not your live one


def simple(keys, **page):
    return {"desktops": [{"name": "D", "pages": [{"id": "root", "title": "T", "cols": 2, "rows": 1, "keys": keys, **page}]}]}


class DeckTest(unittest.TestCase):
    def test_default_deck_is_valid_and_has_no_warnings(self):
        deck = Deck.load(FIXTURE)
        self.assertEqual(deck.warnings, [])
        self.assertEqual(len(deck.layout["desktops"]), 5)
        self.assertTrue(deck.has_nowplaying and deck.has_stats)
        self.assertEqual(deck.layout["desktops"][2], {"name": "PC", "kind": "stats", "options": {
            "style": "gauges", "items": ["cpu", "gpu", "ram", "vram", "disk", "net"]}})
        self.assertEqual(deck.layout["desktops"][1], {"name": "Playing", "kind": "nowplaying", "options": {
            "tint": True, "album": True, "controls": "full", "appIcon": False}})
        self.assertFalse(deck.np_app_icon)  # the app icon is off unless asked for
        self.assertEqual(deck.np_filter, {"allow": [], "block": []})
        self.assertEqual(deck.action_at(0, 0, 0), {"type": "hotkey", "keys": "volume_mute"})
        self.assertIsNone(deck.action_at(0, 0, 4))  # folder key has no action

    def test_layout_for_board_has_no_actions(self):
        text = Deck.load(FIXTURE).layout_text()
        self.assertNotIn('"action"', text)
        json.loads(text)
        self.assertLess(len(text), 15000)

    def test_shared_actions_by_id(self):
        d = Deck({"actions": {"a.b": {"type": "lock"}},
                  **simple([{"label": "X", "id": "a.b"}, {"label": "Y"}])})
        self.assertEqual(d.action_at(0, 0, 0), {"type": "lock"})
        self.assertIsNone(d.action_at(0, 0, 1))
        self.assertTrue(any("no action" in w for w in d.warnings))

    def test_errors(self):
        bad = [
            {}, {"desktops": []}, {"desktops": [{"pages": []}]},
            simple([{"label": "X", "folder": "nowhere"}]),
            simple([{"label": "X", "color": "red"}]),
            simple([{"label": "X", "action": {"type": "hotkey", "keys": "ctrl+zzz"}}]),
            simple([], cols=9),
            {"settings": {"volume": 500}, **simple([])},
        ]
        for data in bad:
            with self.assertRaises(DeckError, msg=str(data)[:80]):
                Deck(data)

    def test_general_settings(self):
        full = {"volume": 40, "sounds": False, "soundTheme": "chirp", "navSounds": False, "brightness": 90,
                "dimAfter": 30, "sleepAfter": 300, "flip": True, "startDesktop": 0, "keyRadius": 20, "keyGap": 3, "wrap": True, "swipe": "long"}
        self.assertEqual(Deck({"settings": full, **simple([])}).settings, full)
        self.assertEqual(Deck({"settings": {"volume": 5, "bogus": 1}, **simple([])}).settings, {"volume": 5})
        self.assertEqual(Deck(simple([])).settings, {})
        for bad in ({"soundTheme": "loud"}, {"soundTheme": 3}, {"navSounds": 1}, {"flip": "yes"}, {"wrap": 1}, {"swipe": "far"}, {"swipe": 2}, {"dimAfter": -1},
                    {"sleepAfter": 99999}, {"keyRadius": 25}, {"keyGap": 1}, {"keyGap": 2.5}, {"startDesktop": 8},
                    {"volume": True}, {"brightness": 4}, {"sounds": 0}):
            with self.assertRaises(DeckError, msg=str(bad)):
                Deck({"settings": bad, **simple([])})
        with self.assertRaises(DeckError):
            Deck({"settings": [], **simple([])})

    def test_effective_settings_fill_in_defaults_and_match_the_editor(self):
        from streamdeck_companion.deck import SETTINGS_SPEC, default_settings

        deck = Deck({"settings": {"volume": 5}, **simple([])})
        eff = deck.effective_settings()
        self.assertEqual(eff["volume"], 5)
        self.assertEqual(eff["soundTheme"], "classic")
        self.assertEqual(set(eff), set(SETTINGS_SPEC))
        self.assertEqual(Deck(simple([])).effective_settings(), default_settings())
        # the editor (web/model.js) has its own copy of the defaults: they must agree
        import re
        js = (ROOT / "streamdeck_companion" / "web" / "model.js").read_text(encoding="utf-8")
        block = re.search(r"SETTING_DEFAULTS = \{(.*?)\};", js, re.S).group(1)
        js_defaults = {}
        for name, value in re.findall(r"(\w+): ('?[\w]+'?)", block):
            if value in ("true", "false"):
                js_defaults[name] = value == "true"
            elif value.startswith("'"):
                js_defaults[name] = value.strip("'")
            else:
                js_defaults[name] = int(value)
        self.assertEqual(js_defaults, default_settings())

    def test_start_desktop_beyond_the_last_desktop_warns(self):
        deck = Deck({"settings": {"startDesktop": 3}, **simple([])})
        self.assertTrue(any("startDesktop" in w for w in deck.warnings))
        self.assertEqual(Deck({"settings": {"startDesktop": 0}, **simple([])}).warnings, [])

    def test_nowplaying_desktop_rules(self):
        np = {"name": "P", "kind": "nowplaying"}
        Deck({"desktops": [np]})  # needs no pages
        with self.assertRaises(DeckError):
            Deck({"desktops": [np, dict(np)]})  # only one
        with self.assertRaises(DeckError):
            Deck({"desktops": [{"name": "P", "kind": "weird"}]})
        st = {"name": "S", "kind": "stats"}
        Deck({"desktops": [np, st]})
        with self.assertRaises(DeckError):
            Deck({"desktops": [st, dict(st)]})

    def test_live_screen_options(self):
        deck = Deck({"desktops": [
            {"name": "P", "kind": "nowplaying", "options": {"filter": {"mode": "only", "apps": ["tidal", "spotify"]},
                                                             "tint": False, "controls": "basic"}},
            {"name": "S", "kind": "stats", "options": {"style": "tiles", "items": ["ram", "cpu"]}}]})
        np, stats = deck.layout["desktops"]
        # the player filter stays on the PC: the board only gets how the screen looks
        self.assertEqual(np["options"], {"tint": False, "album": True, "controls": "basic", "appIcon": False})
        self.assertNotIn("filter", json.dumps(deck.layout))
        self.assertEqual(deck.np_filter, {"allow": ["tidal", "spotify"], "block": []})  # the older form is converted
        newer = Deck({"desktops": [{"name": "P", "kind": "nowplaying", "options": {"filter": {"allow": ["tidal"], "block": ["chrome"]}}}]})
        self.assertEqual(newer.np_filter, {"allow": ["tidal"], "block": ["chrome"]})
        self.assertNotIn("chrome", json.dumps(newer.layout))
        self.assertEqual(stats["options"], {"style": "tiles", "items": ["ram", "cpu"]})

    def test_bad_live_screen_options(self):
        def np(**opts):
            return {"desktops": [{"name": "P", "kind": "nowplaying", "options": opts}]}

        def st(**opts):
            return {"desktops": [{"name": "S", "kind": "stats", "options": opts}]}

        bad = [np(filter={"allow": "x"}), np(filter={"block": [1]}), np(filter={"allow": ["x"] * 31}), np(filter={"block": [""]}),
               np(tint="yes"), np(album=1), np(controls="huge"), np(filter="all"), np(filter={"mode": "some"}),
               np(filter={"mode": "only", "apps": "tidal"}), np(filter={"mode": "only", "apps": [""]}),
               np(filter={"mode": "only", "apps": ["x"] * 31}),
               st(style="pie"), st(items=[]), st(items=["cpu", "cpu"]), st(items=["cpu", "fan"]), st(items="cpu"),
               {"desktops": [{"name": "P", "kind": "stats", "options": []}]}]
        for data in bad:
            with self.assertRaises(DeckError, msg=str(data)):
                Deck(data)

    def test_app_icon_option(self):
        on = Deck({"desktops": [{"name": "P", "kind": "nowplaying", "options": {"appIcon": True}}]})
        self.assertTrue(on.np_app_icon)
        self.assertTrue(on.layout["desktops"][0]["options"]["appIcon"])   # the board needs to know
        with self.assertRaises(DeckError):
            Deck({"desktops": [{"name": "P", "kind": "nowplaying", "options": {"appIcon": "yes"}}]})

    def test_an_app_on_both_lists_is_reported(self):
        deck = Deck({"desktops": [{"name": "P", "kind": "nowplaying", "options": {"filter": {"allow": ["Tidal"], "block": ["tidal"]}}}]})
        self.assertTrue(any("both lists" in w for w in deck.warnings))

    def test_unknown_option_is_only_a_warning(self):
        deck = Deck({"desktops": [{"name": "S", "kind": "stats", "options": {"sparkle": True}}]})
        self.assertTrue(any("sparkle" in w for w in deck.warnings))

    def test_warnings(self):
        d = Deck(simple([{"label": "X", "icon": "nonexistent", "action": {"type": "lock"}},
                         {}, {"label": "extra", "action": {"type": "lock"}}]))
        self.assertTrue(any("unknown icon" in w for w in d.warnings))
        self.assertTrue(any("dropped" in w for w in d.warnings))
        self.assertEqual(len(d.layout["desktops"][0]["pages"][0]["keys"]), 2)


class ProtocolTest(unittest.TestCase):
    def test_fnv_known_vectors(self):
        self.assertEqual(fnv1a32(b""), 0x811C9DC5)
        self.assertEqual(fnv1a32(b"a"), 0xE40C292C)
        self.assertEqual(fnv1a32(b"foobar"), 0xBF9CF968)

    def test_chunks_reassemble(self):
        text = Deck.load(FIXTURE).layout_text()
        msgs = layout_messages(text, chunk=500)
        self.assertGreater(len(msgs), 1)
        self.assertEqual([m["seq"] for m in msgs], list(range(len(msgs))))
        self.assertTrue(all(m["of"] == len(msgs) for m in msgs))
        self.assertEqual("".join(m["data"] for m in msgs), text)
        self.assertEqual(layout_messages("")[0]["of"], 1)

    def test_crc_hex_is_utf8_based(self):
        self.assertEqual(crc_hex("é"), f"{fnv1a32('é'.encode('utf-8')):08x}")


if __name__ == "__main__":
    unittest.main()
