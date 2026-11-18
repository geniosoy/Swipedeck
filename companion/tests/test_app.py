import unittest
from pathlib import Path

from streamdeck_companion.actions import Executor
from streamdeck_companion.app import Companion
from streamdeck_companion.protocol import crc_hex

DECK = Path(__file__).resolve().parent.parent / "deck.json"


class FakeBoard:
    """Speaks the firmware's protocol (see src/link.h). `script` lists messages to emit after the link is up."""

    def __init__(self, crc="", accept=True, script=(), silent=False):
        self.crc, self.accept, self.silent = crc, accept, silent
        self.script = list(script)
        self.out = []          # what the companion sent
        self.inbox = []        # what the board will "send" next
        self.layout_parts = []
        self.settings = None
        self.on_idle = None

    def send(self, msg):
        self.out.append(msg)
        if self.silent:
            return
        t = msg["type"]
        if t == "hello":
            self.inbox.append({"type": "hello", "protocol": 1, "firmware": "t", "screen": {"w": 320, "h": 240},
                               "layoutCrc": self.crc})
        elif t == "ping":
            self.inbox.append({"type": "pong", "id": msg["id"]})
        elif t == "settings":
            self.settings = msg
            self.inbox.append({"type": "settings-ack"})
        elif t == "layout":
            self.layout_parts.append(msg["data"])
            if msg["seq"] < msg["of"] - 1:
                self.inbox.append({"type": "layout-part", "seq": msg["seq"]})
            elif self.accept:
                self.crc = crc_hex("".join(self.layout_parts))
                self.inbox.append({"type": "layout-ack", "ok": True, "crc": self.crc})
            else:
                self.inbox.append({"type": "layout-ack", "ok": False, "error": "nope"})

    def read(self):
        if self.inbox:
            return self.inbox.pop(0)
        if self.script:
            return self.script.pop(0)
        if self.on_idle:
            self.on_idle()
        return None

    def close(self):
        pass


class Recorder(Executor):
    def __init__(self):
        super().__init__(dry_run=True)
        self.ran = []

    def run_async(self, action, label=""):
        self.ran.append((label, action))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.05  # every call advances: idle loops terminate
        return self.t


def make(board, clock=None):
    rec = Recorder()
    app = Companion(DECK, executor=rec, open_board_fn=lambda port: board, clock=clock or Clock())
    board.on_idle = app.stop
    return app, rec


class AppTest(unittest.TestCase):
    def test_fresh_board_gets_settings_then_layout_and_press_runs_action(self):
        board = FakeBoard(script=[{"type": "press", "desktop": 0, "page": 0, "key": 1, "id": ""}])
        app, rec = make(board)
        app.session(board)
        types = [m["type"] for m in board.out]
        self.assertEqual(types[0], "hello")
        self.assertLess(types.index("settings"), types.index("layout"))
        self.assertGreater(types.count("layout"), 1)  # chunked
        self.assertEqual(board.settings["volume"], 70)
        self.assertEqual("".join(board.layout_parts), app.deck.layout_text())
        self.assertEqual(rec.ran, [("0/0/1", {"type": "hotkey", "keys": "volume_down"})])

    def test_matching_crc_skips_layout(self):
        probe = FakeBoard()
        app, _ = make(probe)
        board = FakeBoard(crc=crc_hex(app.deck.layout_text()))
        app, _ = make(board)
        app.session(board)
        self.assertNotIn("layout", [m["type"] for m in board.out])

    def test_press_without_action_and_folder_are_ignored(self):
        board = FakeBoard(crc="x", script=[{"type": "press", "desktop": 0, "page": 0, "key": 4, "id": ""},
                                            {"type": "nav", "desktop": 0, "page": 1}])
        app, rec = make(board)
        app.session(board)
        self.assertEqual(rec.ran, [])

    def test_rejected_layout_raises(self):
        board = FakeBoard(accept=False)
        app, _ = make(board)
        with self.assertRaises(ConnectionError):
            app.session(board)

    def test_silent_board_times_out_in_handshake(self):
        board = FakeBoard(silent=True)
        app, _ = make(board)
        with self.assertRaises(ConnectionError):
            app.session(board)

    def test_board_that_stops_answering_after_connect(self):
        board = FakeBoard()
        app, _ = make(board)
        board.on_idle = lambda: setattr(board, "silent", True)  # goes quiet once the sync is done
        with self.assertRaises(ConnectionError):
            app.session(board)


if __name__ == "__main__":
    unittest.main()
