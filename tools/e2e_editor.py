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
window.press = (key, opts = {}) => document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opts }));
window.setInput = (el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })); };
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
    server = EditorServer(deck_path, port=0, status_fn=lambda: {"connected": False})
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

        print("saving")
        cdp.js("setInput(document.querySelector('#treeSettings input[type=range]'), '40')")
        cdp.js("keyAt(11).click()")
        cdp.js("card('Mute').click()")
        cdp.js("btn('Save').click()")
        check("Save succeeds", cdp.wait("document.querySelector('#toast') && !document.querySelector('#toast').hidden && document.querySelector('#toast').textContent.startsWith('Saved')"))
        check("...and the button reads Saved again", cdp.js("btn('Saved') && btn('Saved').disabled"))
        saved = json.loads(deck_path.read_text(encoding="utf-8"))
        check("the file has the new key", any(k.get("label") == "Mute" for k in saved["desktops"][0]["pages"][0]["keys"][8:]))
        check("the file has the volume setting", saved["settings"]["volume"] == 40)
        check("the saved deck loads in the companion", Deck(saved) is not None)
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
