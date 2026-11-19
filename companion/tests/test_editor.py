import http.client
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from streamdeck_companion.deck import ICONS, Deck, DeckError
from streamdeck_companion.editor import EditorServer, WEB, format_deck, validate_preset_key

DECK = Path(__file__).resolve().parent / "fixtures" / "deck.json"  # a frozen demo deck, not your live one


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
        for path in ("/app.js", "/model.js", "/style.css", "/icons.json", "/presets.json", "/templates.json", "/apps.json", "/fonts/fa.woff", "/favicon.svg", "/favicon.ico"):
            self.assertEqual(self.call("GET", path)[0], 200, path)

    def test_board_settings_and_sound_endpoints(self):
        sent = []
        srv = EditorServer(self.deck_path, port=0, command_fn=lambda c: sent.append(c) or True)
        srv.start()
        self.addCleanup(srv.stop)
        host = f"127.0.0.1:{srv.port}"

        def post(path, body, **kw):
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
            h = {"Host": host, "Origin": f"http://{host}", "X-Streamdeck-Token": srv.token, "Content-Type": "application/json"}
            h.update(kw)
            conn.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=h)
            r = conn.getresponse()
            out = (r.status, json.loads(r.read()))
            conn.close()
            return out

        self.assertEqual(post("/api/board/settings", {"settings": {"volume": 30, "soundTheme": "soft", "flip": True}}),
                         (200, {"ok": True, "sent": True}))
        self.assertEqual(sent[-1], {"kind": "settings", "settings": {"volume": 30, "soundTheme": "soft", "flip": True}})
        self.assertEqual(post("/api/board/sound", {"name": "nav"}), (200, {"ok": True, "sent": True}))
        self.assertEqual(sent[-1], {"kind": "sound", "name": "nav"})
        self.assertEqual(post("/api/board/settings", {"settings": {"volume": 300}})[0], 400)
        self.assertEqual(post("/api/board/settings", {"nothing": 1})[0], 400)
        self.assertEqual(post("/api/board/sound", {"name": "boom"})[0], 400)
        self.assertEqual(post("/api/board/other", {})[0], 404)
        self.assertEqual(post("/api/board/sound", {"name": "tap"}, **{"X-Streamdeck-Token": "wrong"})[0], 403)
        self.assertEqual(post("/api/board/sound", {"name": "tap"}, Origin="http://evil.example")[0], 403)
        self.assertEqual(len(sent), 2)  # nothing refused reached the board
        # no board connected: the request is fine but nothing is sent
        down = EditorServer(self.deck_path, port=0, command_fn=lambda c: False)
        self.assertEqual(down.board_sound({"name": "tap"}), (200, {"ok": True, "sent": False}))
        self.assertEqual(self.server.board_sound({"name": "tap"}), (200, {"ok": True, "sent": False}))  # no command function
        down._httpd.server_close()

    def test_obs_connection_test_endpoint(self):
        try:
            from test_netactions import FakeObs  # run as `unittest discover -s tests`
        except ImportError:
            from tests.test_netactions import FakeObs  # run as `unittest tests.test_editor`
        import os
        import socket
        from unittest import mock

        fake = FakeObs(password="pw", answers={"GetVersion": (True, 100, None, {"obsVersion": "30.2.1", "obsWebSocketVersion": "5.5.2"})})
        self.addCleanup(fake.close)
        self.assertEqual(self.server.obs_test({"obs": {"port": fake.port, "password": "pw"}}),
                         (200, {"ok": True, "obs": "30.2.1", "websocket": "5.5.2"}))
        status, result = self.server.obs_test({"obs": {"port": fake.port, "password": "wrong"}})
        self.assertEqual((status, result["ok"]), (200, False))
        self.assertIn("password", result["error"])
        with mock.patch.dict(os.environ, {"SD_TEST_OBS_PW": "pw"}):  # ${VARIABLE} is read here, as when the action runs
            self.assertTrue(self.server.obs_test({"obs": {"port": fake.port, "password": "${SD_TEST_OBS_PW}"}})[1]["ok"])
        os.environ.pop("SD_TEST_OBS_PW", None)
        status, result = self.server.obs_test({"obs": {"port": fake.port, "password": "${SD_TEST_OBS_PW}"}})
        self.assertEqual(status, 200)
        self.assertIn("SD_TEST_OBS_PW", result["error"])
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        status, result = self.server.obs_test({"obs": {"port": port}})
        self.assertEqual((status, result["ok"]), (200, False))
        self.assertIn("is OBS running", result["error"])
        for bad in ({"obs": "x"}, {"obs": {"port": 0}}, {"obs": {"bogus": 1}}, {"obs": []}):
            self.assertEqual(self.server.obs_test(bad)[0], 400, bad)  # (no settings at all would try the default 127.0.0.1:4455)
        # over HTTP: needs the token like every write
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        headers = {"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json"}
        conn.request("POST", "/api/obs/test", body=json.dumps({"obs": {"port": port}}).encode(), headers=headers)
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_running_apps_endpoint(self):
        self.assertEqual(self.call("GET", "/api/running-apps")[0], 501)  # this server has no such function
        ok = EditorServer(self.deck_path, port=0, running_apps_fn=lambda: ["chrome", "code"])
        ok.start()
        self.addCleanup(ok.stop)
        conn = http.client.HTTPConnection("127.0.0.1", ok.port, timeout=5)
        conn.request("GET", "/api/running-apps", headers={"Host": f"127.0.0.1:{ok.port}"})
        r = conn.getresponse()
        self.assertEqual((r.status, json.loads(r.read())), (200, {"apps": ["chrome", "code"]}))
        conn.close()

        def boom():
            raise RuntimeError("nope")

        bad = EditorServer(self.deck_path, port=0, running_apps_fn=boom)
        bad.start()
        self.addCleanup(bad.stop)
        conn = http.client.HTTPConnection("127.0.0.1", bad.port, timeout=5)
        conn.request("GET", "/api/running-apps", headers={"Host": f"127.0.0.1:{bad.port}"})
        self.assertEqual(conn.getresponse().status, 501)  # a failing lookup is an error message, not a crash
        conn.close()

    def test_media_sources_endpoint(self):
        self.assertEqual(self.call("GET", "/api/media-sources")[0], 501)  # this server has no source function
        ok = EditorServer(self.deck_path, port=0, media_sources_fn=lambda: [{"id": "Chrome", "playing": True}])
        ok.start()
        self.addCleanup(ok.stop)
        conn = http.client.HTTPConnection("127.0.0.1", ok.port, timeout=5)
        conn.request("GET", "/api/media-sources", headers={"Host": f"127.0.0.1:{ok.port}"})
        r = conn.getresponse()
        self.assertEqual((r.status, json.loads(r.read())), (200, {"sources": [{"id": "Chrome", "playing": True}]}))
        conn.request("GET", "/api/media-sources", headers={"Host": "evil.example.com"})
        self.assertEqual(conn.getresponse().status, 403)

        def broken():
            raise RuntimeError("no winrt")

        bad = EditorServer(self.deck_path, port=0, media_sources_fn=broken)
        bad.start()
        self.addCleanup(bad.stop)
        conn = http.client.HTTPConnection("127.0.0.1", bad.port, timeout=5)
        conn.request("GET", "/api/media-sources", headers={"Host": f"127.0.0.1:{bad.port}"})
        r = conn.getresponse()
        self.assertEqual(r.status, 501)
        self.assertIn("no winrt", json.loads(r.read())["error"])

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

    def test_icon_codes_are_those_of_the_symbols_the_board_draws(self):
        """The board draws the UTF-8 bytes of each LV_SYMBOL_*; the editor must show the same glyph. (LVGL's own
        comments next to the symbols have typos, e.g. LV_SYMBOL_FILE, so the bytes are what counts.)"""
        import re

        root = Path(__file__).resolve().parents[2]
        header = root / ".pio" / "libdeps" / "es3c28p" / "lvgl" / "src" / "font" / "lv_symbol_def.h"
        if not header.exists():
            self.skipTest("LVGL sources not downloaded (build the firmware once)")
        ui = (root / "src" / "ui.cpp").read_text(encoding="utf-8")
        used = dict(re.findall(r'\{"([a-z_]+)",\s*(LV_SYMBOL_[A-Z_0-9]+)\}', ui[ui.index("kIcons[]"):]))
        defs = header.read_text(encoding="utf-8")
        icons = json.loads((WEB / "icons.json").read_text(encoding="utf-8"))
        for name, symbol in used.items():
            m = re.search(rf'#define {symbol}\s+"((?:\\x[0-9A-Fa-f]{{2}})+)"', defs)
            self.assertIsNotNone(m, symbol)
            point = ord(bytes.fromhex(m.group(1).replace("\\x", "")).decode("utf-8"))
            self.assertEqual(icons[name], f"{point:04x}", f"icon {name} ({symbol})")


