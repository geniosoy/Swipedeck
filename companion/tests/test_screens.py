"""The volume mixer and the clock / timer / weather screens: companion side."""
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from streamdeck_companion import mixer as mixmod
from streamdeck_companion import weather as wx
from streamdeck_companion.app import Companion
from streamdeck_companion.deck import Deck, DeckError
from streamdeck_companion.mixer import AudioMixer, clamp_percent, display_name, pick_apps

try:
    from test_app import Clock, FakeBoard, Recorder  # run as `unittest discover -s tests`
except ImportError:
    from tests.test_app import Clock, FakeBoard, Recorder  # run as `unittest tests.test_screens`


def wait_until(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---- mixer ---------------------------------------------------------------------------------------------------

def prog(name, vol=50, mute=False, active=False):
    return {"id": name, "vol": vol, "mute": mute, "active": active}


class MixerHelpersTest(unittest.TestCase):
    def test_names_and_percentages(self):
        self.assertEqual(display_name("chrome"), "Chrome")
        self.assertEqual(display_name("OBS64.exe"), "Obs64")
        self.assertEqual(display_name(""), "?")
        self.assertEqual([clamp_percent(v) for v in (-5, 0, 49.6, 100, 250, "30")], [0, 0, 50, 100, 100, 30])

    def test_which_programs_get_a_fader(self):
        programs = [prog("zoom", active=True), prog("chrome"), prog("spotify", active=True), prog("discord"), prog("steam"), prog("teams")]
        names = lambda picked: [p["id"] for p in picked]
        self.assertEqual(names(pick_apps(programs, [])), ["spotify", "zoom", "chrome", "discord"])   # sound now first, then A-Z, at most 4
        self.assertEqual(names(pick_apps(programs, ["steam", "discord"])), ["steam", "discord", "spotify", "zoom"])  # pinned ones lead
        self.assertEqual(names(pick_apps(programs, ["nothing", "steam", "steam"])), ["steam", "spotify", "zoom", "chrome"])  # unknown / repeated pins
        self.assertEqual(names(pick_apps(programs, ["a", "chrome"], limit=2)), ["chrome", "spotify"])
        self.assertEqual(pick_apps([], ["x"]), [])
        picked = pick_apps([prog("spotify", vol=80, mute=True)], [])[0]
        self.assertEqual(picked, {"id": "spotify", "name": "Spotify", "vol": 80, "mute": True})


class FakeAudio:
    def __init__(self):
        self.master = {"vol": 40, "mute": False}
        self.programs = [prog("spotify", 70, active=True), prog("chrome", 100)]
        self.changes = []
        self.fail = 0

    def read(self):
        if self.fail:
            self.fail -= 1
            raise OSError("audio service busy")
        return {"master": dict(self.master), "programs": [dict(p) for p in self.programs]}

    def set_volume(self, target, percent):
        self.changes.append(("volume", target, percent))
        if target == "master":
            self.master["vol"] = percent
        else:
            next(p for p in self.programs if p["id"] == target)["vol"] = percent

    def set_mute(self, target, mute):
        self.changes.append(("mute", target, mute))
        if target == "master":
            self.master["mute"] = mute


class AudioMixerTest(unittest.TestCase):
    def mixer(self, audio=None):
        audio = audio or FakeAudio()
        m = AudioMixer(backend=audio)
        m.start()
        self.addCleanup(m.stop)
        return m, audio

    def test_nothing_is_read_until_the_screen_is_active(self):
        m, _ = self.mixer()
        time.sleep(0.4)
        self.assertIsNone(m.latest())
        m.set_active(True)
        self.assertTrue(wait_until(lambda: m.latest() is not None))
        seq, snap = m.latest()
        self.assertEqual(snap["master"], {"vol": 40, "mute": False})
        self.assertEqual([a["id"] for a in snap["apps"]], ["spotify", "chrome"])

    def test_the_sequence_number_changes_only_when_something_does(self):
        m, audio = self.mixer()
        m.set_active(True)
        wait_until(lambda: m.latest() is not None)
        first = m.latest()[0]
        time.sleep(0.7)
        self.assertEqual(m.latest()[0], first)                      # nothing changed: nothing new to send
        audio.programs[0]["vol"] = 30
        self.assertTrue(wait_until(lambda: m.latest()[0] != first))
        self.assertEqual(m.latest()[1]["apps"][0]["vol"], 30)

    def test_changes_from_the_board_are_applied_and_collapsed(self):
        m, audio = self.mixer()
        m.set_active(True)
        wait_until(lambda: m.latest() is not None)
        for v in (10, 20, 35, 60):              # a fader drag: many values, only the newest counts
            m.set_volume("master", v)
        m.set_mute("spotify", True)
        self.assertTrue(wait_until(lambda: ("mute", "spotify", True) in audio.changes))
        self.assertEqual(audio.master["vol"], 60)
        self.assertLessEqual(len([c for c in audio.changes if c[:2] == ("volume", "master")]), 4)
        self.assertEqual(audio.changes[-1][0] in ("volume", "mute"), True)

    def test_commands_while_the_screen_is_not_showing_are_dropped(self):
        m, audio = self.mixer()
        m.set_volume("master", 90)
        time.sleep(0.5)
        self.assertEqual(audio.changes, [])

    def test_pins_and_a_hiccup_of_the_audio_service(self):
        audio = FakeAudio()
        audio.fail = 2
        m, _ = self.mixer(audio)
        m.configure(["Chrome.exe"])
        m.set_active(True)
        self.assertTrue(wait_until(lambda: m.latest() is not None))        # the first two reads failed: it kept trying
        self.assertEqual(m.latest()[1]["apps"][0]["id"], "chrome")

    def test_a_failing_change_does_not_stop_anything(self):
        audio = FakeAudio()

        def boom(target, percent):
            raise OSError("no such session")

        audio.set_volume = boom
        m, _ = self.mixer(audio)
        m.set_active(True)
        wait_until(lambda: m.latest() is not None)
        m.set_volume("spotify", 5)
        audio.programs[1]["vol"] = 11
        self.assertTrue(wait_until(lambda: any(a["vol"] == 11 for a in m.latest()[1]["apps"])))

    def test_reactivating_starts_from_a_fresh_reading(self):
        m, audio = self.mixer()
        m.set_active(True)
        wait_until(lambda: m.latest() is not None)
        m.set_active(False)
        audio.master["vol"] = 77
        m.set_active(True)
        self.assertTrue(wait_until(lambda: m.latest() is not None and m.latest()[1]["master"]["vol"] == 77))


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class RealAudioTest(unittest.TestCase):
    def test_the_real_readings_have_the_documented_shape(self):
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)
        reading = mixmod.WindowsAudio().read()
        self.assertTrue(0 <= reading["master"]["vol"] <= 100)
        self.assertIsInstance(reading["master"]["mute"], bool)
        for p in reading["programs"]:
            self.assertTrue(p["id"] and p["id"] == p["id"].lower() and not p["id"].endswith(".exe"))
            self.assertTrue(0 <= p["vol"] <= 100)
            self.assertIsInstance(p["active"], bool)
        self.assertEqual(len({p["id"] for p in reading["programs"]}), len(reading["programs"]))  # one entry per program


