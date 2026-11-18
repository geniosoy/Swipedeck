// Streamdeck firmware (milestone 3): deck UI + JSON link to the PC companion.
#include <Arduino.h>
#include <lvgl.h>

#include "audio.h"
#include "display_hal.h"
#include "link.h"
#include "ui.h"

void setup() {
  Serial.setRxBufferSize(16384);
  Serial.begin(115200);
  Serial.setTxTimeoutMs(10);  // never stall the UI when nobody is reading the port
  delay(500);
  Serial.println("\n[streamdeck] boot");
  displayBegin();
  const bool audioOk = audioBegin();
  uiInit(linkSendEvent);
  linkBegin();  // storage, saved settings, saved layout (or the demo)
  Serial.printf("[streamdeck] ready, audio %s, heap %u KB, PSRAM %u KB\n", audioOk ? "ok" : "FAILED",
                (unsigned)(ESP.getFreeHeap() / 1024), (unsigned)(ESP.getFreePsram() / 1024));
  if (audioOk) audioPlay(Sound::Nav);
}

void loop() {
  displayLoop();
  while (Serial.available()) {
    static String line;
    const char c = Serial.read();
    if (c == '\n') {
      linkHandleLine(line);
      line = "";
    } else if (c != '\r' && line.length() < 15000) line += c;
  }
  delay(5);
}
