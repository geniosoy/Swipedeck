"""Generates src/icons.cpp: the transport icons of the Now playing screen as 8-bit alpha bitmaps (32x32).

    companion\\.venv\\Scripts\\python tools\\gen_icons.py [--preview preview.png]

Each icon is drawn here with plain geometry (solid, rounded, Spotify-like style), supersampled for smooth edges.
To use your own artwork instead, put an SVG or PNG named like the icon (play, pause, prev, next, shuffle,
repeat) in tools/icons/. SVG: the filled shape of its <path> elements is used (even-odd fill, so holes and the
gap where the shuffle arrows cross come out right). PNG: its alpha channel (or, without alpha, its brightness)
is the shape. If there is a prev icon but no next icon, next is prev mirrored. Only use artwork you may use.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

SIZE = 32          # final icon size in pixels (all icons share it, so all buttons look the same size)
SS = 8             # supersampling factor
ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "src" / "icons.cpp"
OVERRIDES = ROOT / "icons"


def canvas():
    im = Image.new("L", (SIZE * SS, SIZE * SS), 0)
    return im, ImageDraw.Draw(im)


def s(points):
    return [(x * SS, y * SS) for x, y in points]


def rounded_poly(d, points, r=1.6):
    """Filled polygon with rounded corners (fill + a round-jointed outline of width 2r)."""
    pts = s(points)
    d.polygon(pts, fill=255)
    d.line(pts + [pts[0]], fill=255, width=int(2 * r * SS), joint="curve")
    for x, y in pts:
        d.ellipse((x - r * SS, y - r * SS, x + r * SS, y + r * SS), fill=255)


def bar(d, x0, y0, x1, y1, r=1.6):
    d.rounded_rectangle((x0 * SS, y0 * SS, x1 * SS, y1 * SS), radius=r * SS, fill=255)


def stroke(d, points, w=2.8):
    pts = s(points)
    d.line(pts, fill=255, width=int(w * SS), joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - w * SS / 2, y - w * SS / 2, x + w * SS / 2, y + w * SS / 2), fill=255)


def play(d):
    rounded_poly(d, [(9, 6), (9, 26), (26, 16)], 1.8)


def pause(d):
    bar(d, 8, 5, 13.5, 27, 2.0)
    bar(d, 18.5, 5, 24, 27, 2.0)


def nxt(d):
    rounded_poly(d, [(6, 6), (6, 26), (21, 16)], 1.6)
    bar(d, 23, 5.5, 27.5, 26.5, 1.8)


def prev(d):
    rounded_poly(d, [(26, 6), (26, 26), (11, 16)], 1.6)
    bar(d, 4.5, 5.5, 9, 26.5, 1.8)


def shuffle(d):
    # two crossing arrows; the one passing "under" gets a small gap where the other crosses it
    stroke(d, [(3, 22), (8, 22), (18, 10), (22, 10)])
    stroke(d, [(3, 10), (8, 10), (12.4, 15.3)])
    stroke(d, [(16.2, 19.8), (18, 22), (22, 22)])
    d.polygon(s([(21, 5.5), (29, 10), (21, 14.5)]), fill=255)
    d.polygon(s([(21, 17.5), (29, 22), (21, 26.5)]), fill=255)


def repeat(d):
    # rounded loop with an arrowhead at the top right and one at the bottom left
    d.rounded_rectangle((3 * SS, 8 * SS, 29 * SS, 24 * SS), radius=5.5 * SS, outline=255, width=int(2.8 * SS))
    d.rectangle((17 * SS, 5 * SS, 30 * SS, 11.5 * SS), fill=0)    # gap: top right
    d.rectangle((2 * SS, 20.5 * SS, 15 * SS, 27 * SS), fill=0)    # gap: bottom left
    d.polygon(s([(17, 3.5), (25, 8), (17, 12.5)]), fill=255)
    d.polygon(s([(15, 19.5), (7, 24), (15, 28.5)]), fill=255)


ICONS = {"play": play, "pause": pause, "prev": prev, "next": nxt, "shuffle": shuffle, "repeat": repeat}


# ---- minimal SVG path support (M L H V C Z, absolute and relative) ------------------------------------

_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"([MmLlHhVvCcZz])|({_NUM})")


def parse_path(d: str) -> list[list[tuple[float, float]]]:
    """Path data -> list of subpaths, each a polyline (curves are flattened)."""
    toks = [(m.group(1), m.group(2)) for m in _TOKEN.finditer(d)]
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = sx = sy = 0.0
    i, cmd = 0, ""

    def take(n):
        nonlocal i
        vals = [float(toks[i + k][1]) for k in range(n)]
        i += n
        return vals

    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
            if cmd in "Zz":
                if cur:
                    subpaths.append(cur)
                cur, x, y = [], sx, sy
                continue
        elif cmd in ("M", "m"):  # extra coordinate pairs after a moveto are linetos
            cmd = "L" if cmd == "M" else "l"
        rel, c = cmd.islower(), cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            nx, ny = take(2)
            if cur:
                subpaths.append(cur)
            x, y = nx + ox, ny + oy
            sx, sy = x, y
            cur = [(x, y)]
        elif c == "L":
            nx, ny = take(2)
            x, y = nx + ox, ny + oy
            cur.append((x, y))
        elif c == "H":
            (nx,) = take(1)
            x = nx + ox
            cur.append((x, y))
        elif c == "V":
            (ny,) = take(1)
            y = ny + oy
            cur.append((x, y))
        elif c == "C":
            x1, y1, x2, y2, nx, ny = take(6)
            p0 = (x, y)
            p1, p2, p3 = (x1 + ox, y1 + oy), (x2 + ox, y2 + oy), (nx + ox, ny + oy)
            for k in range(1, 17):
                t = k / 16
                a, b, cc, dd = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                cur.append((a * p0[0] + b * p1[0] + cc * p2[0] + dd * p3[0],
                            a * p0[1] + b * p1[1] + cc * p2[1] + dd * p3[1]))
            x, y = p3
        else:
            raise ValueError(f"unsupported path command {cmd!r}")
    if cur:
        subpaths.append(cur)
    return subpaths


def render_svg(path: Path, fit: float) -> Image.Image:
    """Rasterize the SVG's paths (even-odd) scaled to `fit` px on the longer side, centered in SIZE x SIZE."""
    text = path.read_text(encoding="utf-8")
    vb = re.search(r'viewBox="([^"]+)"', text)
    _, _, vw, vh = (float(v) for v in vb.group(1).replace(",", " ").split())
    scale = fit / max(vw, vh)
    ox, oy = (SIZE - vw * scale) / 2, (SIZE - vh * scale) / 2
    mask = Image.new("L", (SIZE * SS, SIZE * SS), 0)
    for d in re.findall(r'<path[^>]*\sd="([^"]+)"', text):
        for sub in parse_path(d):
            layer = Image.new("L", mask.size, 0)
            ImageDraw.Draw(layer).polygon([((px * scale + ox) * SS, (py * scale + oy) * SS) for px, py in sub], fill=255)
            mask = ImageChops.difference(mask, layer)  # XOR of the subpaths = even-odd fill
    return mask.resize((SIZE, SIZE), Image.LANCZOS)


