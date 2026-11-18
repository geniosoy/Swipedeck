# Streamdeck (ES3C28P)

A touch macro deck with a "Now playing" screen and a PC stats screen, for the Hosyond/LCDWiki 2.8" ESP32-S3 board
(ES3C28P). The board shows the screens and sends touches; a small Python program on the PC (the **companion**) runs
the actions and feeds the live screens over the USB-C cable.

Board facts and traps: `../ES3C28P_BOARD_NOTES.md`. Firmware: Arduino + PlatformIO + LVGL 8.3.6 (v0.6.0).

## What it does
* **Desktops** you swipe between. A *keys* desktop holds **pages** of keys; a folder key opens a page, a back button
  returns. A fixed top bar shows the title, the back button and dots for the current desktop.
* **Key actions** on the PC: hotkeys (also media/volume keys), typed text, open file/URL/app, run a program, lock the PC,
  and sequences of these.
* **Now playing** desktop (Spotify Car Thing style) fed by the Windows media session (the volume-popup one): cover,
  cover-tinted background, album / title / artist, elapsed / total time, a thin progress bar you can drag to seek, and
  shuffle / previous / play-pause / next / repeat.
* **PC stats** desktop: CPU, GPU and RAM gauges, GPU temperature and power, VRAM and disk bars, network speed.
* **Sound feedback** on touch (ES8311 codec + speaker): tick on a key, chirp when opening a folder, falling chirp on Back.
* The board stores the last layout and the settings, so it still works without the PC (key presses just do nothing).

## Running it
**PC side:** double-click **`Streamdeck.bat`**. On the first run it creates the Python environment
(`companion\.venv`) and installs the requirements, then starts the companion; afterwards it just starts. Arguments are
passed through: `--dry-run` (log actions, do not run them), `--no-media`, `--no-stats`, `--port COM3`, `--deck FILE`,
`--check` (validate the deck file and exit), `-v` (debug log).
* **`Streamdeck-background.vbs`** starts it without a window (log: `companion\companion.log`). A shortcut to it in the
  Startup folder (Win+R, `shell:startup`) starts the companion with Windows.
* **`Stop-Streamdeck.bat`** stops a running companion (hidden or not). Do this before flashing: it holds the USB port.
  It reconnects by itself after a replug or reflash.

**Board:** the firmware is flashed with PlatformIO:
```
%USERPROFILE%\.platformio\penv\Scripts\pio.exe run -t upload --upload-port COM3
```
If the board does not accept the upload: hold BOOT, tap RESET, release BOOT, upload, tap RESET once.

