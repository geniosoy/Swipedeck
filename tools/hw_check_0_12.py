"""Hardware check of firmware 0.12.0 (hold / repeat keys, state keys, sleeping with the PC) over the serial port.

    Stop the companion first (Stop-Streamdeck.bat): it holds the port.  Then:
    companion\\.venv\\Scripts\\python tools\\hw_check_0_12.py

It puts a small scratch layout on the board, checks the new messages (no crash, no memory leak, the screen-away logic
through the console's `info` line), then restores your own layout and settings from companion/deck.json. Two parts need
a finger and the script says when: hold and repeat keys, and what the screen looks like.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "companion"))
from streamdeck_companion.deck import Deck  # noqa: E402
from streamdeck_companion.link import open_board  # noqa: E402
from streamdeck_companion.protocol import crc_hex, layout_messages  # noqa: E402

REAL = ROOT / "companion" / "deck.json"
real_deck = Deck(json.loads(REAL.read_text(encoding="utf-8")))

SCRATCH = {"desktops": [
    {"name": "Test", "pages": [{"id": "r", "title": "Hold and state", "cols": 3, "rows": 2, "keys": [
        {"label": "Mute", "icon": "mute", "action": {"type": "hotkey", "keys": "volume_mute"}, "hold": {"type": "lock"},
         "state": {"source": "mute", "label": "Muted", "color": "#B3261E"}},
        {"label": "Vol +", "icon": "volume_up", "action": {"type": "hotkey", "keys": "volume_up"}, "repeat": True},
        {"label": "OBS", "icon": "video", "action": {"type": "lock"}, "state": {"source": "app:obs64", "icon": "ok"}},
        {"label": "Plain", "action": {"type": "lock"}},
        {"label": "Next", "folder": "second"},
    ]}, {"id": "second", "title": "Second", "cols": 2, "rows": 1, "keys": [
        {"label": "Mic", "icon": "bell", "action": {"type": "lock"}, "state": {"source": "mic_mute"}},
    ]}]},
    {"name": "Other", "pages": [{"id": "r", "keys": [{"label": "B", "action": {"type": "lock"}}]}]},
]}

board = open_board(None)
results: list[bool] = []


def check(name: str, cond, detail="") -> None:
    results.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{detail}]"), flush=True)


def pump(seconds: float) -> list[dict]:
    seen, end = [], time.time() + seconds
    while time.time() < end:
        m = board.read()
        if m:
            seen.append(m)
    return seen


def wait(types, timeout=6.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        m = board.read()
        if m and m.get("type") in types:
            return m
    raise SystemExit(f"timeout waiting for {types}")


def hello() -> dict:
    for _ in range(30):
        board.send({"type": "hello"})
        end = time.time() + 1.0
        while time.time() < end:
            m = board.read()
            if m and m.get("type") == "hello":
                return m
    raise SystemExit("the board does not answer hello (is the companion still running?)")


def info() -> dict:
    """The console's info line: screen state, pc locked, link lost, heap and PSRAM."""
    board._ser.reset_input_buffer()
    board._ser.write(b"info\n")
    text, end = "", time.time() + 2.0
    while time.time() < end and "PSRAM" not in text:
        text += board._ser.read(board._ser.in_waiting or 1).decode("utf-8", "replace")
    m = re.search(r"screen (\w+) \(pc locked (\d), link lost (\d)\), heap (\d+) KB, PSRAM (\d+) KB", text)
    if not m:
        raise SystemExit(f"could not read the info line: {text!r}")
    return {"screen": m.group(1), "locked": int(m.group(2)), "lost": int(m.group(3)), "heap": int(m.group(4)), "psram": int(m.group(5))}


def push(deck: Deck) -> None:
    text = deck.layout_text()
    for m in layout_messages(text):
        board.send(m)
        reply = wait({"layout-part", "layout-ack"})
        if reply["type"] == "layout-ack":
            assert reply.get("ok") and reply.get("crc") == crc_hex(text), reply
            return
    raise SystemExit("no layout ack")


def settings(patch: dict, preview=True) -> dict:
    board.send({"type": "settings", **patch, **({"preview": True} if preview else {})})
    return wait({"settings-ack"})


h = hello()
check("firmware 0.12.0 or newer", tuple(int(x) for x in h["firmware"].split(".")) >= (0, 12, 0), h["firmware"])
check("hello announces sleepWithPc", "sleepWithPc" in h["settings"], str(h["settings"]))
uptime = h["uptimeMs"]

scratch = Deck(SCRATCH)
push(scratch)
check("a layout with hold, repeat and state keys is accepted", True)
text = scratch.layout_text()
check("the layout the board got carries them but no actions", '"hold":true' in text and '"repeat":true' in text and '"on":' in text and "volume_mute" not in text)

