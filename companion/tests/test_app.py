import unittest
from pathlib import Path

from streamdeck_companion.actions import Executor
from streamdeck_companion.app import Companion
from streamdeck_companion.nowplaying import Snapshot
from streamdeck_companion.protocol import crc_hex

DECK = Path(__file__).resolve().parent.parent / "deck.json"


class FakeBoard:
    """Speaks the firmware's protocol (see src/link.h). `script` lists messages to emit after the link is up."""

    def __init__(self, crc="", accept=True, script=(), silent=False, desktop=0):
        self.crc, self.accept, self.silent, self.desktop = crc, accept, silent, desktop
        self.script = list(script)
        self.out = []          # what the companion sent
        self.inbox = []        # what the board will "send" next
        self.layout_parts = []
        self.cover_parts = []
        self.settings = None
        self.on_idle = None

    def send(self, msg):
        self.out.append(msg)
        if self.silent:
            return
        t = msg["type"]
        if t == "hello":
            self.inbox.append({"type": "hello", "protocol": 1, "firmware": "t", "screen": {"w": 320, "h": 240},
                               "layoutCrc": self.crc, "desktop": self.desktop})
        elif t == "ping":
            self.inbox.append({"type": "pong", "id": msg["id"]})
        elif t == "settings":
            self.settings = msg
            self.inbox.append({"type": "settings-ack"})
        elif t == "cover":
            self.cover_parts.append(msg)
            if msg["off"] + 3000 >= msg["total"]:
                self.inbox.append({"type": "cover-ack", "ok": True, "id": msg["id"]})
            else:
                self.inbox.append({"type": "cover-part", "off": msg["off"]})
        elif t == "layout":
            self.layout_parts.append(msg["data"])
            if msg["seq"] < msg["of"] - 1:
                self.inbox.append({"type": "layout-part", "seq": msg["seq"]})
            elif self.accept:
                self.crc = crc_hex("".join(self.layout_parts))
                self.desktop = 0  # like the real board: a freshly loaded layout starts on the first desktop
                self.inbox.append({"type": "desktop", "index": 0})
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


class FakeReader:
    def __init__(self, snap=None, cover=None, handled=True):
        self.snap = snap or Snapshot()
        self.cv = cover
        self.handled = handled
        self.commands, self.seeks = [], []
        self.active = False
        self.activations = []   # every set_active call, in order

    def set_active(self, on):
        self.active = on
        self.activations.append(on)

    def ready(self):
        return self.active

    def snapshot(self):
        return self.snap

    def cover(self):
        return self.cv

    def command(self, cmd):
        self.commands.append(cmd)
        return self.handled

    def seek(self, ms):
        self.seeks.append(ms)
        return True


class FakeStats:
    def __init__(self):
        self.active = False
        self.activations = []
        self.seq = 0

    def set_active(self, on):
        self.active = on
        self.activations.append(on)

    def latest(self):
        if not self.active:
            return None
        self.seq += 1
        return (self.seq, {"cpu": 12, "gpu": 3})


