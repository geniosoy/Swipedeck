import re
import unittest
from pathlib import Path

from streamdeck_companion.deck import ICONS

UI_CPP = Path(__file__).resolve().parents[2] / "src" / "ui.cpp"


class IconSyncTest(unittest.TestCase):
    def test_icon_names_match_firmware(self):
        src = UI_CPP.read_text(encoding="utf-8")
        table = src[src.index("kIcons[]"):src.index("};", src.index("kIcons[]"))]
        firmware = set(re.findall(r'\{"([a-z_]+)",\s*LV_SYMBOL_', table))
        self.assertEqual(firmware, ICONS)


if __name__ == "__main__":
    unittest.main()
