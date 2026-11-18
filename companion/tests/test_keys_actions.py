import unittest

from streamdeck_companion.actions import Executor, validate_action
from streamdeck_companion.keys import parse_hotkey


class KeysTest(unittest.TestCase):
    def test_hotkey_order_and_codes(self):
        self.assertEqual(parse_hotkey("ctrl+shift+s"), [0x11, 0x10, 0x53])
        self.assertEqual(parse_hotkey("win+e"), [0x5B, 0x45])
        self.assertEqual(parse_hotkey("F13"), [0x7C])
        self.assertEqual(parse_hotkey("volume_mute"), [0xAD])
        self.assertEqual(parse_hotkey(" Ctrl + 1 "), [0x11, 0x31])

    def test_bad_keys(self):
        for bad in ("", "ctrl+", "ctrl+nope", "f25"):
            with self.assertRaises(ValueError, msg=bad):
                parse_hotkey(bad)


class ActionsTest(unittest.TestCase):
    def test_validate(self):
        good = [
            {"type": "hotkey", "keys": "ctrl+c"}, {"type": "text", "text": "x"}, {"type": "lock"},
            {"type": "open", "target": "https://x"}, {"type": "run", "command": ["wt"]},
            {"type": "run", "command": "code", "shell": True},
            {"type": "multi", "steps": [{"type": "lock"}, {"wait": 0.5}]},
        ]
        for a in good:
            validate_action(a)
        bad = [
            {}, {"type": "nope"}, {"type": "hotkey", "keys": "ctrl+zzz"}, {"type": "text", "text": ""},
            {"type": "run", "command": "code"}, {"type": "run", "command": []},
            {"type": "multi", "steps": []}, {"type": "multi", "steps": [{"wait": 99}]},
            {"type": "multi", "steps": [{"type": "hotkey", "keys": "bad+key"}]},
        ]
        for a in bad:
            with self.assertRaises(ValueError, msg=str(a)):
                validate_action(a)

    def test_executor_uses_backend_and_multi_sequence(self):
        calls = []
        ex = Executor(backend=(lambda codes: calls.append(("hotkey", codes)),
                               lambda s: calls.append(("text", s)),
                               lambda: calls.append(("lock",))))
        ex.run({"type": "multi", "steps": [
            {"type": "text", "text": "git commit"}, {"wait": 0}, {"type": "hotkey", "keys": "left"}, {"type": "lock"}]})
        self.assertEqual(calls, [("text", "git commit"), ("hotkey", [0x25]), ("lock",)])

    def test_dry_run_does_nothing(self):
        ex = Executor(dry_run=True, backend=(None, None, None))
        ex.run({"type": "lock"})  # would raise if it touched the backend

    def test_failure_in_async_action_is_contained(self):
        def boom(_):
            raise OSError("nope")
        ex = Executor(backend=(boom, boom, boom))
        ex.run_async({"type": "hotkey", "keys": "a"}).join(2)  # must not raise


if __name__ == "__main__":
    unittest.main()