# ---- weather -------------------------------------------------------------------------------------------------

SAMPLE = {"current": {"temperature_2m": 8.6, "weather_code": 61}, "daily": {"temperature_2m_max": [11.2], "temperature_2m_min": [5.4]}}


class WeatherTest(unittest.TestCase):
    def test_codes_become_words(self):
        self.assertEqual([wx.describe(c) for c in (0, 3, 61, 95, 77)], ["Clear", "Overcast", "Light rain", "Thunderstorm", "Snow grains"])
        self.assertEqual([wx.describe(c) for c in (42, None, "x")], ["Unknown"] * 3)

    def test_parsing_an_answer(self):
        self.assertEqual(wx.parse_forecast(SAMPLE, "c"), {"ok": True, "temp": 9, "unit": "C", "code": 61, "text": "Light rain", "hi": 11, "lo": 5})
        self.assertEqual(wx.parse_forecast(SAMPLE, "f")["unit"], "F")
        for bad in ({}, {"current": {}}, {"current": SAMPLE["current"], "daily": {"temperature_2m_max": []}}, {"current": {"temperature_2m": "x", "weather_code": 1}, "daily": SAMPLE["daily"]}, None):
            with self.assertRaises(ValueError, msg=str(bad)):
                wx.parse_forecast(bad, "c")

    def test_the_request_carries_the_place_and_nothing_else(self):
        seen = []
        wx.fetch_weather(-33.8688, 151.2093, "f", get=lambda url: seen.append(url) or SAMPLE)
        url = seen[0]
        self.assertTrue(url.startswith("https://api.open-meteo.com/v1/forecast?"))
        for part in ("latitude=-33.8688", "longitude=151.2093", "temperature_unit=fahrenheit", "timezone=auto"):
            self.assertIn(part, url)
        self.assertEqual(url.count("="), url.count("&") + 1)   # only the documented parameters
        seen.clear()
        wx.fetch_weather(1.5, 2.5, "c", get=lambda u: seen.append(u) or SAMPLE)
        self.assertIn("temperature_unit=celsius", seen[0])

    def test_only_open_meteo_is_ever_asked(self):
        for url in ("https://example.com/x", "http://localhost:1/", "https://api.open-meteo.com.evil.example/v1"):
            with self.assertRaises(ValueError, msg=url):
                wx.get_json(url)

    def test_place_search(self):
        answer = {"results": [{"name": "Paris", "admin1": "Île-de-France", "country": "France", "latitude": 48.85341, "longitude": 2.3488},
                              {"name": "Broken"}, {"name": "Paris", "latitude": 33.66, "longitude": -95.55}]}
        seen = []
        found = wx.search_places("Pa ris & co", get=lambda url: seen.append(url) or answer)
        self.assertEqual(found, [{"name": "Paris", "region": "Île-de-France", "country": "France", "lat": 48.8534, "lon": 2.3488},
                                 {"name": "Paris", "region": "", "country": "", "lat": 33.66, "lon": -95.55}])  # the broken entry is skipped
        self.assertIn("name=Pa+ris+%26+co", seen[0])                  # what you typed is encoded, not pasted into the address
        self.assertEqual(wx.search_places("zzzz", get=lambda u: {}), [])
        for bad in ("", "a", "x" * 81, "  "):
            with self.assertRaises(ValueError, msg=repr(bad)):
                wx.search_places(bad, get=lambda u: answer)