## Configuring: `companion/deck.json`
One file holds everything. The companion strips the actions, sends the layout to the board, and reloads when the file
is saved (the board's layout, settings and screens update without a restart). The comments in this example are only
for explanation: the real file is plain JSON.
```jsonc
{
  "settings": { "volume": 70, "sounds": true, "brightness": 255 },
  "desktops": [
    { "name": "Main", "pages": [                                  // a keys desktop
      { "id": "root", "title": "Main", "cols": 4, "rows": 3, "keys": [
        { "label": "Mute", "icon": "mute", "color": "#B3261E", "action": { "type": "hotkey", "keys": "volume_mute" } },
        { "label": "Media", "icon": "audio", "folder": "media" },  // opens the page with id "media"
        {}                                                         // empty slot
      ] },
      { "id": "media", "title": "Media", "cols": 3, "rows": 2, "keys": [ /* ... */ ] }
    ] },
    { "name": "Playing", "kind": "nowplaying" },                   // the media screen (at most one)
    { "name": "PC", "kind": "stats" }                              // the stats screen (at most one)
  ]
}
```
* Up to 8 desktops, 16 pages per desktop, 6 x 5 keys per page. The first page of a desktop is its root.
* Actions: `hotkey` (`"ctrl+shift+s"`, `"volume_up"`, `"f13"` ...), `text` (a newline presses Enter), `open`,
  `run` (list, or string with `"shell": true`), `lock`, `multi` (steps, with `{"wait": seconds}`). Shared actions can
  be defined in a top-level `"actions"` map and referenced by a key's `"id"`. Everything is validated when loading, with
  readable errors (`--check`).
* Icons on keys come from a built-in list of about 50 names (`mute`, `play`, `home`, `copy` ...), see `ICONS` in
  `companion/streamdeck_companion/deck.py`; an unknown name shows a warning symbol and a log warning.
* The whole layout must stay below about 15 KB of JSON.

## Live screens run on demand
The Now playing and PC stats screens only cost the PC something while they are on screen. The board tells the
companion which desktop is showing; the media-session reader and the stats sampler run only then, stay completely idle
otherwise, and start from a clean state when you come back (the full state is sent again). On the board, LVGL does not
draw off-screen objects, so nothing needs switching there.

## Now playing
* Cover (140 x 140, square) converted on the PC to RGB565; the screen background is a dark, muted tone of the cover's
  average color. The title uses up to two lines (largest font that fits, then an ellipsis); album and artist scroll
  once in a while only when they do not fit (pause, scroll, pause, scroll back).
* Controls are icon-only. Shuffle and repeat (off / list / track) are green when active and dimmed when the player does
  not offer them through Windows. There is **no like button**: Windows exposes no like/save control.
* Seeking, shuffle and repeat only work for players that support them through Windows. If no media session exists, play /
  next / previous fall back to the plain media keys.
* Icons: 32 x 32 alpha bitmaps generated by `tools/gen_icons.py` into `src/icons.cpp`
  (`companion\.venv\Scripts\python tools\gen_icons.py --preview out.png`). Shuffle, repeat and previous (next is
  its mirror) come from SVGs in `tools/icons/`; play and pause are drawn by the script. Put `play.svg`, `pause.svg` or
  `next.svg` (or `.png` with the same names) there and run the script again to replace any of them. Only use artwork you
  may use.

## PC stats
CPU, RAM, disk and network come from `psutil`; the GPU (first NVIDIA card: usage, temperature, power, VRAM) from the
driver's NVML library, with no process spawned per reading. **CPU temperature is not available** (Windows only gives it
to elevated programs or sensor software such as LibreHardwareMonitor), so it shows "temp n/a"; any value that cannot be
read shows as n/a. AMD / Intel GPUs are not read.

## Known limits
* The board fonts are plain ASCII: the companion strips accents (é becomes e) and shows other scripts as `?`. Accented
  fonts need a font generated with `lv_font_conv` (an npm tool); not done yet.
* USB serial is shared: only one program at a time can use the board's port.

## Link protocol
JSON lines over the USB serial port; the full table is in `src/link.h`. PC to board: `hello`, `ping`, chunked `layout`,
`settings`, `focus`, `reset-layout`, `np` (media state), `cover` / `cover-clear`, `stats`. Board to PC: `hello`
(firmware, screen size, layout CRC, cover size, visible desktop), `pong`, `layout-part` / `layout-ack`, `settings-ack`,
`cover-part` / `cover-ack`, and the events `press`, `nav`, `desktop`, `media` (`toggle|next|prev|shuffle|repeat`),
`seek`. An unchanged layout is not re-sent (FNV-1a CRC). Plain-text debug commands work on the same port:
`ping`, `beep`, `info`, `reboot`, `vol N`, `amp 0|1`, `desktop N`, `back`.

## Layout of the project
```
Streamdeck.bat, Streamdeck-background.vbs, Stop-Streamdeck.bat   launchers for the PC side
platformio.ini, include/, src/                                  firmware (ui.cpp, nowplaying.cpp, stats.cpp, link.cpp, audio.cpp ...)
lib/                                                            patched TFT_eSPI, FT6336, ES8311 (see below)
tools/gen_icons.py, tools/icons/                                icon generator and its SVG sources
companion/                                                      the Python companion: deck.json, streamdeck_companion/, tests/
```
Tests: `companion\.venv\Scripts\python -m unittest discover -s tests` (from `companion`).

## Milestones
1. Bring-up (display, touch, sound, serial) - done
2. Firmware UI (swipeable desktops, folders, back, key sounds) - done
3. Serial protocol and PC companion (actions, layout sync, stored layout and settings) - done
4. Now playing screen, PC stats screen, on-demand live screens - done
5. Polish: brightness / sleep, settings screen, accented fonts - open

## Third-party code
`lib/TFT_eSPI` (patched, from the vendor package), `lib/FT6336`, `lib/ES8311` (Espressif, Apache-2.0, patched to use
`Wire`). Python: pyserial, Pillow, psutil, nvidia-ml-py, winrt (pywinrt) packages.
