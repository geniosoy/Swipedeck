"""End-to-end check of the web editor in a real browser (Edge or Chrome, headless) over the DevTools protocol.

    pip install websocket-client          (anywhere; it is not a dependency of the companion)
    python tools/e2e_editor.py [--shots DIR]

It serves the editor on a temporary COPY of the demo deck in companion/tests/fixtures (your real deck is never touched), drives the page
like a user (select, edit, presets, drag and drop, undo, save, reload) and checks the saved file. With --shots it also
stores screenshots. Exit code 1 if any check fails.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "companion"))
from streamdeck_companion.deck import Deck  # noqa: E402
from streamdeck_companion.editor import EditorServer  # noqa: E402

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=20, suppress_origin=True)
        self.n = 0
        self.problems: list[str] = []

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.problems.append(d.get("exception", {}).get("description") or d.get("text", "exception"))
            elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] in ("error", "assert"):
                self.problems.append(" ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"]))
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str, await_promise: bool = False):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(d.get("exception", {}).get("description") or d.get("text"))
        return r["result"].get("value")

    def wait(self, cond: str, timeout: float = 6.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(cond):
                return True
            time.sleep(0.1)
        return False

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


HELPERS = """
window.confirm = () => true;
window.prompt = () => 'My test preset';
window.$$ = (s) => [...document.querySelectorAll(s)];
window.keyAt = (i) => document.querySelector('.key[data-i="' + i + '"]');
window.keyLabel = (i) => { const k = keyAt(i); const l = k && k.querySelector('.lbl'); return l ? l.textContent : ''; };
window.card = (name) => $$('.pcard').find((c) => c.querySelector('.nm').textContent === name);
window.btn = (text, scope) => [...(scope || document).querySelectorAll('button')].find((b) => b.textContent.trim() === text);
window.names = () => $$('.deskrow .name').map((n) => n.textContent);
window.menuItem = (name) => $$('.addmenu .item').find((i) => i.querySelector('.nm').textContent === name);
window.dragDesktop = (from, to, after) => {
  const boxes = $$('.desk');
  const dst = boxes[to], r = dst.getBoundingClientRect();
  const y = after ? r.bottom - 2 : r.top + 2;
  const dt = new DataTransfer();
  boxes[from].querySelector('.deskrow').dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
  dst.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true, clientY: y }));
  window.indicator = dst.className;
  dst.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true, clientY: y }));
};
window.dragSortRow = (from, to, after) => {
  const rows = $$('.srow:not(.off)');
  const dst = rows[to], r = dst.getBoundingClientRect(), y = after ? r.bottom - 2 : r.top + 2;
  const dt = new DataTransfer();
  rows[from].dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
  dst.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true, clientY: y }));
  dst.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true, clientY: y }));
};
window.mockItems = () => $$('.mock .mk').map((e) => e.dataset.item);
window.statRow = (id) => document.querySelector('.srow[data-item="' + id + '"]');
window.radio = (group, index) => $$('input[name="' + group + '"]')[index];
window.chips = () => $$('.appchip').map((c) => c.firstChild.textContent);
window.libRow = (name) => $$('.library .arow').find((r) => r.querySelector('.an').textContent.replace(/[“”]/g, '').startsWith(name));
window.libButton = (name, list) => libRow(name).querySelector('button[data-list="' + list + '"]');
window.listNames = (list) => $$('.applist[data-list="' + list + '"] .arow .an').map((e) => e.textContent);
window.dragApp = (from, to) => {
  const dt = new DataTransfer();
  from.dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
  to.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
  to.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
};
window.lookCheck = (text) => [...$$('#liveConfig .check')].find((l) => l.textContent.includes(text)).querySelector('input');
window.press = (key, opts = {}) => document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opts }));
window.setInput = (el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })); };
window.setSelect = (el, value) => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); };
window.drag = (from, to) => {
  const dt = new DataTransfer();
  from.dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
  to.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
  to.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
};
true;
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="directory for screenshots")
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    browser = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not browser:
        print("no Edge or Chrome found")
        return 2

    work = Path(tempfile.mkdtemp(prefix="editor_e2e_"))
    deck_path = work / "deck.json"
    shutil.copyfile(ROOT / "companion" / "tests" / "fixtures" / "deck.json", deck_path)
    players = [{"id": "Chrome", "title": "A video", "artist": "A channel", "playing": True, "current": True},
               {"id": "Spotify.exe", "title": "", "artist": "", "playing": False, "current": False}]
    board_commands: list = []  # what the editor asked the (pretend) companion to send to the board
    server = EditorServer(deck_path, port=0, status_fn=lambda: {"connected": False}, media_sources_fn=lambda: players,
                          command_fn=lambda c: board_commands.append(c) or True)
    server.start()
    port = free_port()
    proc = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--no-first-run", f"--remote-debugging-port={port}",
                             "--remote-allow-origins=*", f"--user-data-dir={work / 'profile'}", "--window-size=1700,1050",
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond, detail: str = "") -> None:
        results.append((name, bool(cond), detail))
        print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  [{detail}]"))

    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1))
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("browser did not start")
        cdp = CDP(next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"])
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", width=1700, height=1050, deviceScaleFactor=1, mobile=False)

        def open_editor():
            cdp.call("Page.navigate", url=server.url)
            assert cdp.wait("document.querySelectorAll('.key').length > 0", 10), "editor did not render"
            cdp.js(HELPERS)

        print("loading")
        open_editor()
        check("renders the 12 keys of the first page", cdp.js("$$('.key').length") == 12)
        check("starts saved (nothing to save)", cdp.js("btn('Saved') && btn('Saved').disabled"))
        check("preview shows the page title", cdp.js("document.querySelector('.hdr .title').textContent") == "Main")
        check("one dot per desktop", cdp.js("$$('.hdr .dot').length") == 5)
        check("presets loaded", cdp.js("$$('.pcard').length") > 60)

        print("editing a key")
        cdp.js("keyAt(0).click()")
        check("selecting a key shows it in the inspector", cdp.js("$$('#inspector h4').pop().textContent") == "Key 1")
        check("label field has the key's label", cdp.js("[...document.querySelectorAll('#inspector input[type=text]')].some((i) => i.value === 'Mute')"))
        cdp.js("setInput([...document.querySelectorAll('#inspector input[type=text]')].find((i) => i.value === 'Mute'), 'Silence')")
        check("typing updates the preview live", cdp.js("keyLabel(0)") == "Silence")
        check("unsaved changes enable Save", cdp.js("btn('Save') && !btn('Save').disabled"))
        cdp.js("press('z', { ctrlKey: true })")
        check("Ctrl+Z undoes the label", cdp.js("keyLabel(0)") == "Mute")
        check("...and the deck is clean again", cdp.js("btn('Saved') && btn('Saved').disabled"))
        cdp.js("press('y', { ctrlKey: true })")
        check("Ctrl+Y redoes it", cdp.js("keyLabel(0)") == "Silence")
        cdp.js("press('z', { ctrlKey: true })")

        print("presets")
        cdp.js("keyAt(8).click()")
        cdp.js("card('Copy').click()")
        check("clicking a preset fills the selected empty slot", cdp.js("keyLabel(8)") == "Copy")
        check("the action is a Ctrl+C hotkey", cdp.js("keyAt(8).title") == "press ctrl+c")
        check("the hotkey editor shows the result", cdp.js("[...document.querySelectorAll('#inspector code')].some((c) => c.textContent === 'ctrl+c')"))
        cdp.js("[...document.querySelectorAll('.mods label')].find((l) => l.textContent.trim() === 'Shift').querySelector('input').click()")
        check("ticking Shift composes ctrl+shift+c", cdp.js("keyAt(8).title") == "press ctrl+shift+c")
        if shots:
            cdp.shot(shots / "1_key_selected.png")
        cdp.js("press('z', { ctrlKey: true })")
        check("undo reverts the modifier", cdp.js("keyAt(8).title") == "press ctrl+c")
        cdp.js("setInput(document.querySelector('.phead input'), 'git')")
        check("searching filters the presets", 0 < cdp.js("$$('.pcard').length") < 12)
        cdp.js("setInput(document.querySelector('.phead input'), '')")
        cdp.js("btn('Streaming (F13-F24)').click()")
        check("category chips filter", cdp.js("$$('.pcard').length") == 8)
        cdp.js("btn('All').click()")

        print("drag and drop")
        cdp.js("drag(keyAt(8), keyAt(9))")
        check("dragging a key onto another swaps them", cdp.js("keyLabel(9)") == "Copy" and cdp.js("keyAt(8).classList.contains('empty')"))
        cdp.js("drag(card('Paste'), keyAt(8))")
        check("dragging a preset onto a slot places it", cdp.js("keyLabel(8)") == "Paste")

        print("folders and key types")
        before_pages = cdp.js("$$('.pagerow').length")
        cdp.js("keyAt(10).click()")
        cdp.js("card('Folder').click()")
        check("the Folder preset creates a new page", cdp.js("$$('.pagerow').length") == before_pages + 1)
        check("...and the key shows the folder arrow", cdp.js("!!keyAt(10).querySelector('.arrow')"))
        cdp.js("btn('Action', document.querySelector('#inspector')).click()")
        check("switching a folder key to Action gives it an action", cdp.js("keyAt(10).title.startsWith('press')"))
        cdp.js("btn('Empty', document.querySelector('#inspector')).click()")
        check("switching to Empty clears the slot", cdp.js("keyAt(10).classList.contains('empty')"))

        print("grid size")
        cdp.js("btn('+', document.querySelectorAll('.stepper')[2]).click()")
        check("adding a row adds 4 slots", cdp.js("$$('.key').length") == 16)
        cdp.js("press('z', { ctrlKey: true })")
        check("undo restores the grid", cdp.js("$$('.key').length") == 12)

        print("other desktops, multi-step action")
        cdp.js("$$('.pagerow').find((r) => r.querySelector('.id').textContent === 'git').click()")
        cdp.js("keyAt(2).click()")
        check("a multi action shows its steps", cdp.js("$$('.step').length") == 2)
        if shots:
            cdp.shot(shots / "2_multi_action.png")
        cdp.js("btn('+ Add step').click()")
        check("steps can be added", cdp.js("$$('.step').length") == 3)
        cdp.js("press('z', { ctrlKey: true })")

        cdp.js("$$('.deskrow').find((r) => r.textContent.includes('Playing')).click()")
        check("Now playing desktop shows its preview", cdp.js("!!document.querySelector('.mock') && document.querySelector('.mock').textContent.includes('Song title')"))
        check("...with no key grid", cdp.js("$$('.key').length") == 0)
        if shots:
            cdp.shot(shots / "3_now_playing.png")
        cdp.js("$$('.deskrow').find((r) => r.textContent.includes('PC')).click()")
        check("PC stats desktop shows its preview", cdp.js("document.querySelector('.mock').textContent.includes('CPU')"))
        if shots:
            cdp.shot(shots / "4_stats.png")

        print("reordering desktops by dragging")
        base = ["Main", "Playing", "PC", "Work", "Stream"]
        check("starting order", cdp.js("names()") == base)
        cdp.js("dragDesktop(3, 0, false)")
        check("dropping on the upper half puts it before", cdp.js("names()") == ["Work", "Main", "Playing", "PC", "Stream"])
        check("a drop indicator was shown while hovering", "drop-before" in cdp.js("window.indicator"))
        check("the moved desktop is the selected one", cdp.js("$$('.desk').findIndex((d) => d.classList.contains('active'))") == 0)
        cdp.js("dragDesktop(0, 4, true)")
        check("dropping on the lower half of the last one moves it to the end", cdp.js("names()") == ["Main", "Playing", "PC", "Stream", "Work"])
        check("the lower-half indicator was shown", "drop-after" in cdp.js("window.indicator"))
        cdp.js("dragDesktop(1, 1, false)")
        check("dropping on itself changes nothing", cdp.js("names()") == ["Main", "Playing", "PC", "Stream", "Work"])
        cdp.js("press('z', { ctrlKey: true })")
        cdp.js("press('z', { ctrlKey: true })")
        check("undo puts the order back", cdp.js("names()") == base)
        cdp.js("drag(keyAt(0), keyAt(1))")  # a key drag must not be mistaken for a desktop drag
        check("key drags and desktop drags do not interfere", cdp.js("names()") == base)
        cdp.js("press('z', { ctrlKey: true })")

        print("add-desktop menu")
        templates = json.loads((ROOT / "companion" / "streamdeck_companion" / "web" / "templates.json").read_text(encoding="utf-8"))["templates"]
        check("the old standalone add buttons are gone", cdp.js("!btn('+ Now playing') && !btn('+ PC stats') && !btn('+ Keys desktop')"))
        cdp.js("btn('+ Add desktop').click()")
        check("the menu lists live screens, ready-made desktops and a blank one", cdp.js("$$('.addmenu .item').length") == 2 + len(templates) + 1)
        check("live screens already in the deck are disabled", cdp.js("menuItem('Now playing').disabled && menuItem('PC stats').disabled"))
        check("every ready-made desktop is offered", all(cdp.js(f"!!menuItem({json.dumps(t['name'])})") for t in templates))
        cdp.js("press('Escape')")
        check("Escape closes the menu", cdp.js("!document.querySelector('.addmenu')"))
        cdp.js("btn('+ Add desktop').click()")
        cdp.js("menuItem('Discord').click()")
        check("adding a ready-made desktop appends it", cdp.js("names()")[-1] == "Discord" and cdp.js("$$('.desk').length") == 6)
        check("the menu closed and the new desktop is shown with its keys", cdp.js("!document.querySelector('.addmenu') && $$('.key').length") == 12 and cdp.js("keyLabel(0)") == "Mute")
        cdp.js("btn('+ Add desktop').click()")
        cdp.js("menuItem('Discord').click()")
        check("adding the same template again gives it a unique name", cdp.js("names()")[-1] == "Discord 2")
        cdp.js("btn('+ Add desktop').click()")
        cdp.js("menuItem('Coding').click()")
        cdp.js("$$('.desk.active .pagerow').find((r) => r.querySelector('.id').textContent === 'git').click()")
        check("a template with several pages brings its folders", cdp.js("$$('.key').length") == 6 and cdp.js("keyLabel(0)") == "Status")
        if shots:
            cdp.shot(shots / "6_template_coding.png")
        for _ in range(3):
            cdp.js("$$('.desk')[5].querySelector('button.danger').click()")
        check("the added desktops can be deleted again", cdp.js("names()") == base)
        cdp.js("btn('+ Add desktop').click()")
        cdp.js("menuItem('Keys desktop').click()")
        check("a blank keys desktop", cdp.js("$$('.desk').length") == 6 and cdp.js("$$('.key.empty').length") == 12)
        cdp.js("$$('.desk')[5].querySelector('button.danger').click()")
        for kind in ("Playing", "PC"):  # live screens can be re-added once removed
            cdp.js(f"$$('.desk').find((d) => d.querySelector('.name').textContent === '{kind}').querySelector('button.danger').click()")
        check("live screens removed", cdp.js("names()") == ["Main", "Work", "Stream"])
        cdp.js("btn('+ Add desktop').click()")
        check("...and now enabled in the menu", cdp.js("!menuItem('Now playing').disabled && !menuItem('PC stats').disabled"))
        if shots:
            cdp.shot(shots / "7_add_menu.png")
        cdp.js("menuItem('Now playing').click()")
        cdp.js("btn('+ Add desktop').click()")
        cdp.js("menuItem('PC stats').click()")
        check("live screens added back from the menu", cdp.js("names()") == ["Main", "Work", "Stream", "Playing", "PC"])
        cdp.js("$$('.deskrow')[0].click()")

        print("live screen settings: PC stats")
        cdp.js("$$('.deskrow').find((r) => r.textContent.includes('PC')).click()")
        check("the presets area is replaced by settings on a live screen", cdp.js("document.querySelector('#presets').children.length") == 0 and cdp.js("$$('#liveConfig .card').length") == 2)
        check("three styles and six items are offered", cdp.js("$$('#liveConfig .option').length") == 3 and cdp.js("$$('.srow').length") == 6)
        check("the preview starts in the gauges style with all six items", cdp.js("document.querySelector('.mock').dataset.style") == "gauges" and cdp.js("mockItems()") == ["cpu", "gpu", "ram", "vram", "disk", "net"])
        cdp.js("document.querySelector('.option[data-style=\"tiles\"]').click()")
        check("choosing the tiles style redraws the preview", cdp.js("document.querySelector('.mock').dataset.style") == "tiles" and cdp.js("$$('.mock .mk').length") == 6)
        if shots:
            cdp.shot(shots / "8_stats_tiles.png")
        cdp.js("statRow('net').querySelector('input').click()")
        check("hiding an item removes it from the preview", cdp.js("mockItems()") == ["cpu", "gpu", "ram", "vram", "disk"])
        check("...and it moves to the unticked rows", cdp.js("statRow('net').classList.contains('off')"))
        cdp.js("dragSortRow(0, 2, true)")
        check("dragging an item reorders the screen", cdp.js("mockItems()") == ["gpu", "ram", "cpu", "vram", "disk"])
        cdp.js("statRow('gpu').querySelectorAll('button')[1].click()")
        check("the arrow buttons reorder too", cdp.js("mockItems()") == ["ram", "gpu", "cpu", "vram", "disk"])
        for item in ("ram", "cpu", "vram", "disk"):
            cdp.js(f"statRow('{item}').querySelector('input').click()")
        check("the last item cannot be hidden", cdp.js("mockItems()") == ["gpu"] and cdp.js("statRow('gpu').querySelector('input').disabled"))
        for item in ("net", "disk", "vram", "cpu", "ram"):
            cdp.js(f"statRow('{item}').querySelector('input').click()")
        check("hidden items come back at the end", cdp.js("mockItems()") == ["gpu", "net", "disk", "vram", "cpu", "ram"])
        cdp.js("document.querySelector('.option[data-style=\"bars\"]').click()")
        check("the bars style", cdp.js("document.querySelector('.mock').dataset.style") == "bars" and cdp.js("$$('.mock .mk').length") == 6)
        if shots:
            cdp.shot(shots / "9_stats_bars.png")
        cdp.js("document.querySelector('.option[data-style=\"gauges\"]').click()")

        print("live screen settings: Now playing - players")
        cdp.js("$$('.deskrow').find((r) => r.textContent.includes('Playing')).click()")
        check("the player lists are always visible, side by side with the library", cdp.js("$$('.applist').length") == 2 and cdp.js("!!document.querySelector('.library')"))
        check("the lists say what empty means", "any player can be shown" in cdp.js("document.querySelector('.applist[data-list=allow]').textContent") and "no player is blocked" in cdp.js("document.querySelector('.applist[data-list=block]').textContent"))
        check("the summary describes an empty filter", cdp.js("document.querySelector('#filterSummary').textContent") == "Any player can be shown.")
        check("running players are detected on their own", cdp.wait("document.querySelectorAll('.libsec').length > 0 && [...$$('.libsec')].some((e) => e.textContent === 'Running now')"))
        check("...listed first, with the playing tag and the known app's name", cdp.js("$$('.arow')[0].textContent.includes('Chrome') && $$('.arow')[0].textContent.includes('playing')"))
        check("the library has the catalog sections", cdp.js("$$('.libsec').map((e) => e.textContent)") == ["Running now", "Music apps", "Browsers", "Video players"])
        check("catalog apps show logos or letter badges", cdp.js("$$('.library .aico.brand').length") >= 8 and cdp.js("$$('.library .aico.badge').length") >= 8)
        check("a letter badge shows the app's initial on its color", cdp.js("$$('.library .arow').find((r) => r.querySelector('.an').textContent.startsWith('TIDAL')).querySelector('.aico').textContent") == "T")
        check("the Spotify logo is the Spotify glyph", cdp.js("$$('.library .arow').find((r) => r.querySelector('.an').textContent.startsWith('Spotify')).querySelector('.aico .fa').textContent.codePointAt(0)") == 0xF1BC)
        if shots:
            cdp.shot(shots / "12_players_empty.png")

        cdp.js("libButton('Spotify', 'allow').click()")
        check("'Only' puts a player on the first list, with its icon", cdp.js("listNames('allow')") == ["Spotify"] and cdp.js("!!document.querySelector('.applist[data-list=allow] .aico.brand')"))
        check("...and the library row shows it as selected", cdp.js("libButton('Spotify', 'allow').classList.contains('on')"))
        cdp.js("libButton('Chrome', 'block').click()")
        check("'Never' puts a player on the second list", cdp.js("listNames('block')") == ["Chrome"])
        check("the summary describes both lists", "never those under" in cdp.js("document.querySelector('#filterSummary').textContent"))
        cdp.js("libButton('Spotify', 'block').click()")
        check("a player is on one list at a time (moving)", cdp.js("listNames('allow')") == [] and cdp.js("listNames('block')") == ["Chrome", "Spotify"])
        cdp.js("libButton('Spotify', 'block').click()")
        check("clicking a selected button removes the player", cdp.js("listNames('block')") == ["Chrome"])
        cdp.js("dragApp(libRow('TIDAL'), document.querySelector('.applist[data-list=allow]'))")
        check("dragging a library row into a list adds it", cdp.js("listNames('allow')") == ["TIDAL"])
        cdp.js("dragApp(document.querySelector('.applist[data-list=block] .arow'), document.querySelector('.applist[data-list=allow]'))")
        check("dragging between lists moves a player", cdp.js("listNames('allow')") == ["TIDAL", "Chrome"] and cdp.js("listNames('block')") == [])
        cdp.js("setInput(document.querySelector('#appSearch'), 'vlc')")
        check("the search narrows the library", cdp.js("$$('.library .arow').length") == 1 and cdp.js("$$('.library .arow')[0].textContent.includes('VLC')"))
        cdp.js("setInput(document.querySelector('#appSearch'), 'my player')")
        check("a custom name gets its own row with a placeholder icon", cdp.js("$$('.library .arow').length") == 1 and cdp.js("!!document.querySelector('.library .aico.placeholder')") and cdp.js("document.querySelector('.library .libsec').textContent") == "Custom name")
        cdp.js("libButton('my player', 'block').click()")
        check("a custom name can be added to a list and keeps the placeholder icon", cdp.js("listNames('block')") == ["my player"] and cdp.js("!!document.querySelector('.applist[data-list=block] .aico.placeholder')") and "custom name" in cdp.js("document.querySelector('.applist[data-list=block]').textContent"))
        cdp.js("setInput(document.querySelector('#appSearch'), '')")
        check("clearing the search shows the library again", cdp.js("$$('.library .arow').length") > 20)
        cdp.js("document.querySelector('.applist[data-list=block] .x').click()")
        check("the remove button takes a player off its list", cdp.js("listNames('block')") == [])
        cdp.js("libButton('Firefox', 'block').click()")
        check("a player with two names is one entry", cdp.js("listNames('block')") == ["Firefox"] and "firefox, 308046B0AF4A39CB" in cdp.js("document.querySelector('.applist[data-list=block]').textContent"))
        cdp.js("libButton('Firefox', 'block').click()")
        cdp.js("libButton('Chrome', 'block').click()")
        check("no stray 'null' text in the settings", "null" not in cdp.js("document.querySelector('#liveConfig').textContent"))
        cdp.js("btn('Refresh running players').click()")
        check("the running players can be refreshed", cdp.wait("!btn('Looking\u2026') && document.querySelectorAll('.arow').length > 20"))
        if shots:
            cdp.shot(shots / "13_players_lists.png")

        print("live screen settings: Now playing - look")
        check("the preview starts with the album line and full controls", cdp.js("!!document.querySelector('.m-album')") and cdp.js("$$('.mock > .fa').length") == 5)
        cdp.js("[...$$('#liveConfig .check')].find((l) => l.textContent.includes('album')).querySelector('input').click()")
        check("hiding the album line", cdp.js("!document.querySelector('.m-album')") and cdp.js("document.querySelector('.mock').dataset.album") == "false")
        cdp.js("radio('npctl', 1).click()")
        check("basic controls show three icons", cdp.js("$$('.mock > .fa').length") == 3)
        before = cdp.js("getComputedStyle(document.querySelector('.mock')).backgroundColor")
        cdp.js("[...$$('#liveConfig .check')].find((l) => l.textContent.includes('Tint')).querySelector('input').click()")
        check("turning the tint off changes the preview background", cdp.js("getComputedStyle(document.querySelector('.mock')).backgroundColor") != before and cdp.js("document.querySelector('.mock').dataset.tint") == "false")
        if shots:
            cdp.shot(shots / "10_now_playing_settings.png")
        cdp.js("btn('Undo').click()")
        check("undo works for settings", cdp.js("document.querySelector('.mock').dataset.tint") == "true")
        cdp.js("btn('Redo').click()")
        check("the app icon option is off by default (nothing in the preview)", cdp.js("!lookCheck('icon of the app').checked") and cdp.js("!document.querySelector('.m-appicon')") and cdp.js("document.querySelector('.mock').dataset.appicon") == "false")
        cdp.js("lookCheck('icon of the app').click()")
        check("turning it on puts an icon in the preview, at the end of the time row", cdp.js("!!document.querySelector('.m-appicon')") and cdp.js("document.querySelector('.mock').dataset.appicon") == "true")
        if shots:
            cdp.shot(shots / "14_app_icon_option.png")

        print("saving")
        cdp.js("$$('.deskrow')[0].click()")  # back to the first keys desktop
        print("general settings page")
        check("the board settings are no longer at the bottom of the desktop list", cdp.js("!document.body.textContent.includes('Board settings')"))
        check("the editor starts on the desktops view", cdp.js("!document.querySelector('#deckView').hidden && document.querySelector('#settingsPage').hidden"))
        cdp.js("btn('General settings').click()")
        check("the General settings tab replaces the editor", cdp.js("document.querySelector('#deckView').hidden && !document.querySelector('#settingsPage').hidden"))
        check("the tab shows which view is active", cdp.js("btn('General settings').classList.contains('on') && !btn('Desktops').classList.contains('on')"))
        check("sound, screen, start, key look and deck cards", cdp.js("$$('#settingsPage .card h4').map((h) => h.textContent)") == ["Sound", "Screen", "Start", "Key look", "This deck"])
        check("the deck card shows the number of desktops", cdp.js("document.querySelector('#settingsPage').textContent.includes('Desktops5')"))
        cdp.js("setInput(document.querySelector('#setVolume'), '40')")
        check("the volume label follows the slider", cdp.js("document.querySelector('#setVolume').closest('.field').querySelector('b').textContent") == "40")
        cdp.js("setInput(document.querySelector('#setBrightness'), '120')")
        cdp.js("$$('#settingsPage .check input')[0].click()")
        check("changing a setting makes the deck unsaved", cdp.js("btn('Save') && !btn('Save').disabled"))
        cdp.js("press('z', { ctrlKey: true })")
        check("undo works on the settings page", cdp.js("document.querySelector('#settingsPage .check input').checked") is True)
        cdp.js("$$('#settingsPage .check input')[0].click()")  # sounds off again for the saved-file check
        def wait_py(cond, timeout=4.0):
            end = time.time() + timeout
            while time.time() < end:
                if cond():
                    return True
                time.sleep(0.05)
            return False

        # sound theme, tests, screen power, start desktop, key look
        check("the classic theme is selected by default", cdp.js("document.querySelector('#themeSeg .on').dataset.theme") == "classic")
        board_commands.clear()
        cdp.js("document.querySelector('#themeSeg [data-theme=soft]').click()")
        check("choosing a theme selects it and makes the deck unsaved", cdp.js("document.querySelector('#themeSeg .on').dataset.theme") == "soft")
        check("the change reaches the board as a live preview", wait_py(lambda: any(c.get("kind") == "settings" and c["settings"]["soundTheme"] == "soft" for c in board_commands)), str(board_commands[-1:]))
        # key sounds are off here (switched off above for the saved-file check): the Test buttons must be disabled
        check("with key sounds off the Test buttons are disabled", cdp.js("[...document.querySelectorAll('#testRow button')].every((b) => b.disabled)"))
        cdp.js("$$('#settingsPage .check input')[0].click()")  # key sounds on
        check("...and enabled with them on", cdp.js("[...document.querySelectorAll('#testRow button')].every((b) => !b.disabled)"))
        cdp.js("document.querySelector('#testRow [data-sound=nav]').click()")
        check("a Test button asks the board to play that sound", wait_py(lambda: {"kind": "sound", "name": "nav"} in board_commands))
        theme_at = max((i for i, c in enumerate(board_commands) if c.get("kind") == "settings" and c["settings"]["soundTheme"] == "soft"), default=-1)
        sound_at = max((i for i, c in enumerate(board_commands) if c == {"kind": "sound", "name": "nav"}), default=-1)
        check("the chosen theme reaches the board before the test sound", 0 <= theme_at < sound_at, str(board_commands))
        cdp.js("$$('#settingsPage .check input')[0].click()")  # key sounds off again for the saved-file check
        cdp.js("setSelect(document.querySelector('#setDim'), '30')")
        cdp.js("setSelect(document.querySelector('#setSleep'), '60')")
        check("dim and sleep choices are kept", cdp.js("document.querySelector('#setDim').value") == "30" and cdp.js("document.querySelector('#setSleep').value") == "60")
        check("no warning when the screen dims before it sleeps", cdp.js("!document.querySelector('#dimClash')"))
        cdp.js("setSelect(document.querySelector('#setSleep'), '60')")
        cdp.js("setSelect(document.querySelector('#setDim'), '120')")
        check("dimming after the screen turns off is flagged", cdp.js("!!document.querySelector('#dimClash')"))
        cdp.js("setSelect(document.querySelector('#setDim'), '30')")
        cdp.js("$$('#settingsPage .check input')[2].click()")  # flip
        check("flip is stored", cdp.js("$$('#settingsPage .check input')[2].checked") is True)
        cdp.js("$$('#settingsPage .check input')[2].click()")  # and back
        cdp.js("setSelect(document.querySelector('#setStart'), '1')")
        check("the start desktop is stored", cdp.js("document.querySelector('#setStart').value") == "1")
        cdp.js("setSelect(document.querySelector('#setStart'), '0')")
        before = cdp.js("document.querySelector('#lookSample .sampleKey').style.borderRadius")
        left_before = cdp.js("document.querySelectorAll('#lookSample .sampleKey')[1].style.left")
        cdp.js("setInput(document.querySelector('#setRadius'), '3')")
        cdp.js("setInput(document.querySelector('#setGap'), '10')")
        check("the key look sliders redraw the sample", cdp.js("document.querySelector('#lookSample .sampleKey').style.borderRadius") != before)
        check("the sample follows the gap", cdp.js("document.querySelectorAll('#lookSample .sampleKey')[1].style.left") != left_before and cdp.js("document.querySelector('#lookSample').children.length") == 12)
        check("a radius label follows the slider", cdp.js("document.querySelector('#setRadius').closest('.field').querySelector('b').textContent") == "3 px")
        if shots:
            cdp.shot(shots / "11_general_settings.png")
        cdp.js("btn('Desktops').click()")
        check("the desktop preview uses the key look (radius 3 px, shown at 2x)", cdp.js("document.querySelector('.key').style.borderRadius") == "6px")
        cdp.js("btn('General settings').click()")
        cdp.js("setInput(document.querySelector('#setRadius'), '12')")
        cdp.js("setInput(document.querySelector('#setGap'), '6')")
        cdp.js("document.querySelector('#themeSeg [data-theme=classic]').click()")
        cdp.js("btn('Desktops').click()")
        check("the Desktops tab brings the editor back", cdp.js("!document.querySelector('#deckView').hidden && document.querySelector('#settingsPage').hidden"))
        cdp.js("keyAt(11).click()")
        cdp.js("card('Mute').click()")
        cdp.js("btn('Save').click()")
        check("Save succeeds", cdp.wait("document.querySelector('#toast') && !document.querySelector('#toast').hidden && document.querySelector('#toast').textContent.startsWith('Saved')"))
        check("...and the button reads Saved again", cdp.js("btn('Saved') && btn('Saved').disabled"))
        saved = json.loads(deck_path.read_text(encoding="utf-8"))
        check("the file has the new key", any(k.get("label") == "Mute" for k in saved["desktops"][0]["pages"][0]["keys"][8:]))
        check("the file has the volume, brightness and sounds settings", saved["settings"]["volume"] == 40 and saved["settings"]["brightness"] == 120 and saved["settings"]["sounds"] is False, str(saved["settings"]))
        check("the file has the new settings too", saved["settings"]["dimAfter"] == 30 and saved["settings"]["sleepAfter"] == 60
              and saved["settings"]["soundTheme"] == "classic" and saved["settings"]["keyRadius"] == 12 and saved["settings"]["keyGap"] == 6, str(saved["settings"]))
        check("a saved deck with these settings loads in the companion", Deck(saved).effective_settings()["dimAfter"] == 30)
        stats_options = next(d for d in saved["desktops"] if d.get("kind") == "stats")["options"]
        np_options = next(d for d in saved["desktops"] if d.get("kind") == "nowplaying")["options"]
        check("the saved file has the stats style and items", stats_options["style"] == "gauges" and stats_options["items"] == ["gpu", "net", "disk", "vram", "cpu", "ram"], str(stats_options))
        check("the saved file turns the app icon on (it is off unless chosen)", np_options.get("appIcon") is True, str(np_options))
        check("the saved file has the player lists and the look", np_options["filter"] == {"allow": ["tidal"], "block": ["chrome"]}
              and np_options["album"] is False and np_options["controls"] == "basic" and np_options["tint"] is False, str(np_options))
        loaded = Deck(saved)
        check("the saved deck loads in the companion", loaded is not None)
        check("the player filter reaches the companion but not the board", loaded.np_filter == {"allow": ["tidal"], "block": ["chrome"]} and "Chrome" not in loaded.layout_text())
        check("a backup of the previous deck was written", (work / "deck.json.bak").exists())
        check("saved text is readable (one key per line)", max(len(l) for l in deck_path.read_text(encoding="utf-8").splitlines()) < 400)

        print("user presets")
        cdp.js("keyAt(8).click()")
        cdp.js("btn('Save as preset').click()")
        check("saving a key as a preset", cdp.wait("!!btn('My presets')"))
        check("the preset file was written", (work / "presets.user.json").exists() and "My test preset" in (work / "presets.user.json").read_text(encoding="utf-8"))

        print("reload")
        open_editor()
        check("reloading shows the saved deck, nothing unsaved", cdp.js("btn('Saved') && btn('Saved').disabled") and cdp.js("keyLabel(11)") == "Mute")
        check("user presets come back", cdp.js("!!btn('My presets')"))
        if shots:
            cdp.shot(shots / "5_after_reload.png")

        check("no JavaScript errors during the whole session", not cdp.problems, "; ".join(cdp.problems[:3]))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
