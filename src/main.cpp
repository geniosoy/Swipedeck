// Streamdeck firmware, milestone 1: board bring-up (display, touch, sound, serial link).
#include <Arduino.h>
#include <lvgl.h>

#include "audio.h"
#include "display_hal.h"

static lv_obj_t* touchLabel;

static void onTouch(lv_event_t*) {
  lv_point_t p;
  lv_indev_get_point(lv_indev_get_act(), &p);
  char buf[32];
  snprintf(buf, sizeof buf, "touch %d, %d", (int)p.x, (int)p.y);
  lv_label_set_text(touchLabel, buf);
}

static void onSoundButton(lv_event_t* e) {
  const auto s = static_cast<Sound>((intptr_t)lv_event_get_user_data(e));
  audioPlay(s);
  Serial.printf("{\"type\":\"press\",\"sound\":%d}\n", (int)s);
}

static void addSoundButton(lv_obj_t* parent, const char* text, Sound s, int x) {
  lv_obj_t* b = lv_btn_create(parent);
  lv_obj_set_size(b, 92, 64);
  lv_obj_align(b, LV_ALIGN_CENTER, x, 40);
  lv_obj_add_event_cb(b, onSoundButton, LV_EVENT_PRESSED, (void*)(intptr_t)s);
  lv_obj_t* l = lv_label_create(b);
  lv_label_set_text(l, text);
  lv_obj_center(l);
}

static void handleLine(String line) {
  line.trim();
  if (line == "ping") Serial.println("{\"type\":\"pong\"}");
  else if (line == "beep") audioPlay(Sound::Tap);
  else if (line.startsWith("vol ")) { audioSetVolume(line.substring(4).toInt()); Serial.printf("vol %d\n", audioVolume()); }
  else if (line.startsWith("amp ")) { audioSetAmp(line.substring(4).toInt() != 0); audioPlay(Sound::Tap); Serial.println("amp set"); }
  else if (line.length()) Serial.println("commands: ping | beep | vol N | amp 0/1");
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n[streamdeck] boot");
  displayBegin();
  const bool audioOk = audioBegin();
  Serial.printf("[streamdeck] display ok, audio %s, heap %u KB, PSRAM %u KB\n", audioOk ? "ok" : "FAILED",
                (unsigned)(ESP.getFreeHeap() / 1024), (unsigned)(ESP.getFreePsram() / 1024));

  lv_obj_t* scr = lv_scr_act();
  lv_obj_set_scrollbar_mode(scr, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0x0B1220), 0);
  lv_obj_set_style_text_color(scr, lv_color_hex(0xE6EDF7), 0);
  lv_obj_t* title = lv_label_create(scr);
  lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);
  lv_label_set_text(title, "Streamdeck bring-up");
  lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 14);
  lv_obj_t* st = lv_label_create(scr);
  lv_label_set_text(st, audioOk ? LV_SYMBOL_OK " audio ready" : LV_SYMBOL_WARNING " audio failed");
  lv_obj_align(st, LV_ALIGN_TOP_MID, 0, 56);
  touchLabel = lv_label_create(scr);
  lv_label_set_text(touchLabel, "touch the screen");
  lv_obj_align(touchLabel, LV_ALIGN_CENTER, 0, -30);
  lv_obj_add_event_cb(scr, onTouch, LV_EVENT_PRESSING, nullptr);
  addSoundButton(scr, "Tap", Sound::Tap, -100);
  addSoundButton(scr, "Nav", Sound::Nav, 0);
  addSoundButton(scr, "Back", Sound::Back, 100);
  if (audioOk) audioPlay(Sound::Nav);
}

void loop() {
  displayLoop();
  while (Serial.available()) {
    static String line;
    const char c = Serial.read();
    if (c == '\n') { handleLine(line); line = ""; }
    else if (c != '\r' && line.length() < 200) line += c;
  }
  delay(5);
}
