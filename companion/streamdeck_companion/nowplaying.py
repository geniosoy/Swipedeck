"""Now playing: what the board should show, derived from a media snapshot.

Pure logic (no Windows API here) so it can be tested: `NowPlayingSync` decides which messages to send,
`cover_to_rgb565` / `cover_messages` prepare the cover image for the board.
"""
from __future__ import annotations

import base64
import colorsys
import hashlib
import io
import unicodedata
from dataclasses import dataclass

COVER_PX_DEFAULT = 140  # the firmware announces its real size in hello (coverPx)
COVER_CHUNK_BYTES = 3000   # raw bytes per message (4000 base64 chars, well below the board's line limit)
POS_DRIFT_MS = 1500        # resend the position when the board's own clock drifts from the player's
RESYNC_S = 10.0            # ...and anyway this often while something is playing


@dataclass
class Snapshot:
    state: str = "none"          # none | playing | paused | stopped
    title: str = ""
    artist: str = ""
    album: str = ""
    pos_ms: int = 0
    dur_ms: int = 0
    rate: float = 1.0
    can_seek: bool = False
    can_prev: bool = False
    can_next: bool = False
    can_play: bool = False
    shuffle: bool = False
    repeat: int = 0              # 0 off, 1 repeat track, 2 repeat list
    can_shuffle: bool = False
    can_repeat: bool = False
    cover_key: str | None = None  # id of the cover image of this track, None = no cover (yet)


def sanitize(text: str, limit: int = 80) -> str:
    """The board fonts only have plain ASCII: drop accents (é -> e), replace anything else with '?'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    out = []
    for ch in decomposed:
        if unicodedata.combining(ch):
            continue
        out.append(ch if " " <= ch <= "~" else ("?" if not ch.isspace() else " "))
    return "".join(out).strip()[:limit]


def cover_to_rgb565(image_bytes: bytes, px: int) -> bytes:
    """Decode any image Pillow can read, center-crop to a square, scale to px x px, little-endian RGB565."""
    from PIL import Image

    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((px, px), Image.LANCZOS)
    raw = im.tobytes()
    out = bytearray(px * px * 2)
    j = 0
    for i in range(0, len(raw), 3):
        v = ((raw[i] & 0xF8) << 8) | ((raw[i + 1] & 0xFC) << 3) | (raw[i + 2] >> 3)
        out[j] = v & 0xFF
        out[j + 1] = v >> 8
        j += 2
    return bytes(out)


def cover_key(image_bytes: bytes) -> str:
    return hashlib.sha1(image_bytes).hexdigest()[:8]


def background_color(image_bytes: bytes) -> str:
    """A dark, muted version of the cover's average color, "#RRGGBB" - the screen background."""
    from PIL import Image

    r, g, b = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    l, s = 0.16 + 0.12 * l, min(s, 0.6)  # always dark enough for white text, hue and some color kept
    r, g, b = (round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
    return f"#{r:02x}{g:02x}{b:02x}"


def cover_messages(key: str, rgb565: bytes, px: int, bg: str = "") -> list[dict]:
    return [{"type": "cover", "id": key, "px": px, "off": off, "total": len(rgb565), "bg": bg,
             "data": base64.b64encode(rgb565[off:off + COVER_CHUNK_BYTES]).decode("ascii")}
            for off in range(0, len(rgb565), COVER_CHUNK_BYTES)]


class NowPlayingSync:
    """Remembers what the board was told and says what must be sent next."""

    def __init__(self):
        self._sent: dict | None = None
        self._sent_at = 0.0
        self.cover_sent: str | None = None  # cover id the board holds ("" = cleared)

    @staticmethod
    def message(s: Snapshot) -> dict:
        return {"type": "np", "state": s.state, "title": sanitize(s.title), "artist": sanitize(s.artist),
                "album": sanitize(s.album), "posMs": max(0, int(s.pos_ms)), "durMs": max(0, int(s.dur_ms)),
                "rate": round(s.rate, 3), "canSeek": s.can_seek and s.dur_ms > 0, "canPrev": s.can_prev,
                "canNext": s.can_next, "canPlay": s.can_play, "shuffle": s.shuffle, "repeat": s.repeat,
                "canShuffle": s.can_shuffle, "canRepeat": s.can_repeat}

    def update(self, snap: Snapshot, now: float) -> dict | None:
        """The `np` message to send now, or None when the board's own clock is still right."""
        msg = self.message(snap)
        if self._sent is None:
            return self._remember(msg, now)
        static_changed = any(msg[k] != self._sent[k] for k in msg if k not in ("posMs", "type"))
        if static_changed:
            return self._remember(msg, now)
        predicted = self._sent["posMs"]
        if self._sent["state"] == "playing":
            predicted += (now - self._sent_at) * 1000 * self._sent["rate"]
        if abs(msg["posMs"] - predicted) > POS_DRIFT_MS:
            return self._remember(msg, now)
        if snap.state == "playing" and now - self._sent_at >= RESYNC_S:
            return self._remember(msg, now)
        return None

    def cover_action(self, snap: Snapshot) -> str:
        """'none' | 'clear' | 'send' for the cover of this snapshot."""
        want = snap.cover_key or ""
        if want == (self.cover_sent or ""):
            return "none"
        return "send" if want else "clear"

    def _remember(self, msg: dict, now: float) -> dict:
        self._sent, self._sent_at = msg, now
        return msg
