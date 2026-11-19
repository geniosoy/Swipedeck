# Streamdeck: ideas for later

The rest of this list is not started. Everything builds on what already exists (see [README.md](README.md)).

## Built (the small ones)

These four are implemented, documented in the README ("Hold, state and active-app features") and covered by tests. The
firmware part (0.12.0) builds but still has to be tried on the real board: hold and repeat on the touch screen, the
"on" look of a state key, and the screen going off with the PC.

* Switch desktops by active app, keys that show state (speakers or microphone muted, a program running), long-press
  actions and hold-to-repeat keys, sleep with the PC.

## Medium

* **Volume mixer screen.** A touch screen with sliders for the master volume and for each app, as a new live desktop
  like Now playing and PC stats.
* **Clock, timer and weather screen.** It could also be the screensaver shown while the board is dimmed.
* **More action types.** An HTTP request (Home Assistant, smart lights), OBS control over its WebSocket, Discord mute
  and deafen.

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
