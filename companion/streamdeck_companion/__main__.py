"""python -m streamdeck_companion [--deck deck.json] [--port COM3] [--dry-run] [-v]"""
import argparse
import logging
import sys
from pathlib import Path

from .actions import Executor
from .app import Companion
from .deck import DeckError


def main() -> int:
    ap = argparse.ArgumentParser(prog="streamdeck_companion", description="PC side of the ES3C28P Streamdeck")
    ap.add_argument("--deck", default=str(Path(__file__).resolve().parent.parent / "deck.json"),
                    help="deck file (layout + actions), reloaded automatically when it changes")
    ap.add_argument("--port", help="serial port (default: auto-detect the native USB ESP32-S3)")
    ap.add_argument("--dry-run", action="store_true", help="log actions instead of running them")
    ap.add_argument("--no-media", action="store_true", help="do not read the Windows media session (Now playing)")
    ap.add_argument("--check", action="store_true", help="only validate the deck file and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        app = Companion(args.deck, port=args.port, executor=Executor(dry_run=args.dry_run), media=not args.no_media)
    except (DeckError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.check:
        print(f"deck OK ({len(app.deck.layout['desktops'])} desktops, {len(app.deck.warnings)} warning(s))")
        return 0
    try:
        app.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