class TemplatesFileTest(unittest.TestCase):
    def setUp(self):
        self.templates = json.loads((WEB / "templates.json").read_text(encoding="utf-8"))["templates"]

    def test_there_are_several_and_they_are_described(self):
        self.assertGreaterEqual(len(self.templates), 8)
        ids, names = set(), set()
        for t in self.templates:
            self.assertTrue(t["id"] and t["name"] and t["description"], t)
            self.assertIn(t["icon"], ICONS, t["id"])
            self.assertNotIn(t["id"], ids)
            self.assertNotIn(t["name"], names)
            ids.add(t["id"])
            names.add(t["name"])

    def test_every_template_is_a_valid_desktop_without_warnings(self):
        for t in self.templates:
            deck = Deck({"desktops": [t["desktop"]]})  # validates every action, icon, color and folder reference
            self.assertEqual(deck.warnings, [], t["id"])  # no unknown icons, actions missing, or keys that do not fit
            pages = t["desktop"]["pages"]
            page_ids = [p["id"] for p in pages]
            self.assertEqual(len(page_ids), len(set(page_ids)), f"{t['id']}: duplicate page ids")
            self.assertEqual(page_ids[0], "root", t["id"])
            folders = {k["folder"] for p in pages for k in p["keys"] if k.get("folder")}
            self.assertEqual(set(page_ids[1:]), folders, f"{t['id']}: every sub-page needs a folder key, and vice versa")
            for p in pages:
                self.assertEqual(len(p["keys"]), p["cols"] * p["rows"], f"{t['id']}/{p['id']}: fill the whole grid ({{}} = empty)")
                for k in p["keys"]:
                    self.assertLessEqual(len(k.get("label", "")), 10, f"{t['id']}: label too long: {k.get('label')}")
            self.assertLess(len(deck.layout_text()), 3500, f"{t['id']}: keep templates small")

    def test_a_template_added_to_the_demo_deck_still_saves(self):
        data = json.loads(DECK.read_text(encoding="utf-8"))
        data["desktops"].extend(t["desktop"] for t in self.templates[:3])
        Deck(data)


if __name__ == "__main__":
    unittest.main()
