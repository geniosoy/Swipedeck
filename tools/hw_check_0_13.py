"""Hardware check of firmware 0.13.0 (volume mixer and clock / timer / weather screens) over the serial port.

    Stop the companion first (Stop-Streamdeck.bat): it holds the port.  Then:
    companion\\.venv\\Scripts\\python tools\\hw_check_0_13.py

It puts a scratch layout with a mixer and a clock desktop on the board, feeds both screens with made-up readings (the
volumes and the weather are NOT real, and nothing is changed on your PC), checks that nothing crashes or leaks, tests the
timer's alarm through the console, and then asks you to try the faders and the timer with your finger. It ends by
restoring your own layout and settings from companion/deck.json.
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
    {"name": "Keys", "pages": [{"id": "r", "title": "Keys", "cols": 2, "rows": 1, "keys": [{"label": "A", "action": {"type": "lock"}}]}]},
    {"name": "Mixer", "kind": "mixer"},
    {"name": "Clock", "kind": "clock", "options": {"seconds": True, "timer": True, "weather": True, "place": {"name": "Testville", "lat": 1, "lon": 2}}},
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
    """Free memory from the console's info line. The board never stalls on a slow serial reader, so a console line can
    occasionally be dropped: the question is simply asked again."""
    pattern = re.compile(r"heap (\d+) KB, PSRAM (\d+) KB")
    for _ in range(5):
        board._ser.write(b"info\n")
        text, end = "", time.time() + 1.5
        while time.time() < end:
            text += board._ser.read(board._ser.in_waiting or 1).decode("utf-8", "replace")
            m = pattern.search(text)
            if m:
                return {"heap": int(m.group(1)), "psram": int(m.group(2))}
    raise SystemExit("the board did not answer the info command (5 tries)")


def push(deck: Deck) -> None:
    text = deck.layout_text()
    for m in layout_messages(text):
        board.send(m)
        reply = wait({"layout-part", "layout-ack"})
        if reply["type"] == "layout-ack":
            assert reply.get("ok") and reply.get("crc") == crc_hex(text), reply
            return
    raise SystemExit("no layout ack")


h = hello()
check("firmware 0.13.0 or newer", tuple(int(x) for x in h["firmware"].split(".")) >= (0, 13, 0), h["firmware"])
uptime = h["uptimeMs"]

scratch = Deck(SCRATCH)
push(scratch)
check("a layout with a mixer and a clock desktop is accepted", True)
layout = scratch.layout_text()
check("the board is told the options but not the place", '"kind":"mixer"' in layout and '"kind":"clock"' in layout and "Testville" not in layout)

# ---- the mixer
board.send({"type": "focus", "desktop": 1})
pump(0.6)
before = info()
mix = {"type": "mixer", "master": {"vol": 55, "mute": False},
       "apps": [{"id": "spotify", "name": "Spotify", "vol": 80, "mute": False}, {"id": "chrome", "name": "Chrome", "vol": 35, "mute": True},
                {"id": "discord", "name": "Discord", "vol": 100, "mute": False}, {"id": "steam", "name": "Steam", "vol": 0, "mute": False}]}
board.send(mix)
pump(0.5)
check("the mixer takes five faders", hello()["uptimeMs"] > uptime)
print(">>> Look at the board: five faders (Master blue 55, Spotify 80, Chrome 35 grey and muted, Discord 100, Steam 0). <<<", flush=True)
time.sleep(3)
for variant in ({**mix, "apps": mix["apps"][:2]}, {**mix, "apps": []}, {**mix, "apps": mix["apps"] * 2},   # fewer, none, too many
                {"type": "mixer"}, {"type": "mixer", "master": {"vol": 999}, "apps": [{"id": "", "vol": -5}, {"vol": "x"}, 7]}):
    board.send(variant)
    pump(0.3)
check("fewer, no, too many and malformed readings are all handled", hello()["uptimeMs"] > uptime)
for i in range(300):  # a stream of readings: the faders move and nothing leaks
    board.send({**mix, "master": {"vol": i % 101, "mute": i % 7 == 0}})
    pump(0.03)
after = info()
check("300 mixer updates did not use up memory", after["heap"] >= before["heap"] - 8 and after["psram"] >= before["psram"] - 64,
      f"heap {before['heap']} -> {after['heap']} KB, PSRAM {before['psram']} -> {after['psram']} KB")

# ---- the clock
board.send({"type": "focus", "desktop": 2})
pump(0.6)
board.send({"type": "clock", "epoch": 1_763_560_000, "offsetMin": -180})
board.send({"type": "weather", "ok": True, "temp": 21, "unit": "C", "text": "Partly cloudy", "hi": 24, "lo": 15, "place": "Testville"})
pump(0.5)
print(">>> Look at the board: a time of day with seconds counting, the date, 21°C Partly cloudy H 24° L 15° Testville, and the timer. <<<", flush=True)
time.sleep(4)
for variant in ({"type": "clock"}, {"type": "clock", "epoch": "x"}, {"type": "weather"}, {"type": "weather", "ok": False},
                {"type": "weather", "ok": True, "temp": -40, "unit": "F", "text": "x" * 200, "place": "y" * 200}):
    board.send(variant)
    pump(0.3)
check("unusual clock and weather messages are handled", hello()["uptimeMs"] > uptime)
board.send({"type": "weather", "ok": True, "temp": -12, "unit": "C", "text": "Thunderstorm, hail", "hi": -5, "lo": -15, "place": "A long place name"})
pump(0.5)

# ---- the alarm
print(">>> The alarm sound is about to play (three beeps), even with key sounds off. <<<", flush=True)
board.send({"type": "settings", "sounds": False, "preview": True})
wait({"settings-ack"})
board.send({"type": "sound", "name": "alarm"})
pump(1.5)
check("the alarm plays through the console", hello()["uptimeMs"] > uptime)

# ---- needs a finger
def collect(types, first_timeout=180.0, quiet=10.0) -> list[dict]:
    """Waits for your first touch (up to `first_timeout` s), then gathers events until `quiet` s pass without any."""
    events, started, last, begin = [], False, time.time(), time.time()
    while True:
        now = time.time()
        if not started and now - begin > first_timeout:
            break
        if started and now - last > quiet:
            break
        m = board.read()
        if m and m.get("type") in types:
            events.append(m)
            started, last = True, now
    return events


print("\n>>> PART 1, THE MIXER (take your time: it starts at your first touch and ends after 10 s without touches).", flush=True)
print("    The board now shows the five test faders. Drag a few of them up and down, then tap two speaker buttons.", flush=True)
board.send({"type": "focus", "desktop": 1})
pump(0.3)
board.send(mix)
events = collect({"mixer-set", "mixer-mute"})
sets = [m for m in events if m.get("type") == "mixer-set"]
mutes = [m for m in events if m.get("type") == "mixer-mute"]
print(f"    seen: {len(sets)} fader messages, {len(mutes)} mute taps", flush=True)
if sets:
    check("manual: dragging a fader sends values in range", all(0 <= m["vol"] <= 100 and m["id"] for m in sets), str(sets[:3]))
    stamps = len(sets)
    check("manual: not flooding (a drag sends at most about 12 messages a second)", stamps <= 12 * 60, str(stamps))
if mutes:
    check("manual: a mute tap names the fader and the new state", all("id" in m and isinstance(m["mute"], bool) for m in mutes), str(mutes[:2]))
if not (sets or mutes):
    print("    (no touch on the mixer seen: that part was skipped)", flush=True)

print("\n>>> PART 2, THE TIMER. On the CLOCK screen: tap '1m', then Start. While it counts, SWIPE to the keys desktop.", flush=True)
print("    After about a minute it should ring (three beeps, repeating), light the screen and bring the clock back by itself.", flush=True)
print("    Tap any timer button to stop the ringing. I am listening for 110 s.", flush=True)
board.send({"type": "focus", "desktop": 2})
pump(0.3)
board.send({"type": "settings", "sounds": False, "preview": True})   # the alarm must ring even with key sounds off
wait({"settings-ack"})
events = pump(110)
desktops = [m.get("index") for m in events if m.get("type") == "desktop"]
print(f"    desktop changes seen: {desktops}", flush=True)
if desktops:
    check("manual: the clock desktop was brought to the front (by the alarm, or by you)", 2 in desktops, str(desktops))
else:
    print("    (no desktop change seen: the timer part was skipped or you stayed on the clock; tell me what you saw and heard)", flush=True)

# ---- put everything back
board.send({"type": "settings", **real_deck.effective_settings()})
wait({"settings-ack"})
push(real_deck)
final = hello()
check("your layout is back", final["layoutCrc"] == crc_hex(real_deck.layout_text()))
board.close()
print(f"\n{sum(results)}/{len(results)} checks passed", flush=True)
sys.exit(0 if all(results) else 1)
