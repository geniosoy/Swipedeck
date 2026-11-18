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
* **`Streamdeck-Editor.bat`** opens the deck editor in your browser (starting the companion in the background first if
  it is not running). The editor lives at http://127.0.0.1:8765/ whenever the companion runs.
* **`Stop-Streamdeck.bat`** stops a running companion (hidden or not). Do this before flashing: it holds the USB port.
  It reconnects by itself after a replug or reflash.

**Board:** the firmware is flashed with PlatformIO:
```
%USERPROFILE%\.platformio\penv\Scripts\pio.exe run -t upload --upload-port COM3
```
If the board does not accept the upload: hold BOOT, tap RESET, release BOOT, upload, tap RESET once.

## The deck editor (web)
The companion serves a layout editor at **http://127.0.0.1:8765/** (`Streamdeck-Editor.bat` opens it). Saving writes
`companion/deck.json`, and the companion pushes the new layout to the board a moment later.
* **Live preview** of the board's screen, drawn with the same geometry and the same icon glyphs as the firmware.
  Click a key to edit it, drag keys to swap them, undo/redo (Ctrl+Z / Ctrl+Y), copy/paste keys (Ctrl+C / Ctrl+V),
  Delete clears a key, arrow keys move the selection, Ctrl+S saves.
* **Per key:** label, icon (all 51 board icons), color, and either an action, a folder (opens another page, or creates one)
  or nothing. Actions have forms: a hotkey builder (modifier tick boxes plus a key box, with a "press a key" capture;
  the Windows key cannot be captured by a browser, so it is a tick box), typed text, open, run, lock, and multi-step
  sequences with waits.
* **Structure:** add, rename and delete desktops and pages, reorder desktops by **dragging** them in the list (or with
  the arrows), and change a page's grid size (keys keep their row and column; you are asked before keys are removed).
