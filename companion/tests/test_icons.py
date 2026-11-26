import json
import re
import unittest
from pathlib import Path

from streamdeck_companion.deck import ICONS

UI_CPP = Path(__file__).resolve().parents[2] / "src" / "ui.cpp"


class IconSyncTest(unittest.TestCase):
    def test_icon_names_match_firmware(self):
        src = UI_CPP.read_text(encoding="utf-8")
        table = src[src.index("kIcons[]"):src.index("};", src.index("kIcons[]"))]
        entries = re.findall(r'\{"([a-z_0-9]+)",\s*(LV|SD)_SYMBOL_', table)
        self.assertEqual({name for name, _ in entries}, ICONS)
        self.assertEqual(len(entries), len(ICONS))  # no name twice

    def test_the_extra_icons_are_drawn_and_offered_by_the_editor(self):
        root = UI_CPP.parents[1]
        header = (root / "src" / "sd_icons.h").read_text(encoding="utf-8")
        source = (root / "src" / "sd_icons.c").read_text(encoding="utf-8")
        editor = json.loads((root / "companion" / "streamdeck_companion" / "web" / "icons.json").read_text(encoding="utf-8"))
        extra = dict(re.findall(r'#define SD_SYMBOL_([A-Z_0-9]+) "((?:\\x[0-9A-F]{2})+)"', header))
        self.assertGreater(len(extra), 40)
        for symbol, literal in extra.items():
            name = symbol.lower()
            code = ord(bytes(int(b, 16) for b in re.findall(r"x([0-9A-F]{2})", literal)).decode("utf-8"))
            self.assertEqual(int(editor[name], 16), code, name)      # the editor draws the same glyph
            for size in (20, 24, 32):
                block = source[source.index(f"bitmap_{size}[]"):source.index(f"glyphs_{size}[]")]
                self.assertIn(f"/* {name} U+{code:04X} */", block, f"{name} at {size} px")


if __name__ == "__main__":
    unittest.main()
