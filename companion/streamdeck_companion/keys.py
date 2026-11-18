"""Key names -> Windows virtual-key codes, and hotkey strings like "ctrl+shift+s"."""

MODIFIERS = {
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B, "super": 0x5B, "cmd": 0x5B,
}

NAMED = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "capslock": 0x14, "menu": 0x5D,
    # media / volume keys (handled by Windows itself, they work with any player)
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "next_track": 0xB0, "prev_track": 0xB1, "stop": 0xB2, "play_pause": 0xB3,
}

PUNCTUATION = {";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
               "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}

# Keys that Windows expects with the "extended key" flag.
EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x5B, 0x5D,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def key_code(name: str) -> int:
    """Virtual-key code of one key name ("a", "f5", "enter", "volume_up", ...)."""
    n = name.strip().lower()
    if n in MODIFIERS:
        return MODIFIERS[n]
    if n in NAMED:
        return NAMED[n]
    if len(n) == 1 and n.isalnum() and n.isascii():
        return ord(n.upper())
    if len(n) == 1 and n in PUNCTUATION:
        return PUNCTUATION[n]
    if n.startswith("f") and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x70 + int(n[1:]) - 1
    raise ValueError(f"unknown key '{name}'")


def parse_hotkey(text: str) -> list[int]:
    """"ctrl+shift+s" -> [0x11, 0x10, 0x53]. Modifiers are pressed first, in the order written."""
    parts = [x.strip() for x in text.split("+")]
    # An empty part ("ctrl+" or "ctrl++") is almost certainly a typo; the plus key itself is not
    # supported as a hotkey (use a text action instead).
    if any(not p for p in parts):
        raise ValueError(f"malformed hotkey {text!r}")
    return [key_code(p) for p in parts]
