# Streamdeck: ideas for later

The rest of this list is not started. Everything builds on what already exists (see [README.md](README.md)).

## Built

Documented in the README and covered by tests.

* **The small ones** ("Hold, state and active-app features"): switch desktops by active app, keys that show state
  (speakers or microphone muted, a program running), long-press and hold-to-repeat keys, sleep with the PC. The firmware
  part (0.12.0) builds but still has to be tried on the real board: hold and repeat on the touch screen, the "on" look
  of a state key, and the screen going off with the PC.
* **More action types** ("HTTP, OBS and Discord actions"): an HTTP request (webhooks, Home Assistant), OBS control over
  its WebSocket, and Discord mute / deafen through global keybinds (a real Discord integration needs a registered app, so
  it was left out). The OBS side is tested against a stand-in server, not yet against a real OBS.
* **Volume mixer screen** and **clock, timer and weather screen** ("Volume mixer and clock screens"). The companion side
  was tried for real on the PC; the firmware (0.13.0) builds but still has to be tried on the board: dragging the
  faders, the timer and its alarm. (The clock as a screensaver for the dimmed screen was left out on purpose.)

## Medium

Nothing left here for now.

## Bigger

* **Wi-Fi mode.** The ESP32 talks to the companion over the network, so the cable is not needed for the link.
  Over-the-air firmware updates would also end the "stop the companion before flashing" routine (the companion holds
  the USB port).
* **A settings screen on the board itself,** so the settings can be changed without the editor. (Already on the open
  list in the README.)

## Blocked or parked

* **Choosing a font / accented characters.** The board only has LVGL's built-in Montserrat, which has no accents (the
  companion currently strips them from song titles). A different or accented font needs font files generated with
  `lv_font_conv`. Running it through `npx` was refused by the safety check (it downloads and runs an npm package right
  away). It would work if you approve that one command yourself, install `lv_font_conv` yourself, or drop in pre-built
  font files. Parked: not needed for now.
