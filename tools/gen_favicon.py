"""Draws the editor's favicon: companion/streamdeck_companion/web/favicon.svg and favicon.ico.

    companion\\.venv\\Scripts\\python tools\\gen_favicon.py

A dark rounded square with four colored keys (the colors of the deck's key presets). The SVG is the source of truth;
the .ico (16/32/48/64 px, for browsers without SVG favicon support and for /favicon.ico requests) is drawn from the same
numbers with Pillow.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parent.parent / "companion" / "streamdeck_companion" / "web"

BG = "#111a26"
KEYS = [(9, 9, "#b3261e"), (34, 9, "#2f6fdb"), (9, 34, "#1f6f5c"), (34, 34, "#5b3a8c")]  # x, y, color on a 64 grid
SIZE, KEY, RADIUS_BG, RADIUS_KEY = 64, 21, 14, 6


def svg() -> str:
    keys = "".join(f'<rect x="{x}" y="{y}" width="{KEY}" height="{KEY}" rx="{RADIUS_KEY}" fill="{c}"/>' for x, y, c in KEYS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">'
            f'<rect width="{SIZE}" height="{SIZE}" rx="{RADIUS_BG}" fill="{BG}"/>{keys}</svg>\n')


def raster(px: int) -> Image.Image:
    ss = 8
    big = Image.new("RGBA", (SIZE * ss, SIZE * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, SIZE * ss - 1, SIZE * ss - 1), radius=RADIUS_BG * ss, fill=BG)
    for x, y, c in KEYS:
        d.rounded_rectangle((x * ss, y * ss, (x + KEY) * ss - 1, (y + KEY) * ss - 1), radius=RADIUS_KEY * ss, fill=c)
    return big.resize((px, px), Image.LANCZOS)


def main() -> None:
    (WEB / "favicon.svg").write_text(svg(), encoding="utf-8", newline="\n")
    raster(64).save(WEB / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("wrote", WEB / "favicon.svg", "and", WEB / "favicon.ico")


if __name__ == "__main__":
    main()
