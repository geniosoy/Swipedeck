"""Builds the web editor's icon table: companion/streamdeck_companion/web/icons.json (+ the font file).

The board draws key icons with LVGL's built-in symbols, which are Font Awesome 5 glyphs. This script reads the icon
names the firmware knows (kIcons in src/ui.cpp), looks up each LVGL symbol's code point in LVGL's lv_symbol_def.h and
writes {name: "f001"}. The editor then draws the very same glyphs with the Font Awesome font that LVGL ships
(copied to web/fonts/). Run it again after adding an icon to kIcons:

    companion\\.venv\\Scripts\\python tools\\gen_icon_map.py

Font Awesome Free: icons CC BY 4.0, font SIL OFL 1.1 (https://fontawesome.com/license/free).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LVGL = ROOT / ".pio" / "libdeps" / "es3c28p" / "lvgl"
WEB = ROOT / "companion" / "streamdeck_companion" / "web"


def main() -> None:
    ui = (ROOT / "src" / "ui.cpp").read_text(encoding="utf-8")
    table = ui[ui.index("kIcons[]"):ui.index("};", ui.index("kIcons[]"))]
    names = re.findall(r'\{"([a-z_]+)",\s*(LV_SYMBOL_[A-Z_0-9]+)\}', table)

    defs = (LVGL / "src" / "font" / "lv_symbol_def.h").read_text(encoding="utf-8")
    # The UTF-8 bytes in the string literal are what the board draws, so they are the source of truth. (LVGL's trailing
    # comment gives the code point too, but it has typos: LV_SYMBOL_FILE says 0xF158 while its bytes encode 0xF15B.)
    code = {}
    for m in re.finditer(r'#define (LV_SYMBOL_[A-Z_0-9]+)\s+"((?:\\x[0-9A-Fa-f]{2})+)"\s*/\*(\d+)', defs):
        symbol, escapes, decimal = m.group(1), m.group(2), int(m.group(3))
        point = ord(bytes.fromhex(escapes.replace("\\x", "")).decode("utf-8"))
        code[symbol] = (point, decimal)

    icons = {}
    for name, symbol in names:
        point, decimal = code[symbol]
        if point != decimal:  # only symbols we use must agree: LVGL's comments are wrong for a few others
            raise SystemExit(f"{symbol}: bytes encode {point:#x} but the comment says {decimal}")
        icons[name] = f"{point:04x}"
    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "icons.json").write_text(json.dumps(icons, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    fonts = WEB / "fonts"
    fonts.mkdir(exist_ok=True)
    shutil.copyfile(LVGL / "scripts" / "built_in_font" / "FontAwesome5-Solid+Brands+Regular.woff", fonts / "fa.woff")
    print(f"{len(icons)} icons -> {WEB / 'icons.json'}")


if __name__ == "__main__":
    main()
