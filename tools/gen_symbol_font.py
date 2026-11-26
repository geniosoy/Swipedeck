"""Generates src/sd_icons.c and src/sd_icons.h: the key icons LVGL's built-in fonts do not have.

    companion\\.venv\\Scripts\\python tools\\gen_symbol_font.py [--check]

LVGL's montserrat fonts carry only LVGL's own symbols. This draws the icons listed in EXTRA from the Font Awesome 5
file that ships with LVGL (the same one the web editor uses) with Pillow, as small LVGL fonts (4 bits per pixel) at the
sizes keys use. The firmware puts them behind the built-in fonts, so an icon is still just a character.

Adding an icon: add it here, run this, add {"name", SD_SYMBOL_NAME} to kIcons in src/ui.cpp, add the name to ICONS in
companion/streamdeck_companion/deck.py, run tools/gen_icon_map.py, and add search words in web/model.js (ICON_WORDS).
--check compares this drawing with an icon LVGL already has, to make sure sizes and placement agree.

Font Awesome Free: icons CC BY 4.0, font SIL OFL 1.1 (https://fontawesome.com/license/free).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LVGL = ROOT / ".pio" / "libdeps" / "es3c28p" / "lvgl"
FONT = LVGL / "scripts" / "built_in_font" / "FontAwesome5-Solid+Brands+Regular.woff"
SIZES = (20, 24, 32)

# name -> Font Awesome 5 code point
EXTRA = {
    "mic": 0xF130, "mic_off": 0xF131, "headphones": 0xF025, "speaker_off": 0xF6A9,
    "camera": 0xF030, "webcam": 0xF03D, "webcam_off": 0xF4E2, "record": 0xF111, "broadcast": 0xF519, "layers": 0xF5FD,
    "lightbulb": 0xF0EB, "lock": 0xF023, "desktop": 0xF108, "search": 0xF002, "smile": 0xF118, "clipboard": 0xF328,
    "chart": 0xF201, "task_view": 0xF24D, "exchange": 0xF362, "maximize": 0xF2D0, "minimize": 0xF2D1,
    "fullscreen": 0xF065, "columns": 0xF0DB, "undo": 0xF0E2, "redo": 0xF01E, "globe": 0xF0AC, "code": 0xF121,
    "terminal": 0xF120, "calculator": 0xF1EC, "envelope": 0xF0E0, "youtube": 0xF167, "spotify": 0xF1BC,
    "discord": 0xF392, "steam": 0xF1B6, "gamepad": 0xF11B, "branch": 0xF126, "bug": 0xF188, "history": 0xF1DA,
    "incognito": 0xF21B, "send": 0xF1D8, "script": 0xF1C9, "text": 0xF031, "bold": 0xF032, "italic": 0xF033,
    "underline": 0xF0CD, "print": 0xF02F, "zoom_in": 0xF00E, "zoom_out": 0xF010,
}


def metrics(size: int) -> tuple[int, int]:
    """line_height and base_line of LVGL's own font at this size, so both fonts sit on the same baseline."""
    src = (LVGL / "src" / "font" / f"lv_font_montserrat_{size}.c").read_text(encoding="utf-8")
    return int(re.search(r"\.line_height = (\d+)", src)[1]), int(re.search(r"\.base_line = (\d+)", src)[1])


