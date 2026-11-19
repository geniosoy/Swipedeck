# Streamdeck: ideas for later

Nothing here is started. They build on what already exists (see [README.md](README.md)).

## Small and useful

* **Switch desktops by active app.** The companion notices which window is in front (OBS, VS Code, Discord ...) and
  focuses the matching desktop. The board already accepts a `focus` message, so this is mostly a list of rules
  ("when this app is active, show this desktop") in `deck.json` and the editor. *Suggested first pick: the most noticeable
  in daily use and the cheapest.*
* **Keys that show state.** A mute key that turns red while you are muted, a mic key that shows the mic state. The
  companion reads the state on the PC and tells the board to change a key's colour or icon (needs a new message for that).
* **Long-press actions.** A tap does one thing, holding the key does another. Hold-to-repeat for the volume keys fits
  here too.
* **Sleep with the PC.** Turn the screen off when Windows locks or goes to sleep, and wake it on unlock. Builds on the
  screen-off setting and the wake-up touch handling that already exist.

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
