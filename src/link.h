// Serial link to the PC companion: one JSON object per line in both directions.
//
// PC -> board                                   board -> PC
//   {"type":"hello"}                              {"type":"hello","protocol":1,"firmware":"...","screen":{"w":320,"h":240},
//                                                  "layoutCrc":"1a2b3c4d","settings":{...}}
//   {"type":"ping","id":7}                        {"type":"pong","id":7}
//   {"type":"layout","seq":0,"of":3,"data":"..."} {"type":"layout-part","seq":0}  per chunk, then
//        (chunks of the layout JSON text)         {"type":"layout-ack","ok":true,"crc":"1a2b3c4d"} (or ok:false,"error")
//   {"type":"settings","volume":70,"sounds":true,"brightness":255,"soundTheme":"classic","navSounds":true,
//     "dimAfter":0,"sleepAfter":0,"flip":false,"startDesktop":0,"keyRadius":12,"keyGap":6,"keyPad":6,"wrap":false,"swipe":"normal","preview":false}
//                                                 {"type":"settings-ack", ...current settings...,"preview":false}
//        (every key is optional; out-of-range values are clamped; "preview":true applies without storing)
//   {"type":"sound","name":"tap|nav|back|error|alarm"}  (plays a UI sound, e.g. the editor's Test buttons; wakes the screen)
//   {"type":"focus","desktop":1}                  (switch the visible desktop)
//   {"type":"keystate","on":["0/0/3","1/0/0"]}    (the keys, as desktop/page/key, whose PC state is on right now: they show
//                                                  their "on" look; all others show their normal one. Idempotent: send the
//                                                  whole set whenever it changes, and again after a layout or a connection)
//   {"type":"pc","state":"locked"|"active"}       (the PC's session is locked / unlocked; with the sleepWithPc setting the
//                                                  screen goes off while it is locked. The same happens when the board hears
//                                                  nothing from the PC for 15 s: PC asleep or off, companion stopped)
//   {"type":"reset-layout"}                       (forget the stored layout, show the built-in demo)
//   {"type":"np","state":"playing","title":..,"artist":..,"album":..,"posMs":..,"durMs":..,"rate":1,
//     "canSeek":..,"canPrev":..,"canNext":..,"canPlay":..,"shuffle":..,"repeat":0..2,"canShuffle":..,"canRepeat":..}
//                                                 (media state for the Now playing desktop; see nowplaying.h)
//   {"type":"cover","id":"..","px":140,"off":0,"total":39200,"bg":"#RRGGBB","data":"<base64 RGB565 piece>"}
//                                                 {"type":"cover-part","off":N} per piece, then
//                                                 {"type":"cover-ack","ok":true,"id":".."}
//   {"type":"cover-clear"}                        (no cover for this track)
//   {"type":"appicon","id":"..","px":20,"data":"<base64: px*px pixels of RGB565 little endian + 1 alpha byte>"}
//                                                 {"type":"appicon-ack","ok":true}   (icon of the playing app; shown only
//                                                 when the layout's nowplaying option appIcon is on; hello has "iconPx")
//   {"type":"appicon-clear"}                      (no player shown)
//   {"type":"stats","cpu":..,"gpu":..,"ramPct":..,...}  (-1 = not available; see stats.h)
//   {"type":"mixer","master":{"vol":50,"mute":false},"apps":[{"id":"chrome","name":"Chrome","vol":80,"mute":false}]}
//                                                 (the volume mixer desktop, see mixer.h; answered by mixer-set / mixer-mute events)
//   {"type":"clock","epoch":1731999999,"offsetMin":-180}   (time of day for the clock desktop, see clockscreen.h)
//   {"type":"weather","ok":true,"temp":9,"unit":"C","text":"Overcast","hi":10,"lo":6,"place":"London"}  ("ok":false = unavailable)
//
// board -> PC events from the UI (see ui.h, nowplaying.h):
//   press {desktop,page,key,id[,hold:true|repeat:true]}   nav {desktop,page}   desktop {index}  (also after a layout load)
//   media {cmd: toggle|next|prev|shuffle|repeat}   seek {posMs}
// hello also carries "uptimeMs", "coverPx" (cover size the PC must send) and "desktop" (the visible desktop):
// the companion only feeds the Now playing / stats screens while their desktop is visible.
//
// Lines that do not start with '{' are the plain-text debug console (type "help").
// The layout CRC is FNV-1a (32 bit) over the exact layout text the PC sent.
#pragma once
#include <Arduino.h>

void linkBegin();                  // storage, saved settings, saved (or demo) layout
void linkHandleLine(String line);  // call for every received line
void linkTick();                   // call from loop(): notices that the PC stopped talking (sleepWithPc)
void linkSendEvent(const char* json);  // UI event sink (ui.h)