def draw(font: ImageFont.FreeTypeFont, code: int):
    """(advance in 1/16 px, box width, box height, x offset, y offset of the box's bottom above the baseline, pixels)."""
    ch = chr(code)
    x0, y0, x1, y1 = font.getbbox(ch, anchor="ls")
    image = Image.new("L", (x1 - x0 + 2, y1 - y0 + 2), 0)
    ImageDraw.Draw(image).text((1 - x0, 1 - y0), ch, font=font, fill=255, anchor="ls")
    image = image.point(lambda v: (v + 8) // 17)  # 0..15
    left, top, right, bottom = image.getbbox()
    box = image.crop((left, top, right, bottom))
    return round(font.getlength(ch) * 16), box.width, box.height, x0 - 1 + left, -(y0 - 1 + bottom), list(box.tobytes())


def pack(pixels: list[int]) -> list[int]:
    """Four bits per pixel, high half first, one run over all rows (LVGL's plain format)."""
    padded = pixels + [0] * (len(pixels) % 2)
    return [(padded[i] << 4) | padded[i + 1] for i in range(0, len(padded), 2)]


def utf8_literal(code: int) -> str:
    return '"' + "".join(f"\\x{b:02X}" for b in chr(code).encode("utf-8")) + '"'


def font_source(size: int) -> str:
    font = ImageFont.truetype(str(FONT), size)
    line_height, base_line = metrics(size)
    codes = sorted(EXTRA.values())
    names = {code: name for name, code in EXTRA.items()}
    bitmap: list[str] = []
    glyphs = ["    {.bitmap_index = 0, .adv_w = 0, .box_w = 0, .box_h = 0, .ofs_x = 0, .ofs_y = 0} /* id 0 reserved */"]
    index = 0
    for code in codes:
        adv, w, h, ofs_x, ofs_y, pixels = draw(font, code)
        data = pack(pixels)
        bitmap.append(f"    /* {names[code]} U+{code:04X} */\n    " + ", ".join(f"0x{b:x}" for b in data) + ",")
        glyphs.append(f"    {{.bitmap_index = {index}, .adv_w = {adv}, .box_w = {w}, .box_h = {h}, .ofs_x = {ofs_x}, .ofs_y = {ofs_y}}}")
        index += len(data)
    first = codes[0]
    joined, table = "\n".join(bitmap), ",\n".join(glyphs)
    offsets = ", ".join(f"0x{code - first:x}" for code in codes)
    return f"""
static LV_ATTRIBUTE_LARGE_CONST const uint8_t bitmap_{size}[] = {{
{joined}
}};
static const lv_font_fmt_txt_glyph_dsc_t glyphs_{size}[] = {{
{table}
}};
static const uint16_t codes_{size}[] = {{{offsets}}};
static const lv_font_fmt_txt_cmap_t cmaps_{size}[] = {{{{
    .range_start = 0x{first:X}, .range_length = {codes[-1] - first + 1}, .glyph_id_start = 1,
    .unicode_list = codes_{size}, .glyph_id_ofs_list = NULL, .list_length = {len(codes)}, .type = LV_FONT_FMT_TXT_CMAP_SPARSE_TINY
}}}};
static lv_font_fmt_txt_glyph_cache_t cache_{size};
static const lv_font_fmt_txt_dsc_t dsc_{size} = {{
    .glyph_bitmap = bitmap_{size}, .glyph_dsc = glyphs_{size}, .cmaps = cmaps_{size}, .kern_dsc = NULL, .kern_scale = 0,
    .cmap_num = 1, .bpp = 4, .kern_classes = 0, .bitmap_format = 0, .cache = &cache_{size}
}};
const lv_font_t sd_icons_{size} = {{
    .get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt, .get_glyph_bitmap = lv_font_get_bitmap_fmt_txt,
    .line_height = {line_height}, .base_line = {base_line}, .subpx = LV_FONT_SUBPX_NONE, .underline_position = -2, .underline_thickness = 1,
    .dsc = &dsc_{size}
}};
"""


def check() -> None:
    """Draws LVGL's own "audio" symbol (U+F001) and prints both sets of numbers."""
    for size in SIZES:
        src = (LVGL / "src" / "font" / f"lv_font_montserrat_{size}.c").read_text(encoding="utf-8")
        sparse = [int(x, 16) for x in re.findall(r"0x[0-9a-f]+", re.search(r"unicode_list_1\[\] = \{(.*?)\}", src, re.S)[1])]
        glyph_id = 96 + sparse.index(0xF001 - 176)
        theirs = re.findall(r"\{\.bitmap_index = \d+, (\.adv_w = [^}]*)\}", src)[glyph_id]
        adv, w, h, x, y, _ = draw(ImageFont.truetype(str(FONT), size), 0xF001)
        print(f"{size}px  LVGL: {theirs}\n      here: .adv_w = {adv}, .box_w = {w}, .box_h = {h}, .ofs_x = {x}, .ofs_y = {y}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    if ap.parse_args().check:
        check()
        return
    note = "// GENERATED by tools/gen_symbol_font.py - do not edit. Font Awesome 5 Free (CC BY 4.0 / SIL OFL 1.1).\n"
    source = note + '#include "sd_icons.h"\n' + "".join(font_source(size) for size in SIZES)
    (ROOT / "src" / "sd_icons.c").write_text(source, encoding="utf-8", newline="\n")
    defines = "\n".join(f"#define SD_SYMBOL_{name.upper()} {utf8_literal(code)}" for name, code in EXTRA.items())
    fonts = ", ".join(f"sd_icons_{size}" for size in SIZES)
    header = (note + "#pragma once\n#include <lvgl.h>\n\n#ifdef __cplusplus\nextern \"C\" {\n#endif\n"
              f"extern const lv_font_t {fonts};\n#ifdef __cplusplus\n}}\n#endif\n\n{defines}\n")
    (ROOT / "src" / "sd_icons.h").write_text(header, encoding="utf-8", newline="\n")
    print(f"{len(EXTRA)} icons at {SIZES} px written to src/sd_icons.c and src/sd_icons.h")


if __name__ == "__main__":
    main()