# ---- state keys
before = info()
board.send({"type": "keystate", "on": ["0/0/0", "0/0/2"]})
pump(0.4)
board.send({"type": "keystate", "on": ["0/1/0"]})   # a key on a page that is not shown
pump(0.4)
board.send({"type": "keystate", "on": []})
pump(0.4)
check("keystate sets and clears without trouble", hello()["uptimeMs"] > uptime)
for bad in ({"type": "keystate"}, {"type": "keystate", "on": "0/0/0"}, {"type": "keystate", "on": ["bad", "9/9/9", "-1/0/0", 5, None, "0/0"]},
            {"type": "keystate", "on": ["0/0/3"]}):  # key 3 has no state: ignored
    board.send(bad)
    pump(0.3)
check("malformed or unknown key ids are ignored", hello()["uptimeMs"] > uptime)
print(">>> Look at the board: 'Mute' and 'OBS' should have lit up red / green for a moment, then gone back. <<<", flush=True)
for i in range(150):  # the visible page is redrawn every time: it must not leak
    board.send({"type": "keystate", "on": ["0/0/0", "0/0/2"] if i % 2 == 0 else []})
    pump(0.05)
board.send({"type": "keystate", "on": []})
pump(0.5)
after = info()
check("150 redraws did not use up memory", after["heap"] >= before["heap"] - 8 and after["psram"] >= before["psram"] - 64,
      f"heap {before['heap']} -> {after['heap']} KB, PSRAM {before['psram']} -> {after['psram']} KB")
check("the board did not reboot", hello()["uptimeMs"] > uptime)

# ---- the screen goes off with the PC
settings({"sleepWithPc": False})
board.send({"type": "pc", "state": "locked"})
pump(0.3)
state = info()
check("with sleepWithPc off a locked PC does not turn the screen off", state["screen"] == "awake" and state["locked"] == 1, str(state))
board.send({"type": "pc", "state": "active"})
settings({"sleepWithPc": True})
board.send({"type": "pc", "state": "locked"})
pump(0.3)
state = info()
check("with sleepWithPc on, locked turns the screen off", state["screen"] == "asleep" and state["locked"] == 1, str(state))
board.send({"type": "pc", "state": "active"})
pump(0.3)
check("...and unlocked turns it on again", info()["screen"] == "awake")
print(">>> Silence for 17 s: the board should notice the PC is gone and turn the screen off (do not touch it). <<<", flush=True)
time.sleep(17)
state = info()
check("17 s without a word from the PC turn the screen off", state["screen"] == "asleep" and state["lost"] == 1, str(state))
hello()
pump(0.3)
state = info()
check("...and the first message turns it on again", state["screen"] == "awake" and state["lost"] == 0, str(state))
settings({"sleepWithPc": False})
time.sleep(17)
check("with sleepWithPc off silence changes nothing", info()["screen"] == "awake")
hello()

# ---- needs a finger
print("\n>>> NOW WITH YOUR FINGER, for 25 s:", flush=True)
print("    1. tap 'Mute' once            -> expect a plain press (no hold, no repeat)", flush=True)
print("    2. hold 'Mute' for ~1 s       -> expect ONE press with hold:true and no plain press after it", flush=True)
print("    3. tap 'Vol +' once           -> expect ONE plain press", flush=True)
print("    4. hold 'Vol +' for ~2 s      -> expect several presses with repeat:true (about 10 per second after 0.4 s)", flush=True)
print("    5. tap 'Plain'                -> expect a plain press as before", flush=True)
events = [m for m in pump(25) if m.get("type") == "press"]
for m in events:
    flags = [f for f in ("hold", "repeat") if m.get(f)]
    print(f"    press key {m['key']} {'+'.join(flags) or 'tap'}", flush=True)
keys = {(m["key"], tuple(f for f in ("hold", "repeat") if m.get(f))) for m in events}
if events:
    check("manual: Mute tap, Mute hold, Vol+ tap, Vol+ hold and Plain tap were all seen",
          {(0, ()), (0, ("hold",)), (1, ()), (1, ("repeat",)), (3, ())} <= keys, str(sorted(keys)))
    check("manual: a held Mute is not also a tap", sum(1 for m in events if m["key"] == 0 and not m.get("hold")) <= 1,
          "more than one plain Mute press")
    check("manual: holding Vol+ repeated (3 or more repeat presses)", sum(1 for m in events if m["key"] == 1 and m.get("repeat")) >= 3)
else:
    print("    (no touch seen: the manual part was skipped)", flush=True)

# ---- put everything back
settings(real_deck.effective_settings(), preview=False)
push(real_deck)
final = hello()
check("your layout is back", final["layoutCrc"] == crc_hex(real_deck.layout_text()))
board.close()
print(f"\n{sum(results)}/{len(results)} checks passed", flush=True)
sys.exit(0 if all(results) else 1)
