"""Takes the pictures used in the README (docs/images) from the web editor, in headless Edge or Chrome.

    pip install websocket-client          (anywhere; it is not a dependency of the companion)
    python tools/screenshots.py [--out docs/images]

The editor is served on a temporary COPY of the demo deck in companion/tests/fixtures (your real deck is never touched).
The screen pictures are the editor's preview of what the board shows, not photos of the board.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_editor as e2e  # noqa: E402  (its CDP driver, browser list and page helpers)
from e2e_editor import CDP, ROOT, EditorServer, free_port  # noqa: E402

WIDTH, HEIGHT = 1500, 900


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "images"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    browser = next((b for b in e2e.BROWSERS if os.path.exists(b)), None)
    if not browser:
        print("no Edge or Chrome found")
        return 2

    work = Path(tempfile.mkdtemp(prefix="editor_shots_"))
    deck_path = work / "deck.json"
    shutil.copyfile(ROOT / "companion" / "tests" / "fixtures" / "deck.json", deck_path)
    players = [{"id": "Chrome", "title": "A video", "artist": "A channel", "playing": True, "current": True},
               {"id": "Spotify.exe", "title": "", "artist": "", "playing": False, "current": False}]
    server = EditorServer(deck_path, port=0, status_fn=lambda: {"connected": True, "port": "COM3", "firmware": "0.13.0",
                                                                "desktop": 0, "kind": "keys"},
                          media_sources_fn=lambda: players, command_fn=lambda c: True,
                          places_fn=lambda q: [{"name": "London", "region": "England", "country": "United Kingdom", "lat": 51.5074, "lon": -0.1278}])
    server.start()
    port = free_port()
    proc = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--no-first-run", f"--remote-debugging-port={port}",
                             "--remote-allow-origins=*", f"--user-data-dir={work / 'profile'}",
                             f"--window-size={WIDTH},{HEIGHT}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
        cdp.call("Page.navigate", url=server.url)
        assert cdp.wait("document.querySelectorAll('.key').length > 0", 10), "editor did not render"
        cdp.js(e2e.HELPERS)
        # the selection outline, hover effects and the dashed outline of empty slots are not part of what the board shows
        cdp.js("document.head.append(Object.assign(document.createElement('style'), "
               "{id: 'shotStyle', textContent: '.key.sel{outline:none !important} .key:hover{filter:none !important} "
               ".key.empty{border-color:transparent !important} .hdr .title .ui-ico{display:none}'}))")

        def page_shot(name: str) -> None:
            data = cdp.call("Page.captureScreenshot", format="png")["data"]
            (out / name).write_bytes(base64.b64decode(data))
            print("  ", name)

        def screen_shot(name: str) -> None:
            r = json.loads(cdp.js("JSON.stringify(document.querySelector('.screen').getBoundingClientRect())"))
            data = cdp.call("Page.captureScreenshot", format="png", clip={"x": r["x"], "y": r["y"], "width": r["width"],
                                                                         "height": r["height"], "scale": 1})["data"]
            (out / name).write_bytes(base64.b64decode(data))
            print("  ", name)

        def desktop(i: int) -> None:
            cdp.js(f"$$('.deskrow')[{i}].click()")
            time.sleep(0.3)

        print("desktops:", cdp.js("names()"))  # (names only: the icon glyphs cannot be printed to every console)

        print("screens")
        # tall enough for the board to be drawn at its full 2x in the screen pictures
        cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=1100, deviceScaleFactor=1, mobile=False)
        desktop(0)
        screen_shot("screen-keys.png")
        # a folder page: the second page of the first desktop (the tree lists pages under the desktop)
        if cdp.js("$$('.pagerow').length") > 1:
            cdp.js("$$('.pagerow')[1].click()")
            time.sleep(0.3)
            screen_shot("screen-folder.png")
            cdp.js("$$('.pagerow')[0].click()")
        desktop(1)
        screen_shot("screen-nowplaying.png")
        desktop(2)
        screen_shot("screen-stats.png")

        print("editor views")
        cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        cdp.js("document.querySelector('#shotStyle').textContent = '.key.sel{outline:none !important} .key:hover{filter:none !important}'")
        desktop(0)
        cdp.js("keyAt(1).click()")  # select a key so the inspector shows
        time.sleep(0.3)
        page_shot("editor.png")
        desktop(1)
        time.sleep(0.5)
        page_shot("editor-nowplaying.png")
        desktop(0)
        cdp.js("btn('Settings').click()")
        time.sleep(0.5)
        cdp.js("window.scrollTo(0, 0)")
        page_shot("settings.png")

        # the two newer screens are added last, so the dots in the pictures above are not changed by them
        print("mixer and clock")
        cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=1100, deviceScaleFactor=1, mobile=False)
        cdp.js("btn('Desktops').click()")
        for name in ("Volume mixer", "Clock"):
            cdp.js("btn('+ Add desktop').click()")
            cdp.js(f"[...document.querySelectorAll('.addmenu .item')].find((b) => b.textContent.includes('{name}')).click()")
            time.sleep(0.3)
            if name == "Clock":
                cdp.js("document.querySelector('#placeQuery').value = 'London'; document.querySelector('#placeSearch').click()")
                cdp.wait("document.querySelectorAll('#placeResults button').length > 0")
                cdp.js("document.querySelector('#placeResults button').click()")
                cdp.js("[...document.querySelectorAll('#liveConfig .check')].find((l) => l.textContent.includes('Seconds')).querySelector('input').click()")
                time.sleep(0.3)
            screen_shot("screen-mixer.png" if name == "Volume mixer" else "screen-clock.png")
    finally:
        proc.terminate()
        server.stop()
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
