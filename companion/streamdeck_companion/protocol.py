"""Helpers shared by the link code: layout chunking and the layout CRC (FNV-1a 32, same as the firmware)."""
from __future__ import annotations

CHUNK_CHARS = 1200  # layout text per message; keeps every line far below the board's 15 kB line limit


def fnv1a32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def crc_hex(text: str) -> str:
    return f"{fnv1a32(text.encode('utf-8')):08x}"


def layout_messages(text: str, chunk: int = CHUNK_CHARS) -> list[dict]:
    parts = [text[i:i + chunk] for i in range(0, len(text), chunk)] or [""]
    return [{"type": "layout", "seq": i, "of": len(parts), "data": p} for i, p in enumerate(parts)]