* **General settings** (the second tab at the top), see [Settings](#settings) below: sound theme and which sounds play,
  volume with **Test** buttons that play a sound on the board, brightness, auto-dim and screen-off after no touch,
  flipping the screen, which desktop the board opens on, and how the keys look (corner roundness, spacing). Changes show
  on the board **right away** (a live preview that is not stored) and are kept when you save.
* **Add desktop menu** (`+ Add desktop`): the **live screens** (Now playing, PC stats; one each) and **ready-made
  desktops** you can add and then tweak: Discord, Coding (VS Code, with Git and Debug pages), OBS & streaming (F13-F24
  hotkeys to bind in OBS, with a Scenes page), Music, Windows, Browser, Editing and Gaming. They live in
  `companion/streamdeck_companion/web/templates.json`; add your own entries there (the tests validate every one).
* **Live screen settings:** Now playing and PC stats have no keys, so the presets area gives way to their settings
  (below the preview, which follows every option):
  * *Now playing*: two player lists that are always visible, **Only show these players** and **Never show these players**
    (empty = no restriction), next to a searchable library of players: the ones running right now (detected on their own),
    and common music apps, browsers and video players with their logos (a letter badge where there is no logo; names of
    your own get a placeholder icon). Click **Only** or **Never** on a player, or drag it into a list; a player is on one
    list at a time. Below: tint the background with the cover colors, show the album line, show the icon of the app that is
    playing (off by default), full or basic controls (previous / play / next).
  * *PC stats*: the style (gauges, bars or tiles) and which items to show and in which order (drag or arrows).
* **Button presets** (about 90, in 12 categories: audio, media, system, windows, editing, browser, apps, git, streaming,
  text ...): click one to place it in the selected or first empty slot, drag it onto a slot, search them, or fill the
  empty slots with a whole category. "Save as preset" in the key panel keeps your own keys in *My presets*
  (`companion/presets.user.json`).
* Saving checks the deck with the same validation the companion uses and shows the error if something is wrong; the
  previous file is kept as `deck.json.bak`. The file is written one key per line, so it stays easy to read and diff.
* **Safety:** the editor can write actions that run on your PC, so the server listens on 127.0.0.1 only, accepts only its
  own Host name, and writes need a random token that exists only in the editor page (other websites you visit can neither
  read nor write your deck). `--no-editor` turns it off, `--editor-port N` moves it.

## Configuring by hand: `companion/deck.json`
The editor edits this file; you can also edit it directly (changes are picked up while the companion runs). The companion strips the actions, sends the layout to the board, and reloads when the file
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
    { "name": "Playing", "kind": "nowplaying", "options": {        // the media screen (at most one); options are optional
        "filter": { "allow": ["tidal", "spotify"], "block": ["chrome"] },  // names matched by part; both lists optional
        "tint": true, "album": true, "appIcon": false,               // appIcon: the icon of the playing app (off by default)
        "controls": "full" } },                                      // controls: full | basic
    { "name": "PC", "kind": "stats", "options": {                   // the stats screen (at most one)
        "style": "tiles", "items": ["cpu", "gpu", "ram", "vram", "disk", "net"] } }  // style: gauges | bars | tiles
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
* Layouts have only been tried at a few KB; the editor warns above 30 KB.
* **Firmware 0.8.0 or newer** is needed for `appIcon` (older firmware ignores the option and the icon messages), and
  **0.9.0 or newer** for the general settings below beyond volume, sounds and brightness.
* The live-screen options are checked by the companion when it loads the deck (a wrong value is an error with a clear
  message); the board never receives the Now playing player filter.

## Live screens run on demand
The Now playing and PC stats screens only cost the PC something while they are on screen. The board tells the
companion which desktop is showing; the media-session reader and the stats sampler run only then, stay completely idle
otherwise, and start from a clean state when you come back (the full state is sent again). On the board, LVGL does not
draw off-screen objects, so nothing needs switching there.

## Now playing
* **Which player:** Windows can have several media players at once (Chrome, Spotify, TIDAL ...). The `filter` option picks
  among them with two lists of names: a player is shown unless it matches the `block` list, and, when the `allow` list is
  not empty, only if it matches that. Names are matched by a part of the name Windows gives the player (case-insensitive:
  `tidal` matches `TIDAL.exe`); a player on both lists is blocked. (The older `{"mode": "only|except", "apps": [...]}` form
  is still read.) Of the allowed players one that is playing wins, and the one already shown stays (two players playing
  at once do not make the screen flip). With no allowed player running, the screen says "Nothing playing". The filter
  only exists on the PC; the board just shows what it is sent. Firefox's Windows name is a code rather than "firefox"
  (the catalog lists the usual one); the editor's running-players list always shows the exact names.
* **App icon** (`appIcon`, off by default): a small icon of the app that is playing, at the right end of the time row. The
  companion takes the real icon Windows shows for that app (the Shell's icon for its AppUserModelID, e.g. Chrome's), and if
  there is none, the app's logo or letter badge from the catalog (`web/apps.json`), and for unknown apps a music note.
  It is sent once per player as a 20 x 20 picture (RGB565 + alpha). Needs firmware 0.8.0 or newer.
* **Look options** (sent to the board): `tint` (background from the cover), `album` (album line), `controls` (`full` or
  `basic`), `appIcon`.
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
Three styles, chosen with the `style` option, and `items` sets which items are shown and in which order (each item once):
**gauges** (round gauges for the first three percentage items, bars for the rest, the network as a line), **bars** (one
row per item) and **tiles** (two columns of tiles with a big number). Items: `cpu`, `gpu`, `ram`, `vram`, `disk`, `net`.
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
`settings`, `focus`, `reset-layout`, `np` (media state), `cover` / `cover-clear`, `appicon` / `appicon-clear`, `stats`.
Board to PC: `hello` (firmware, screen size, layout CRC, cover and icon sizes, visible desktop), `pong`,
`layout-part` / `layout-ack`, `settings-ack`, `cover-part` / `cover-ack`, `appicon-ack`, and the events `press`, `nav`, `desktop`, `media` (`toggle|next|prev|shuffle|repeat`),
`seek`. An unchanged layout is not re-sent (FNV-1a CRC). Plain-text debug commands work on the same port:
`ping`, `beep`, `info`, `reboot`, `vol N`, `amp 0|1`, `desktop N`, `back`.

## Layout of the project
```
Streamdeck.bat, Streamdeck-background.vbs, Stop-Streamdeck.bat   launchers for the PC side
platformio.ini, include/, src/                                  firmware (ui.cpp, nowplaying.cpp, stats.cpp, link.cpp, audio.cpp ...)
lib/                                                            patched TFT_eSPI, FT6336, ES8311 (see below)
tools/gen_icons.py, tools/icons/                                icon generator and its SVG sources
tools/gen_icon_map.py, gen_favicon.py, e2e_editor.py            editor icon table and favicon generators, browser test
Streamdeck-Editor.bat                                           opens the editor
companion/                                                      the Python companion: deck.json, streamdeck_companion/ (incl.
                                                                editor.py and web/ = the editor), tests/
```
Tests: `companion\.venv\Scripts\python -m unittest discover -s tests` (from `companion`; companion, protocol and editor
server incl. its security checks) and `node --test companion/tests/js/model.test.mjs` (the editor's logic). A full browser
test of the editor, which drives it in headless Edge/Chrome against a copy of the deck, is `tools/e2e_editor.py`
(needs `pip install websocket-client`; `--shots DIR` saves screenshots).

## Settings
The `settings` object of `deck.json` (all keys optional; a missing one is its default). They go to the board when the
companion connects and when the deck is saved, and the board stores them. Wrong values are an error when the deck loads.

| Key | Values | Default | |
|---|---|---|---|
| `volume` | 0..100 | 70 | |
| `sounds` | true / false | true | the tick when a key is pressed |
| `navSounds` | true / false | true | the chirps for opening a folder and Back |
| `soundTheme` | `classic`, `soft`, `click`, `chirp` | `classic` | the four sounds are synthesised on the board (no audio files) |
| `brightness` | 5..255 | 255 | |
| `dimAfter` | seconds, 0..3600 | 0 (never) | dims the backlight (to one eighth of `brightness`) after no touch |
| `sleepAfter` | seconds, 0..3600 | 0 (never) | switches the backlight off after no touch |
| `flip` | true / false | false | the screen upside down, touch included (for a case mounted the other way up) |
| `startDesktop` | 0..7 | 0 | desktop shown at boot and after a layout loads (the first one if the number does not exist) |
| `keyRadius` | 0..24 | 12 | corner roundness of the keys, px |
| `keyGap` | 2..12 | 6 | space between keys, px |

* **Waking the screen:** the touch that wakes a dimmed or sleeping screen is swallowed: it does not press anything, and
  neither does a swipe started with it. Sounds played by the Test buttons also wake the screen.
* **Live preview:** the editor sends the settings page's values with `"preview": true`: the board applies them but does
  not store them, so a reboot or reconnect brings back the saved ones. Saving the deck stores them.
* **Font:** there is no font setting. The board's fonts are the ones compiled into the firmware (Montserrat without
  accents); a different or accented font needs a generated font file, which is the open "accented fonts" item below.

## Milestones
1. Bring-up (display, touch, sound, serial) - done
2. Firmware UI (swipeable desktops, folders, back, key sounds) - done
3. Serial protocol and PC companion (actions, layout sync, stored layout and settings) - done
4. Now playing screen, PC stats screen, on-demand live screens - done
5. Layout editor (web) with button presets - done
6. Settings: sound themes, auto-dim and sleep, flip, start desktop, key look, live preview - done
7. Open: accented fonts (needs generated font files), a settings screen on the board itself

## Third-party code
`lib/TFT_eSPI` (patched, from the vendor package), `lib/FT6336`, `lib/ES8311` (Espressif, Apache-2.0, patched to use
`Wire`). Python: pyserial, Pillow, psutil, nvidia-ml-py, winrt (pywinrt) packages. The editor draws icons with the Font
Awesome Free font that LVGL ships (`web/fonts/fa.woff`; icons CC BY 4.0, font SIL OFL 1.1,
https://fontawesome.com/license/free).
