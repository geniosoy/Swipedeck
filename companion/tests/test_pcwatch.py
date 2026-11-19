import sys
import time
import unittest
from unittest import mock

from streamdeck_companion import pcwatch
from streamdeck_companion.pcwatch import PcState, PcWatcher, check_state_source, key_states_on, match_desktop, normalize_app


class HelpersTest(unittest.TestCase):
    def test_program_names_compare_without_case_spaces_or_exe(self):
        self.assertEqual(normalize_app(" OBS64.exe "), "obs64")
        self.assertEqual(normalize_app("Code"), "code")
        self.assertEqual(normalize_app("notepad.EXE"), "notepad")
        self.assertEqual(normalize_app(".exe"), "")

    def test_state_sources(self):
        self.assertEqual(check_state_source("mute"), "mute")
        self.assertEqual(check_state_source("mic_mute"), "mic_mute")
        self.assertEqual(check_state_source("app:OBS64.exe"), "app:obs64")
        for bad in ("loud", "app:", "app:.exe", "", None, 3, "Mute"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                check_state_source(bad)

    def test_the_first_matching_desktop_wins(self):
        rules = [(1, {"obs64"}), (3, {"code", "devenv"}), (4, {"code"})]
        self.assertEqual(match_desktop(rules, "obs64"), 1)
        self.assertEqual(match_desktop(rules, "code"), 3)
        self.assertIsNone(match_desktop(rules, "chrome"))
        self.assertIsNone(match_desktop(rules, None))
        self.assertIsNone(match_desktop([], "obs64"))

    def test_which_keys_are_on(self):
        keys = [(0, 0, 0, "mute"), (0, 0, 1, "mic_mute"), (1, 2, 3, "app:obs64"), (0, 1, 5, "app:spotify")]
        self.assertEqual(key_states_on(keys, PcState()), [])
        self.assertEqual(key_states_on(keys, PcState(mute=True)), ["0/0/0"])
        self.assertEqual(key_states_on(keys, PcState(mute=False, mic_mute=True)), ["0/0/1"])
        self.assertEqual(key_states_on(keys, PcState(mute=True, mic_mute=True, running=frozenset({"obs64", "spotify"}))),
                         ["0/0/0", "0/0/1", "0/1/5", "1/2/3"])
        self.assertEqual(key_states_on(keys, PcState(mute=None, running=frozenset({"chrome"}))), [])  # unknown is not "on"


class WatcherTest(unittest.TestCase):
    def setUp(self):
        for name in ("POLL_S", "MUTE_EVERY_S", "PROCESSES_EVERY_S"):  # fast polling, so the tests do not take seconds
            patcher = mock.patch.object(pcwatch, name, 0.02)
            patcher.start()
            self.addCleanup(patcher.stop)

    def watcher(self, **readers):
        w = PcWatcher(readers={"foreground": lambda: "obs64", "locked": lambda: True, "mute": lambda: True,
                               "mic": lambda: False, "running": lambda: frozenset({"obs64", "chrome", "spotify"}), **readers})
        self.addCleanup(w.stop)
        return w

    def wait_for(self, w, cond, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            st = w.state()
            if st is not None and cond(st):
                return st
            time.sleep(0.02)
        self.fail(f"the watcher never reached the expected state, last {w.state()}")

    def test_nothing_is_read_unless_asked(self):
        calls = []
        w = self.watcher(foreground=lambda: calls.append("fg"), mute=lambda: calls.append("mute"), running=lambda: calls.append("run"))
        w.start()
        w.configure()  # nothing wanted
        self.wait_for(w, lambda s: True)
        time.sleep(0.2)
        self.assertEqual(calls, [])
        self.assertEqual(w.state(), PcState())

    def test_reads_what_is_wanted_and_only_the_watched_programs(self):
        w = self.watcher()
        w.configure(foreground=True, mute=True, apps={"obs64", "discord"})
        w.start()
        st = self.wait_for(w, lambda s: s.foreground == "obs64" and s.mute is True and s.running)
        self.assertEqual(st.running, frozenset({"obs64"}))  # chrome and spotify run but nobody asked about them
        self.assertIsNone(st.mic_mute)                       # the microphone was not asked for
        self.assertFalse(st.locked)                          # the lock was not asked for

    def test_a_one_off_lock_reading_is_ignored_but_a_steady_one_counts(self):
        blips = iter([True, False] * 40)   # the lock screen flashes by (a UAC prompt): never two in a row
        w = self.watcher(locked=lambda: next(blips, False))
        w.configure(lock=True)
        w.start()
        time.sleep(0.6)
        self.assertFalse(w.state().locked)

        steady = self.watcher(locked=lambda: True)
        steady.configure(lock=True)
        steady.start()
        self.wait_for(steady, lambda s: s.locked is True)

    def test_a_failing_reader_does_not_stop_the_thread(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise RuntimeError("boom")
            return "code"

        w = self.watcher(foreground=flaky)
        w.configure(foreground=True)
        w.start()
        self.wait_for(w, lambda s: s.foreground == "code")


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WindowsReadersTest(unittest.TestCase):
    """The real readers: they must never raise and must return the documented types."""

    def test_readers_return_their_types(self):
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)
        fg = pcwatch.foreground_app()
        self.assertTrue(fg is None or (isinstance(fg, str) and fg == fg.lower() and not fg.endswith(".exe")))
        self.assertIn(pcwatch.session_locked(), (True, False, None))
        for capture in (False, True):
            self.assertIn(pcwatch.endpoint_muted(capture), (True, False, None))
        running = pcwatch.running_apps()
        self.assertIsInstance(running, frozenset)
        apps = pcwatch.windowed_apps()
        self.assertEqual(apps, sorted(set(apps)))
        self.assertTrue(all(a == a.lower() for a in apps))


if __name__ == "__main__":
    unittest.main()