class WeatherFeedTest(unittest.TestCase):
    def feed(self, get, now):
        feed = wx.WeatherFeed(get=get, clock=lambda: now[0])
        feed.start()
        self.addCleanup(feed.stop)
        return feed

    def test_nothing_is_fetched_until_active_and_configured(self):
        calls = []
        feed = self.feed(lambda url: calls.append(url) or SAMPLE, [0.0])
        feed.set_active(True)
        time.sleep(0.3)
        self.assertEqual(calls, [])           # active but no place: nothing leaves the PC
        feed.configure({"name": "Oslo", "lat": 59.91, "lon": 10.75}, "c")
        feed.set_active(False)
        time.sleep(0.3)
        self.assertEqual(calls, [])           # a place but the screen is not showing
        feed.set_active(True)
        self.assertTrue(wait_until(lambda: feed.latest() is not None))
        self.assertEqual(feed.latest()[1]["place"], "Oslo")
        self.assertEqual(len(calls), 1)

    def test_refresh_interval_and_changes_of_place_or_units(self):
        calls, now = [], [100.0]
        feed = self.feed(lambda url: calls.append(url) or SAMPLE, now)
        feed.configure({"name": "A", "lat": 1, "lon": 2}, "c")
        feed.set_active(True)
        wait_until(lambda: feed.latest() is not None)
        seq = feed.latest()[0]
        now[0] += wx.REFRESH_S - 10
        feed.set_active(True)
        time.sleep(1.2)
        self.assertEqual(len(calls), 1)       # not due yet
        now[0] += 20
        self.assertTrue(wait_until(lambda: len(calls) == 2, 4))
        feed.configure({"name": "B", "lat": 3, "lon": 4}, "f")
        self.assertTrue(wait_until(lambda: len(calls) == 3 and feed.latest() and feed.latest()[1]["place"] == "B", 4))
        self.assertIn("fahrenheit", calls[-1])
        self.assertGreater(feed.latest()[0], seq)

    def test_failures_are_reported_and_retried_sooner(self):
        calls, now = [], [0.0]

        def get(url):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("no network")
            return SAMPLE

        feed = self.feed(get, now)
        feed.configure({"name": "A", "lat": 1, "lon": 2}, "c")
        feed.set_active(True)
        self.assertTrue(wait_until(lambda: feed.latest() is not None))
        self.assertEqual(feed.latest()[1], {"ok": False, "error": "Weather unavailable"})
        now[0] += wx.RETRY_S + 1
        feed.set_active(True)
        self.assertTrue(wait_until(lambda: feed.latest()[1].get("ok") is True, 4))


