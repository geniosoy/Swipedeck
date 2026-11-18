// Streamdeck firmware. Milestone 2: swipeable desktops, folder pages, key sounds, JSON layout.
#include <Arduino.h>
#include <lvgl.h>

#include "audio.h"
#include "deck_model.h"
#include "display_hal.h"
#include "ui.h"

// One JSON object per line, both directions (the PC companion will speak the same thing).
static void sendEvent(const char* json) { Serial.println(json); }

static void loadLayout(const char* json) {
  String err;
  if (uiLoadLayout(json, err)) Serial.println("{\"type\":\"layout-ack\",\"ok\":true}");
  else Serial.printf("{\"type\":\"layout-ack\",\"ok\":false,\"error\":\"%s\"}\n", err.c_str());
}

static void handleLine(String line) {
  line.trim();
  if (line == "ping") Serial.println("{\"type\":\"pong\"}");
  else if (line == "beep") audioPlay(Sound::Tap);
  else if (line == "back") uiGoBack();
  else if (line == "reset") loadLayout(kDefaultLayoutJson);
  else if (line.startsWith("desktop ")) uiShowDesktop(line.substring(8).toInt());
  else if (line.startsWith("layout ")) loadLayout(line.c_str() + 7);
  else if (line.startsWith("vol ")) { audioSetVolume(line.substring(4).toInt()); Serial.printf("vol %d\n", audioVolume()); }
  else if (line.startsWith("amp ")) { audioSetAmp(line.substring(4).toInt() != 0); audioPlay(Sound::Tap); Serial.println("amp set"); }
  else if (line.length()) Serial.println("commands: ping | beep | back | desktop N | reset | layout {json} | vol N | amp 0/1");
}

void setup() {
  Serial.setRxBufferSize(16384);  // whole layouts arrive as one line for now
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n[streamdeck] boot");
  displayBegin();
  const bool audioOk = audioBegin();
  uiInit(sendEvent);
  loadLayout(kDefaultLayoutJson);
  Serial.printf("[streamdeck] ready, audio %s, heap %u KB, PSRAM %u KB\n", audioOk ? "ok" : "FAILED",
                (unsigned)(ESP.getFreeHeap() / 1024), (unsigned)(ESP.getFreePsram() / 1024));
  if (audioOk) audioPlay(Sound::Nav);
}

void loop() {
  displayLoop();
  while (Serial.available()) {
    static String line;
    const char c = Serial.read();
    if (c == '\n') { handleLine(line); line = ""; }
    else if (c != '\r' && line.length() < 15000) line += c;
  }
  delay(5);
}
