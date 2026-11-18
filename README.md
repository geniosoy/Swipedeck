# Streamdeck (ES3C28P)

Touch macro deck + "Now playing" screen for the Hosyond/LCDWiki 2.8" ESP32-S3 board (ES3C28P).
Board facts and traps: `../ES3C28P_BOARD_NOTES.md`. Firmware: Arduino + PlatformIO + LVGL 8.3.6.

## Goals
* **Desktops** you swipe between; each desktop has several **pages** reached with a folder/button (and a back button).
* **Now playing** screen fed by the Windows media session API (the volume-popup one): title/artist/art,
  play / pause / next / previous, and a progress bar you can drag to seek.
* **Sound feedback** on touch (ES8311 codec + speaker): tap, navigate, back, swipe.
* PC companion over the USB-C serial port runs the actions (hotkeys, apps, media, volume ...) and pushes state.

## Milestones
1. **Bring-up** - display, touch, sound, serial console  *(this commit)*
2. Firmware UI: desktops (swipe) -> pages (folder/back buttons) -> keys, layout loaded from SD/serial
3. Serial protocol + PC companion (actions, layout sync)
4. Now playing screen (SMTC) incl. seek slider
5. Polish: icons, brightness/sleep, settings

## Build / flash
```
%USERPROFILE%\.platformio\penv\Scripts\pio.exe run -t upload --upload-port COM3
```
If the board does not accept the upload: hold BOOT, tap RESET, release BOOT, upload, tap RESET once.

## Serial console (115200)
`ping`, `beep`, `vol N` (0-100), `amp 0|1` (debug the speaker amplifier pin).

## Third-party code
`lib/TFT_eSPI` (patched, vendor package), `lib/FT6336`, `lib/ES8311` (Espressif, Apache-2.0, patched to use `Wire`).