# ---- the deck file -------------------------------------------------------------------------------------------

def deck_with(*desktops):
    return {"desktops": [{"name": "K", "pages": [{"keys": []}]}, *desktops]}


class DeckKindsTest(unittest.TestCase):
    def test_a_mixer_desktop(self):
        deck = Deck(deck_with({"name": "Mix", "kind": "mixer", "options": {"apps": ["Spotify.exe", "chrome", "spotify"]}}))
        self.assertTrue(deck.has_mixer)
        self.assertEqual(deck.mixer_pins, ["spotify", "chrome"])
        self.assertEqual(deck.layout["desktops"][1], {"name": "Mix", "kind": "mixer", "options": {}})   # the board needs nothing
        self.assertEqual(Deck(deck_with({"kind": "mixer"})).mixer_pins, [])
        for bad in ({"apps": "x"}, {"apps": [1]}, {"apps": [""]}, {"apps": ["a"] * 5}, {"loud": 1}):
            if "loud" in bad:
                self.assertTrue(any("unknown option" in w for w in Deck(deck_with({"kind": "mixer", "options": bad})).warnings))
                continue
            with self.assertRaises(DeckError, msg=str(bad)):
                Deck(deck_with({"kind": "mixer", "options": bad}))

    def test_a_clock_desktop(self):
        deck = Deck(deck_with({"name": "Clock", "kind": "clock", "options": {
            "hour12": True, "seconds": True, "timer": False, "units": "f", "place": {"name": "Oslo", "lat": 59.9139, "lon": 10.7522}}}))
        self.assertTrue(deck.has_clock and deck.clock_weather)
        self.assertEqual((deck.clock_place, deck.clock_units), ({"name": "Oslo", "lat": 59.9139, "lon": 10.7522}, "f"))
        self.assertEqual(deck.layout["desktops"][1]["options"], {"hour12": True, "seconds": True, "timer": False, "weather": True})
        self.assertNotIn("59.9", deck.layout_text())                 # where you are stays on the PC
        plain = Deck(deck_with({"kind": "clock"}))
        self.assertEqual(plain.layout["desktops"][1]["options"], {"hour12": False, "seconds": False, "timer": True, "weather": False})
        self.assertTrue(any("no place is chosen" in w for w in plain.warnings))
        silent = Deck(deck_with({"kind": "clock", "options": {"weather": False}}))
        self.assertEqual(silent.warnings, [])
        off = Deck(deck_with({"kind": "clock", "options": {"weather": False, "place": {"name": "Oslo", "lat": 1, "lon": 2}}}))
        self.assertFalse(off.clock_weather)

    def test_bad_clock_options(self):
        place = {"name": "Oslo", "lat": 1, "lon": 2}
        for bad in ({"hour12": 1}, {"units": "k"}, {"place": "Oslo"}, {"place": {"name": "", "lat": 1, "lon": 2}}, {"place": {"name": "x", "lat": 91, "lon": 2}},
                    {"place": {"name": "x", "lat": 1, "lon": 181}}, {"place": {"name": "x", "lat": "1", "lon": 2}}, {"place": {"name": "x", "lat": True, "lon": 2}},
                    {"place": {"name": "x" * 61, "lat": 1, "lon": 2}}, {"place": {"name": "x", "lat": 1}}, {"timer": "yes"}, {"weather": None}):
            with self.assertRaises(DeckError, msg=str(bad)):
                Deck(deck_with({"kind": "clock", "options": {"place": place, **bad}}))

    def test_one_of_each_at_most(self):
        for kind in ("mixer", "clock"):
            with self.assertRaises(DeckError):
                Deck(deck_with({"kind": kind}, {"kind": kind}))
        Deck(deck_with({"kind": "mixer"}, {"kind": "clock"}, {"kind": "stats"}, {"kind": "nowplaying"}))
        with self.assertRaises(DeckError):
            Deck(deck_with({"kind": "radar"}))