def make(board, clock=None, reader=None, stats=None):
    rec = Recorder()
    app = Companion(DECK, executor=rec, open_board_fn=lambda port: board, clock=clock or Clock(),
                    reader=reader, media=reader is not None, stats=stats, stats_enabled=stats is not None)
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

    def test_now_playing_sends_cover_then_state(self):
        reader = FakeReader(Snapshot(state="playing", title="Canción", artist="Ñandú", pos_ms=5000, dur_ms=200000,
                                     can_seek=True, can_play=True), cover=("abcd1234", bytes(112 * 112 * 2), "#202840"))
        board = FakeBoard(script=[{"type": "desktop", "index": 1}, None, None])  # swipe to Playing (index 1)
        app, _ = make(board, reader=reader)
        app.session(board)
        types = [m["type"] for m in board.out]
        self.assertIn("np", types)
        self.assertLess(types.index("cover"), types.index("np"))  # image first
        self.assertEqual(sum(m["type"] == "cover" for m in board.out), 9)  # 25088 bytes / 3000
        np_msg = next(m for m in board.out if m["type"] == "np")
        self.assertEqual((np_msg["title"], np_msg["artist"], np_msg["posMs"]), ("Cancion", "Nandu", 5000))
        self.assertEqual(types.count("np"), 1)  # unchanged state is not repeated
        self.assertTrue(all(m["bg"] == "#202840" for m in board.out if m["type"] == "cover"))

    def test_no_cover_means_nothing_sent(self):
        reader = FakeReader(Snapshot(state="playing", title="x"), cover=None)
        board = FakeBoard(script=[{"type": "desktop", "index": 1}, None, None])
        app, _ = make(board, reader=reader)
        app.session(board)
        self.assertFalse(any(m["type"] in ("cover", "cover-clear") for m in board.out))

    def test_nothing_runs_while_a_keys_desktop_is_shown(self):
        reader, stats = FakeReader(Snapshot(state="playing", title="x")), FakeStats()
        board = FakeBoard(crc="x", script=[None] * 5, desktop=0)
        app, _ = make(board, reader=reader, stats=stats)
        app.session(board)
        self.assertEqual(reader.activations, [])
        self.assertEqual(stats.activations, [])
        self.assertFalse(any(m["type"] in ("np", "stats", "cover") for m in board.out))

    def test_screens_start_when_swiped_to_and_stop_when_left(self):
        reader = FakeReader(Snapshot(state="playing", title="x"), cover=("k", bytes(100), "#101010"))
        stats = FakeStats()
        board = FakeBoard(crc="x", script=[
            {"type": "desktop", "index": 1}, None, None,   # to Playing
            {"type": "desktop", "index": 2}, None, None,   # to PC stats
            {"type": "desktop", "index": 0}, None, None,   # back to buttons
            {"type": "desktop", "index": 1}, None, None])  # Playing again
        app, _ = make(board, reader=reader, stats=stats)
        app.session(board)
        self.assertEqual(reader.activations, [True, False, True, False])  # also stopped when the link ends
        self.assertEqual(stats.activations, [True, False])
        types = [m["type"] for m in board.out]
        self.assertGreaterEqual(types.count("np"), 2)  # the full state is sent again on returning
        self.assertTrue(any(m["type"] == "stats" and m["cpu"] == 12 for m in board.out))
        # the stats were sent only between entering and leaving the PC desktop
        first_stats = next(i for i, m in enumerate(board.out) if m["type"] == "stats")
        self.assertGreater(first_stats, next(i for i, m in enumerate(board.out) if m["type"] == "np"))

    def test_hello_tells_which_desktop_is_showing(self):
        reader = FakeReader(Snapshot(state="paused", title="x"))
        board = FakeBoard(script=[None] * 2, desktop=1)
        app, _ = make(board, reader=reader)
        board.crc = crc_hex(app.deck.layout_text())  # layout already on the board: it stays on desktop 1
        app.session(board)
        self.assertEqual(reader.activations[0], True)  # started right away: the board was already on Playing

    def test_link_loss_stops_the_readers(self):
        reader, stats = FakeReader(Snapshot(state="playing", title="x")), FakeStats()
        board = FakeBoard(script=[{"type": "desktop", "index": 2}])  # swipe to the PC stats desktop
        app, _ = make(board, reader=reader, stats=stats)
        board.on_idle = lambda: setattr(board, "silent", True)
        with self.assertRaises(ConnectionError):
            app.session(board)
        self.assertEqual(stats.activations, [True, False])

    def test_media_and_seek_messages_go_to_the_player(self):
        reader = FakeReader()
        board = FakeBoard(crc="x", script=[{"type": "media", "cmd": "next"}, {"type": "seek", "posMs": 42000}])
        app, rec = make(board, reader=reader)
        app.session(board)
        self.assertEqual(reader.commands, ["next"])
        self.assertEqual(reader.seeks, [42000])
        self.assertEqual(rec.ran, [])

    def test_media_falls_back_to_media_keys_without_a_session(self):
        reader = FakeReader(handled=False)
        board = FakeBoard(crc="x", script=[{"type": "media", "cmd": "toggle"}, {"type": "media", "cmd": "prev"}])
        app, rec = make(board, reader=reader)
        app.session(board)
        self.assertEqual([a for _, a in rec.ran], [{"type": "hotkey", "keys": "play_pause"},
                                                    {"type": "hotkey", "keys": "prev_track"}])


if __name__ == "__main__":
    unittest.main()
