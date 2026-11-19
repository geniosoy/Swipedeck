"""The icon of the app that is playing, for the Now playing screen (option "appIcon", off by default).

Where the picture comes from, in this order:
  1. the real icon Windows shows for the app (the Shell's icon for its AppUserModelID, the id the media session reports);
  2. the app's logo or a colored letter badge from the catalog (web/apps.json, the one the editor shows), drawn here;
  3. a generic music note.
It is packed for the board as `px` x `px` pixels of RGB565 (little endian) followed by one alpha byte each, which is what
LVGL's "true color with alpha" image format wants.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger("appicon")

APP_ICON_PX = 20          # the firmware announces its size in hello (iconPx)
WEB = Path(__file__).resolve().parent / "web"
RENDER_PX = 64            # icons are drawn/extracted large and scaled down once

_catalog_cache: dict | None = None
_icon_cache: dict[tuple[str, int], tuple[str, bytes]] = {}
_lock = threading.Lock()


# ---- packing for the board ---------------------------------------------------------------------------

def to_board_bytes(image, px: int = APP_ICON_PX) -> bytes:
    """RGBA image -> px*px pixels of RGB565 (little endian) + alpha byte, 3 bytes per pixel."""
    from PIL import Image

    im = image.convert("RGBA").resize((px, px), Image.LANCZOS)  # Pillow scales RGBA with premultiplied alpha
    raw = im.tobytes()
    out = bytearray(px * px * 3)
    j = 0
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j], out[j + 1], out[j + 2] = v & 0xFF, v >> 8, a
        j += 3
    return bytes(out)


def appicon_message(key: str, data: bytes, px: int = APP_ICON_PX) -> dict:
    return {"type": "appicon", "id": key, "px": px, "data": base64.b64encode(data).decode("ascii")}


# ---- 1. the real icon, from the Windows Shell ---------------------------------------------------------

def shell_icon(app_id: str, px: int = RENDER_PX):
    """The icon Windows shows for this AppUserModelID (what the Start menu shows), as an RGBA image; None if there is
    none (the app is not registered with the Shell) or on any other problem. Windows only."""
    if sys.platform != "win32" or not app_id:
        return None
    # On a thread of its own: the Shell needs COM set up in a single-threaded apartment, and doing that on the caller's
    # thread (the media reader's, which has to receive the Windows media callbacks) made the reader wait forever.
    found: dict = {}

    def work() -> None:
        try:
            found["image"] = _shell_icon(app_id, px)
        except Exception as e:  # never let an icon problem reach the media screen
            log.debug("shell icon for %r failed: %s", app_id, e)

    worker = threading.Thread(target=work, name="shell-icon", daemon=True)
    worker.start()
    worker.join(10)
    return found.get("image")


def _shell_icon(app_id: str, px: int):
    import ctypes
    import uuid
    from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_int, c_void_p, cast, sizeof, wintypes

    from PIL import Image

    hresult = ctypes.c_long

    class GUID(Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class SIZE(Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class BITMAP(Structure):
        _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                    ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD), ("bmBits", c_void_p)]

    class BITMAPINFOHEADER(Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    u = uuid.UUID("BCC18B79-BA16-442F-80C4-8A59C30C463B")  # IID_IShellItemImageFactory
    iid = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    ole32, shell32, gdi32, user32 = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.gdi32, ctypes.windll.user32
    ole32.CoInitializeEx(None, 2)  # the Shell wants COM on the calling thread; "already initialised" is fine
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, c_void_p, POINTER(GUID), POINTER(c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = hresult
    gdi32.GetObjectW.argtypes = [wintypes.HGDIOBJ, c_int, c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, c_void_p, c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    item = c_void_p()
    if shell32.SHCreateItemFromParsingName("shell:AppsFolder\\" + app_id, None, byref(iid), byref(item)) != 0 or not item:
        return None
    vtbl = cast(item, POINTER(POINTER(c_void_p)))[0]
    get_image = WINFUNCTYPE(hresult, c_void_p, SIZE, c_int, POINTER(wintypes.HBITMAP))(vtbl[3])  # IShellItemImageFactory::GetImage
    release = WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl[2])
    hbm = wintypes.HBITMAP()
    try:
        if get_image(item, SIZE(px, px), 0x1 | 0x4, byref(hbm)) != 0 or not hbm:  # BIGGERSIZEOK | ICONONLY
            return None
        bm = BITMAP()
        gdi32.GetObjectW(hbm, sizeof(BITMAP), byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        info = BITMAPINFOHEADER(sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # 32 bit, top-down
        buf = (ctypes.c_ubyte * (w * h * 4))()
        dc = user32.GetDC(None)
        got = gdi32.GetDIBits(dc, hbm, 0, h, buf, byref(info), 0)
        user32.ReleaseDC(None, dc)
        if not got:
            return None
        return Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)  # straight (not premultiplied) alpha
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        release(item)


# ---- 2. the catalog, 3. generic ----------------------------------------------------------------------

def load_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is None:
        try:
            _catalog_cache = json.loads((WEB / "apps.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalog_cache = {"categories": [], "apps": []}
    return _catalog_cache


def find_app(app_id: str, catalog: dict | None = None) -> dict | None:
    """The catalog app a Windows app id belongs to: an exact match of one of its patterns, else an id that contains one
    (same rule as the editor's catalogApp)."""
    apps = (catalog or load_catalog())["apps"]
    low = (app_id or "").lower()
    for a in apps:
        if any(low == q.lower() for q in a["patterns"]):
            return a
    for a in apps:
        if any(q.lower() in low for q in a["patterns"]):
            return a
    return None


def _hex(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _glyph(code: int, color: tuple[int, int, int], px: int = RENDER_PX):
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    font = ImageFont.truetype(str(WEB / "fonts" / "fa.woff"), int(px * 0.82))
    ImageDraw.Draw(im).text((px / 2, px / 2), chr(code), font=font, fill=color + (255,), anchor="mm")
    return im


def _badge(letter: str, color: tuple[int, int, int], px: int = RENDER_PX):
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, px - 1, px - 1), radius=int(px * 0.22), fill=color + (255,))
    d.text((px / 2, px / 2), letter, font=ImageFont.load_default(int(px * 0.7)), fill=(255, 255, 255, 255), anchor="mm")
    return im


def catalog_icon(app_id: str, px: int = RENDER_PX):
    """Logo (or letter badge) of a known app; a generic music note for anything else. Always returns an image."""
    app = find_app(app_id)
    try:
        if app and app.get("glyph"):
            return _glyph(int(app["glyph"], 16), _hex(app["color"]), px)
        if app and app.get("letter"):
            return _badge(app["letter"], _hex(app["color"]), px)
        return _glyph(0xF001, (200, 210, 225), px)  # music note
    except Exception as e:  # font missing/unreadable: a plain disc is better than no icon
        log.debug("catalog icon failed: %s", e)
        from PIL import Image, ImageDraw

        im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((6, 6, px - 7, px - 7), fill=(200, 210, 225, 255))
        return im


# ---- what the companion asks for ----------------------------------------------------------------------

def app_icon(app_id: str, px: int = APP_ICON_PX) -> tuple[str, bytes]:
    """(key, bytes for the board) of the icon of this app. Cached per app; never raises."""
    with _lock:
        cached = _icon_cache.get((app_id, px))
    if cached:
        return cached
    image = shell_icon(app_id) or catalog_icon(app_id)
    data = to_board_bytes(image, px)
    result = (hashlib.sha1(app_id.encode("utf-8")).hexdigest()[:8], data)
    with _lock:
        _icon_cache[(app_id, px)] = result
    return result