# ---- the companion loop --------------------------------------------------------------------------------------

class FakeMixer:
    def __init__(self):
        self.snap, self.seq = {"master": {"vol": 40, "mute": False}, "apps": [{"id": "spotify", "name": "Spotify", "vol": 70, "mute": False}]}, 1
        self.active, self.pins, self.activations, self.calls = False, None, [], []
        self.error = None

    def configure(self, pins):
        self.pins = list(pins)

    def set_active(self, on):
        self.active = on
        self.activations.append(on)

    def latest(self):
        return (self.seq, self.snap) if self.active else None

    def set_volume(self, target, percent):
        self.calls.append(("volume", target, percent))

    def set_mute(self, target, mute):
        self.calls.append(("mute", target, mute))


class FakeWeather:
    def __init__(self):
        self.result, self.seq = {"ok": True, "temp": 20, "unit": "C", "code": 0, "text": "Clear", "hi": 24, "lo": 14, "place": "Oslo"}, 1
        self.active, self.configured, self.activations = False, [], []

    def configure(self, place, units="c"):
        self.configured.append((place, units))

    def set_active(self, on):
        self.active = on
        self.activations.append(on)

    def latest(self):
        return (self.seq, self.result) if self.active else None


class LoopTest(unittest.TestCase):
    def run_session(self, desktops, steps, desktop_events=(), mixer=None, weather=None, wall=None, **kw):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "deck.json"
        path.write_text(json.dumps(deck_with(*desktops)), encoding="utf-8")
        board = FakeBoard(crc="x", desktop=0)
        app = Companion(path, executor=Recorder(), open_board_fn=lambda p: board, clock=Clock(), mixer=mixer, weather=weather,
                        wall_clock=wall or (lambda: 1_700_000_000.0), **kw)
        idle = {"n": 0}

        def on_idle():
            n = idle["n"]
            idle["n"] += 1
            if n < len(steps):
                steps[n](app, board)
            else:
                app.stop()

        board.on_idle = on_idle
        app.session(board)
        return app, board

    @staticmethod
    def sent(board, kind):
        return [m for m in board.out if m["type"] == kind]

    def go(self, index):
        return lambda app, board: board.inbox.append({"type": "desktop", "index": index})

    def test_the_mixer_runs_only_while_its_desktop_shows(self):
        mixer = FakeMixer()
        steps = [self.go(1), lambda a, b: None, lambda a, b: setattr(mixer, "snap", {**mixer.snap, "master": {"vol": 55, "mute": False}}) or setattr(mixer, "seq", 2),
                 lambda a, b: None, self.go(0), lambda a, b: None]
        app, board = self.run_session([{"name": "Mix", "kind": "mixer", "options": {"apps": ["chrome"]}}], steps, mixer=mixer)
        self.assertEqual(mixer.activations, [True, False])
        self.assertEqual(mixer.pins, ["chrome"])
        msgs = self.sent(board, "mixer")
        self.assertEqual([m["master"]["vol"] for m in msgs], [40, 55])      # once per change, nothing while it is not showing
        self.assertEqual(msgs[0]["apps"][0]["id"], "spotify")

    def test_faders_moved_on_the_board_reach_the_mixer(self):
        mixer = FakeMixer()
        steps = [self.go(1), lambda a, b: b.inbox.append({"type": "mixer-set", "id": "master", "vol": 33}),
                 lambda a, b: b.inbox.append({"type": "mixer-mute", "id": "spotify", "mute": True}),
                 lambda a, b: b.inbox.append({"type": "mixer-set", "id": "spotify"}),      # no value: ignored
                 lambda a, b: None]
        self.run_session([{"kind": "mixer"}], steps, mixer=mixer)
        self.assertEqual(mixer.calls, [("volume", "master", 33), ("mute", "spotify", True)])

    def test_a_deck_without_a_mixer_never_creates_one(self):
        with mock.patch("streamdeck_companion.mixer.AudioMixer", side_effect=AssertionError("created")):
            self.run_session([{"kind": "clock"}], [self.go(1), lambda a, b: None])
        mixer = FakeMixer()
        self.run_session([{"kind": "mixer"}], [self.go(1), lambda a, b: None], mixer=mixer, mixer_enabled=False)
        self.assertNotIn(True, mixer.activations)    # --no-mixer: never started

    def test_the_clock_is_told_the_time_at_once_and_every_half_minute(self):
        now = {"t": 1_700_000_000.0}
        steps = [self.go(1)] + [lambda a, b: None] * 40
        app, board = self.run_session([{"kind": "clock"}], steps, wall=lambda: now["t"])
        clocks = self.sent(board, "clock")
        self.assertGreaterEqual(len(clocks), 1)
        self.assertEqual(clocks[0]["epoch"], 1_700_000_000)
        self.assertIsInstance(clocks[0]["offsetMin"], int)
        self.assertTrue(-12 * 60 <= clocks[0]["offsetMin"] <= 14 * 60)
        # the loop clock of the fake board advances 0.05 s per call, so 30 s of it is many iterations: sent again
        app2, board2 = self.run_session([{"kind": "clock"}], [self.go(1)] + [lambda a, b: None] * 800, wall=lambda: now["t"])
        self.assertGreaterEqual(len(self.sent(board2, "clock")), 2)

    def test_nothing_about_time_is_sent_while_another_desktop_shows(self):
        _, board = self.run_session([{"kind": "clock"}], [lambda a, b: None] * 5)
        self.assertEqual(self.sent(board, "clock"), [])

    def test_the_weather_follows_the_feed(self):
        weather = FakeWeather()
        place = {"name": "Oslo", "lat": 59.91, "lon": 10.75}
        steps = [self.go(1), lambda a, b: None, lambda a, b: setattr(weather, "result", {**weather.result, "temp": 22}) or setattr(weather, "seq", 2),
                 lambda a, b: None, self.go(0), lambda a, b: None]
        app, board = self.run_session([{"kind": "clock", "options": {"place": place, "units": "f"}}], steps, weather=weather)
        self.assertEqual([m["temp"] for m in self.sent(board, "weather")], [20, 22])
        self.assertEqual(weather.activations, [True, False])
        self.assertIn((place, "f"), weather.configured)
        self.assertEqual(self.sent(board, "weather")[0]["place"], "Oslo")

    def test_no_weather_without_a_place_or_with_the_switch_off(self):
        weather = FakeWeather()
        self.run_session([{"kind": "clock"}], [self.go(1), lambda a, b: None], weather=weather)
        self.assertNotIn(True, weather.activations)                    # no place: the weather is not even asked for
        place = {"name": "Oslo", "lat": 1, "lon": 2}
        weather2 = FakeWeather()
        self.run_session([{"kind": "clock", "options": {"place": place}}], [self.go(1), lambda a, b: None], weather=weather2, weather_enabled=False)
        self.assertNotIn(True, weather2.activations)                   # --no-weather: never started
        with mock.patch("streamdeck_companion.weather.WeatherFeed", side_effect=AssertionError("created")):
            self.run_session([{"kind": "clock", "options": {"place": place}}], [self.go(1), lambda a, b: None], weather_enabled=False)


class PlacesEndpointTest(unittest.TestCase):
    def test_the_editor_can_search_for_places(self):
        import http.client

        from streamdeck_companion.editor import EditorServer

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "deck.json"
        path.write_text(json.dumps(deck_with()), encoding="utf-8")

        def places(query):
            if query == "boom":
                raise OSError("offline")
            if len(query) < 2:
                raise ValueError("type at least two letters of the place")
            return [{"name": query, "region": "", "country": "", "lat": 1.0, "lon": 2.0}]

        for fn, query, expect in ((places, "Oslo", 200), (places, "a", 400), (places, "boom", 502), (None, "Oslo", 501)):
            server = EditorServer(path, port=0, places_fn=fn)
            server.start()
            self.addCleanup(server.stop)
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", f"/api/places?q={query}", headers={"Host": f"127.0.0.1:{server.port}"})
            r = conn.getresponse()
            body = json.loads(r.read())
            conn.close()
            self.assertEqual(r.status, expect, (query, body))
            if expect == 200:
                self.assertEqual(body["places"][0]["name"], "Oslo")


if __name__ == "__main__":
    unittest.main()
