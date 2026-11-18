import json
import unittest
from pathlib import Path

from streamdeck_companion.deck import Deck, DeckError
from streamdeck_companion.protocol import crc_hex, fnv1a32, layout_messages

ROOT = Path(__file__).resolve().parent.parent


def simple(keys, **page):
    return {"desktops": [{"name": "D", "pages": [{"id": "root", "title": "T", "cols": 2, "rows": 1, "keys": keys, **page}]}]}


class DeckTest(unittest.TestCase):
    def test_default_deck_is_valid_and_has_no_warnings(self):
        deck = Deck.load(ROOT / "deck.json")
        self.assertEqual(deck.warnings, [])
        self.assertEqual(len(deck.layout["desktops"]), 3)
        self.assertEqual(deck.action_at(0, 0, 0), {"type": "hotkey", "keys": "volume_mute"})
        self.assertIsNone(deck.action_at(0, 0, 4))  # folder key has no action

    def test_layout_for_board_has_no_actions(self):
        text = Deck.load(ROOT / "deck.json").layout_text()
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
        text = Deck.load(ROOT / "deck.json").layout_text()
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
