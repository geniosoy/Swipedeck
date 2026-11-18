// Serial link to the PC companion: one JSON object per line in both directions.
//
// PC -> board                                   board -> PC
//   {"type":"hello"}                              {"type":"hello","protocol":1,"firmware":"...","screen":{"w":320,"h":240},
//                                                  "layoutCrc":"1a2b3c4d","settings":{...}}
//   {"type":"ping","id":7}                        {"type":"pong","id":7}
//   {"type":"layout","seq":0,"of":3,"data":"..."} {"type":"layout-part","seq":0}  per chunk, then
//        (chunks of the layout JSON text)         {"type":"layout-ack","ok":true,"crc":"1a2b3c4d"} (or ok:false,"error")
//   {"type":"settings","volume":70,"sounds":true,"brightness":255}
//                                                 {"type":"settings-ack", ...current settings...}
//   {"type":"focus","desktop":1}                  (switch the visible desktop)
//   {"type":"reset-layout"}                       (forget the stored layout, show the built-in demo)
//   (board -> PC events from the UI: press / nav / desktop, see ui.h)
//
// Lines that do not start with '{' are the plain-text debug console (type "help").
// The layout CRC is FNV-1a (32 bit) over the exact layout text the PC sent.
#pragma once
#include <Arduino.h>

void linkBegin();                  // storage, saved settings, saved (or demo) layout
void linkHandleLine(String line);  // call for every received line
void linkSendEvent(const char* json);  // UI event sink (ui.h)
