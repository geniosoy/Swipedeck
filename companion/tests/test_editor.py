import http.client
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from streamdeck_companion.deck import ICONS, Deck, DeckError
from streamdeck_companion.editor import EditorServer, WEB, format_deck, validate_preset_key

DECK = Path(__file__).resolve().parent.parent / "deck.json"


class EditorServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.deck_path = self.tmp / "deck.json"
        shutil.copyfile(DECK, self.deck_path)
        self.server = EditorServer(self.deck_path, port=0, status_fn=lambda: {"connected": True})
        self.server.start()
        self.addCleanup(self.server.stop)
        self.host = f"127.0.0.1:{self.server.port}"

    def call(self, method, path, body=None, host=None, headers=None, token=True, origin=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        h = {"Host": host or self.host}
        if method in ("PUT", "POST"):
            if token:
                h["X-Streamdeck-Token"] = self.server.token
            if origin:
                h["Origin"] = f"http://{host or self.host}"
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        return r.status, raw, r

    def json(self, *a, **kw):
        status, raw, _ = self.call(*a, **kw)
        return status, json.loads(raw)

    # -- reading -------------------------------------------------------------------------------

    def test_index_has_a_fresh_token_and_security_headers(self):
        status, raw, r = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, raw.decode("utf-8"))
        self.assertNotIn("__TOKEN__", raw.decode("utf-8"))
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")

    def test_reads(self):
        status, deck = self.json("GET", "/api/deck")
        self.assertEqual((status, len(deck["desktops"])), (200, 5))
        status, presets = self.json("GET", "/api/presets")
        self.assertEqual(status, 200)
        self.assertGreater(len(presets["builtin"]), 5)
        self.assertEqual(presets["user"], [])
        self.assertEqual(self.json("GET", "/api/status"), (200, {"connected": True}))
        for path in ("/app.js", "/model.js", "/style.css", "/icons.json", "/presets.json", "/fonts/fa.woff"):
            self.assertEqual(self.call("GET", path)[0], 200, path)

    def test_static_files_cannot_escape_the_web_folder(self):
        for path in ("/../deck.json", "/%2e%2e/deck.json", "/fonts/../../deck.json", "/..%2fdeck.json",
                     "/editor.py", "/%2e%2e/streamdeck_companion/editor.py", "/nothing.js"):
            self.assertEqual(self.call("GET", path)[0], 404, path)

    # -- the guards ----------------------------------------------------------------------------

    def test_wrong_host_is_refused_everywhere(self):
        for host in ("evil.example.com", f"evil.example.com:{self.server.port}", "127.0.0.1:1", "localhost"):
            self.assertEqual(self.call("GET", "/", host=host)[0], 403, host)
            self.assertEqual(self.call("GET", "/api/deck", host=host)[0], 403, host)
            self.assertEqual(self.call("PUT", "/api/deck", {}, host=host)[0], 403, host)

    def test_localhost_name_is_accepted(self):
        self.assertEqual(self.call("GET", "/api/deck", host=f"localhost:{self.server.port}")[0], 200)

    def test_writes_need_the_token_and_a_matching_origin(self):
        before = self.deck_path.read_text(encoding="utf-8")
        deck = json.loads(before)
        self.assertEqual(self.call("PUT", "/api/deck", deck, token=False)[0], 403)
        self.assertEqual(self.call("PUT", "/api/deck", deck, headers={"X-Streamdeck-Token": "wrong"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/deck", deck, headers={"X-Streamdeck-Token": "wrongé"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/deck", deck, origin=False, token=False)[0], 403)
        self.assertEqual(self.call("PUT", "/api/deck", deck, headers={"Origin": "http://evil.example.com"})[0], 403)
        self.assertEqual(self.call("PUT", "/api/deck", deck, headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.deck_path.read_text(encoding="utf-8"), before)  # nothing was written
        self.assertEqual(self.call("PUT", "/api/deck", deck)[0], 200)
        self.assertEqual(self.call("PUT", "/api/deck", deck, origin=False)[0], 200)  # non-browser client with token

    def test_other_methods_and_cors_preflight_are_not_served(self):
        self.assertNotEqual(self.call("OPTIONS", "/api/deck")[0], 200)
        status, _, r = self.call("GET", "/api/deck")
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    # -- saving --------------------------------------------------------------------------------

    def test_invalid_deck_is_rejected_and_the_file_is_untouched(self):
        before = self.deck_path.read_text(encoding="utf-8")
        bad = [
            {"desktops": []},
            {"desktops": [{"name": "x", "pages": [{"id": "r", "keys": [{"label": "a", "folder": "nope"}]}]}]},
            {"desktops": [{"name": "x", "pages": [{"id": "r", "keys": [
                {"label": "a", "action": {"type": "hotkey", "keys": "ctrl+nonsense"}}]}]}]},
            [],
        ]
        for data in bad:
            status, result = self.json("PUT", "/api/deck", data)
            self.assertEqual(status, 400, data)
            self.assertIn("error", result)
        self.assertEqual(self.deck_path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.call("PUT", "/api/deck", b"{not json")[0], 400)

    def test_save_roundtrip_backup_and_warnings(self):
        deck = json.loads(self.deck_path.read_text(encoding="utf-8"))
        deck["desktops"][0]["name"] = "Renamed"
        deck["desktops"][0]["pages"][0]["keys"][0]["icon"] = "no_such_icon"
        status, result = self.json("PUT", "/api/deck", deck)
        self.assertEqual(status, 200)
        self.assertTrue(any("unknown icon" in w for w in result["warnings"]))
        saved = json.loads(self.deck_path.read_text(encoding="utf-8"))
        self.assertEqual(saved, deck)
        self.assertTrue((self.tmp / "deck.json.bak").exists())
        self.assertFalse((self.tmp / "deck.json.tmp").exists())
        Deck.load(self.deck_path)  # still loads (this is what the companion does when the file changes)

    # -- presets -------------------------------------------------------------------------------

    def test_user_presets(self):
        good = {"presets": [{"name": " Mine ", "key": {"label": "X", "icon": "home",
                                                      "action": {"type": "hotkey", "keys": "ctrl+1"}}}]}
        status, result = self.json("PUT", "/api/presets/user", good)
        self.assertEqual((status, result["count"]), (200, 1))
        _, presets = self.json("GET", "/api/presets")
        self.assertEqual(presets["user"][0]["name"], "Mine")
        for bad in ({"presets": [{"name": "", "key": {}}]},
                    {"presets": [{"name": "f", "key": {"label": "F", "folder": "x"}}]},
                    {"presets": [{"name": "a", "key": {"label": "A", "action": {"type": "bogus"}}}]},
                    {"presets": "nope"}, {"presets": [{"name": "a"}]}):
            self.assertEqual(self.json("PUT", "/api/presets/user", bad)[0], 400, bad)
        self.assertEqual(self.call("PUT", "/api/presets/user", good, token=False)[0], 403)


class FormatTest(unittest.TestCase):
    def test_roundtrip_of_the_default_deck_and_edge_shapes(self):
        data = json.loads(DECK.read_text(encoding="utf-8"))
        text = format_deck(data)
        self.assertEqual(json.loads(text), data)
        self.assertGreater(text.count("\n"), 40)
        self.assertLess(max(len(line) for line in text.splitlines()), 400)
        for odd in ({"desktops": []},
                    {"desktops": [{"name": "A", "pages": [{"keys": []}]}]},
                    {"desktops": [{"pages": [{"id": "r", "keys": [{}]}]}], "settings": {"volume": 5}},
                    {"settings": {}, "actions": {"a": {"type": "lock"}}, "desktops": [{"name": "ünï", "kind": "stats"}]}):
            self.assertEqual(json.loads(format_deck(odd)), odd, odd)

    def test_keys_are_one_per_line(self):
        data = {"desktops": [{"name": "A", "pages": [{"id": "r", "cols": 2, "rows": 1, "keys": [
            {"label": "a"}, {"label": "b", "action": {"type": "lock"}}]}]}]}
        lines = format_deck(data).splitlines()
        self.assertTrue(any(l.strip() == '{"label": "a"},' for l in lines))
        self.assertTrue(any(l.strip() == '{"label": "b", "action": {"type": "lock"}}' for l in lines))


class PresetsFileTest(unittest.TestCase):
    def test_every_builtin_preset_is_valid(self):
        data = json.loads((WEB / "presets.json").read_text(encoding="utf-8"))
        names = set()
        for cat in data["categories"]:
            self.assertTrue(cat["id"] and cat["name"] and cat["presets"])
            for p in cat["presets"]:
                key = p["key"]
                self.assertNotIn(p["name"], names, "duplicate preset name")
                names.add(p["name"])
                if key.get("folder") == "new":  # the editor turns this into a new page
                    continue
                try:
                    validate_preset_key(key)
                except DeckError as e:
                    self.fail(f"preset {p['name']!r}: {e}")
                if key.get("icon"):
                    self.assertIn(key["icon"], ICONS, p["name"])
                for field in ("label",):
                    if key:
                        self.assertLessEqual(len(key.get(field, "")), 10, f"label too long on a key: {p['name']}")
        self.assertGreaterEqual(len(names), 60)

    def test_icon_table_matches_the_firmware_icon_names(self):
        icons = json.loads((WEB / "icons.json").read_text(encoding="utf-8"))
        self.assertEqual(set(icons), ICONS)
        self.assertTrue(all(len(v) == 4 for v in icons.values()))


if __name__ == "__main__":
    unittest.main()
