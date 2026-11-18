import base64
import sys
import unittest

from PIL import Image

from streamdeck_companion import appicon
from streamdeck_companion.nowplaying import NowPlayingSync


def solid(color, size=16):
    return Image.new("RGBA", (size, size), color)


def opaque_pixels(im):
    """How many pixels are (almost) fully opaque, from the alpha channel."""
    return sum(1 for a in im.getchannel("A").tobytes() if a > 250)


class PackingTest(unittest.TestCase):
    def test_size_and_pixel_layout(self):
        data = appicon.to_board_bytes(solid((255, 0, 0, 255)), 20)
        self.assertEqual(len(data), 20 * 20 * 3)                       # RGB565 (2 bytes) + alpha (1 byte) per pixel
        self.assertEqual(data[:3], bytes([0x00, 0xF8, 255]))           # red = 0xF800, little endian, opaque
        self.assertEqual(appicon.to_board_bytes(solid((0, 255, 0, 255)), 4)[:3], bytes([0xE0, 0x07, 255]))   # 0x07E0
        self.assertEqual(appicon.to_board_bytes(solid((0, 0, 255, 255)), 4)[:3], bytes([0x1F, 0x00, 255]))    # 0x001F

    def test_transparency_is_kept(self):
        half = appicon.to_board_bytes(solid((255, 255, 255, 128)), 4)
        self.assertEqual(half[2], 128)
        clear = appicon.to_board_bytes(solid((255, 255, 255, 0)), 4)
        self.assertEqual(clear[2::3], bytes(16))                       # every alpha byte is 0

    def test_non_square_input_and_message(self):
        data = appicon.to_board_bytes(Image.new("RGBA", (40, 10), (10, 20, 30, 255)), 20)
        self.assertEqual(len(data), 1200)
        msg = appicon.appicon_message("k", data, 20)
        self.assertEqual((msg["type"], msg["id"], msg["px"]), ("appicon", "k", 20))
        self.assertEqual(base64.b64decode(msg["data"]), data)
        self.assertLess(len(msg["data"]), 3000)                        # one small line: no chunking needed


class CatalogIconTest(unittest.TestCase):
    def test_finding_apps(self):
        self.assertEqual(appicon.find_app("Chrome")["id"], "chrome")
        self.assertEqual(appicon.find_app("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")["id"], "spotify")
        self.assertEqual(appicon.find_app("MSEdge")["id"], "edge")
        self.assertIsNone(appicon.find_app("my.own.player"))
        self.assertIsNone(appicon.find_app(""))

    def test_a_brand_logo_is_drawn_in_the_brand_color(self):
        im = appicon.catalog_icon("Spotify.exe")
        self.assertEqual(im.size, (appicon.RENDER_PX, appicon.RENDER_PX))
        self.assertGreater(opaque_pixels(im), 200)                     # the glyph was really drawn
        solid_px = [im.getpixel((x, y)) for x in range(0, im.width, 2) for y in range(0, im.height, 2) if im.getpixel((x, y))[3] > 250]
        r, g, b, _ = solid_px[len(solid_px) // 2]
        self.assertTrue(g > r and g > b, (r, g, b))                    # Spotify green (#1DB954)

    def test_a_letter_badge_and_the_generic_icon(self):
        badge = appicon.catalog_icon("TIDAL.exe")
        self.assertEqual(badge.getpixel((appicon.RENDER_PX // 2, 4))[3], 255)   # filled badge
        self.assertEqual(badge.getpixel((0, 0))[3], 0)                          # rounded corner is transparent
        note = appicon.catalog_icon("unknown.player")
        self.assertGreater(opaque_pixels(note), 200)                            # a music note, not an empty image

    def test_every_catalog_app_can_be_drawn(self):
        for app in appicon.load_catalog()["apps"]:
            im = appicon.catalog_icon(app["patterns"][0])
            self.assertGreater(opaque_pixels(im), 150, app["id"])


class AppIconTest(unittest.TestCase):
    def test_unknown_apps_have_no_shell_icon_and_never_raise(self):
        self.assertIsNone(appicon.shell_icon(""))
        self.assertIsNone(appicon.shell_icon("No.Such.App.Anywhere"))
        self.assertIsNone(appicon.shell_icon("bad\x00id"))

    @unittest.skipUnless(sys.platform == "win32", "the Shell icon is Windows only")
    def test_shell_icon_of_an_app_that_has_one_is_a_real_image(self):
        # Explorer is always registered with the Shell; if this machine has no such entry, skip rather than fail.
        im = appicon.shell_icon("Microsoft.Windows.Explorer")
        if im is None:
            self.skipTest("no Shell entry for Explorer here")
        self.assertGreater(im.size[0], 15)
        self.assertGreater(im.getchannel("A").getextrema()[1], 0)

    def test_app_icon_is_20px_cached_and_stable(self):
        key, data = appicon.app_icon("Deezer.exe")
        self.assertEqual(len(data), appicon.APP_ICON_PX ** 2 * 3)
        self.assertEqual(appicon.app_icon("Deezer.exe"), (key, data))           # same key every time
        self.assertNotEqual(appicon.app_icon("TIDAL.exe")[0], key)              # different apps, different keys

    def test_what_the_board_will_draw_is_the_logo(self):
        _, data = appicon.app_icon("Spotify.exe")
        green = sum(1 for i in range(0, len(data), 3) if data[i + 2] > 200 and
                    ((data[i] | data[i + 1] << 8) >> 5 & 63) > ((data[i] | data[i + 1] << 8) >> 11 & 31) * 2)
        self.assertGreater(green, 80)   # most of the opaque pixels are Spotify green (green channel well above red)


class IconSyncTest(unittest.TestCase):
    def test_actions(self):
        s = NowPlayingSync()
        self.assertEqual(s.icon_action(None), "none")
        self.assertEqual(s.icon_action("a"), "send")
        s.icon_sent = "a"
        self.assertEqual(s.icon_action("a"), "none")
        self.assertEqual(s.icon_action("b"), "send")
        self.assertEqual(s.icon_action(None), "clear")
        s.icon_sent = ""
        self.assertEqual(s.icon_action(None), "none")


if __name__ == "__main__":
    unittest.main()