# longer side in pixels of each icon when taken from an SVG: prev/next square, wide icons a little larger
FIT = {"prev": 24, "next": 24, "shuffle": 28, "repeat": 28, "play": 24, "pause": 24}


def has_override(name: str) -> bool:
    return any((OVERRIDES / f"{name}.{ext}").exists() for ext in ("svg", "png"))


def render(name: str) -> Image.Image:
    if name == "next" and not has_override("next") and has_override("prev"):
        return ImageOps.mirror(render("prev"))
    svg = OVERRIDES / f"{name}.svg"
    if svg.exists():
        return render_svg(svg, FIT[name])
    override = OVERRIDES / f"{name}.png"
    if override.exists():
        src = Image.open(override).convert("RGBA")
        alpha = src.getchannel("A")
        if alpha.getextrema() == (255, 255):  # opaque image: use brightness as the shape
            alpha = src.convert("L")
        side = max(alpha.size)
        square = Image.new("L", (side, side), 0)
        square.paste(alpha, ((side - alpha.width) // 2, (side - alpha.height) // 2))
        return square.resize((SIZE, SIZE), Image.LANCZOS)
    im, d = canvas()
    ICONS[name](d)
    return im.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="also write a preview sheet (white icons on dark, 4x) to this PNG")
    args = ap.parse_args()

    rendered = {n: render(n) for n in ICONS}
    lines = [
        "// GENERATED by tools/gen_icons.py - do not edit. 32x32 8-bit alpha bitmaps, tinted with img_recolor.",
        '#include "icons.h"',
        "",
        "#include <string.h>",
        "",
    ]
    for n, im in rendered.items():
        data = list(im.tobytes())
        lines.append(f"static const uint8_t {n}_map[{SIZE * SIZE}] = {{")
        for i in range(0, len(data), 32):
            lines.append("  " + ", ".join(f"{v}" for v in data[i:i + 32]) + ",")
        lines.append("};")
    lines.append("")
    for n in rendered:
        lines.append(f"lv_img_dsc_t icon_{n};")
    lines += ["", "void iconsInit() {",
              "  auto set = [](lv_img_dsc_t& d, const uint8_t* map) {",
              "    memset(&d, 0, sizeof d);",
              "    d.header.cf = LV_IMG_CF_ALPHA_8BIT;",
              f"    d.header.w = {SIZE};",
              f"    d.header.h = {SIZE};",
              f"    d.data_size = {SIZE * SIZE};",
              "    d.data = map;",
              "  };"]
    for n in rendered:
        lines.append(f"  set(icon_{n}, {n}_map);")
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(rendered)} icons)")

    if args.preview:
        scale = 4
        sheet = Image.new("RGB", (len(rendered) * (SIZE * scale + 16) + 16, SIZE * scale + 32), (27, 37, 53))
        for i, im in enumerate(rendered.values()):
            big = im.resize((SIZE * scale, SIZE * scale), Image.NEAREST)
            white = Image.new("RGB", big.size, (255, 255, 255))
            sheet.paste(white, (16 + i * (SIZE * scale + 16), 16), big)
        sheet.save(args.preview)
        print(f"preview: {args.preview}")


if __name__ == "__main__":
    main()
