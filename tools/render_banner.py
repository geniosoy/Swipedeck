"""Renders assets/banner.html to docs/images/banner.png (1200 x 627 at 1.5x = 1800 x 940) with headless Edge or Chrome.

    python tools/render_banner.py

The screens in it are the pictures in docs/images (see tools/screenshots.py). No third-party packages needed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def main() -> int:
    browser = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not browser:
        print("no Edge or Chrome found")
        return 2
    src = (ROOT / "assets" / "banner.html").resolve().as_uri()
    out = ROOT / "docs" / "images" / "banner.png"
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1.5",
                    "--window-size=1200,627", "--virtual-time-budget=3000", f"--screenshot={out}", src],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
