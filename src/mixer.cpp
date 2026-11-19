#include "mixer.h"

#include <ArduinoJson.h>

#include "audio.h"

namespace {

constexpr uint32_t kBg = 0x101722, kTrack = 0x2A3A4D, kBlue = 0x3B82F6, kGreen = 0x1ED760, kGrey = 0x55657A, kMutedText = 0x7F8FA6;
constexpr int kCols = 5;                  // the master fader and up to four programs
constexpr int kColW = 56, kColGap = 8;
constexpr int kTileW = 320, kTileH = 210;  // the area below the fixed top bar
// The knob is a circle as wide as the fader (30 px): at 100 % it sticks out 15 px above the track and at 0 % 15 px below it,
// so the number above and the mute button below keep that much room.
constexpr int kSliderW = 30, kSliderTop = 44, kSliderH = 96;
constexpr uint32_t kGuardMs = 700;        // after a touch the PC's value is ignored for a moment (it is still catching up)
constexpr uint32_t kSendEveryMs = 80;

struct Col {
  lv_obj_t* box = nullptr;
  lv_obj_t* pct = nullptr;
  lv_obj_t* slider = nullptr;
  lv_obj_t* mute = nullptr;
  lv_obj_t* muteLbl = nullptr;
  lv_obj_t* name = nullptr;
  String id;
  int vol = 0;
  bool muted = false;
  bool dragging = false;
  uint32_t guardUntil = 0;      // ignore incoming volumes until then
  uint32_t muteGuardUntil = 0;  // ignore incoming mute states until then
  int sentVol = -1;
  uint32_t sentAtMs = 0;
  char pctCache[8] = "";
  char nameCache[24] = "";
};

Col cols[kCols];
lv_obj_t* waiting = nullptr;
UiEventFn sink = nullptr;
MixerInfo last;
bool haveData = false;
bool built = false;
int shownCount = 0;

void emit(JsonDocument& doc) {
  if (!sink) return;
  String s;
  serializeJson(doc, s);
  sink(s.c_str());
}

void sendVolume(Col& c, int vol) {
  JsonDocument d;
  d["type"] = "mixer-set";
  d["id"] = c.id;
  d["vol"] = vol;
  emit(d);
  c.sentVol = vol;
  c.sentAtMs = millis();
}

void setText(lv_obj_t* label, char* cache, size_t size, const char* text) {
  if (!label || strncmp(cache, text, size) == 0) return;
  strlcpy(cache, text, size);
  lv_label_set_text(label, text);
}

void showPercent(Col& c, int vol) {
  char buf[8];
  snprintf(buf, sizeof buf, "%d", vol);
  setText(c.pct, c.pctCache, sizeof c.pctCache, buf);
}

// Colors and the speaker symbol follow the mute state.
void paint(Col& c, int index) {
  const uint32_t accent = c.muted ? kGrey : (index == 0 ? kBlue : kGreen);
  lv_obj_set_style_bg_color(c.slider, lv_color_hex(accent), LV_PART_INDICATOR);
  lv_obj_set_style_bg_color(c.slider, lv_color_hex(accent), LV_PART_KNOB);
  lv_obj_set_style_text_color(c.pct, lv_color_hex(c.muted ? kMutedText : 0xFFFFFF), 0);
  lv_label_set_text(c.muteLbl, c.muted ? LV_SYMBOL_MUTE : LV_SYMBOL_VOLUME_MAX);
  lv_obj_set_style_text_color(c.muteLbl, lv_color_hex(c.muted ? 0xF04438 : 0xFFFFFF), 0);
}

void onSlider(lv_event_t* e) {
  const int index = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
  Col& c = cols[index];
  const int v = lv_slider_get_value(c.slider);
  switch (lv_event_get_code(e)) {
    case LV_EVENT_PRESSED:
      c.dragging = true;
      break;
    case LV_EVENT_VALUE_CHANGED:
      c.vol = v;
      showPercent(c, v);
      if (millis() - c.sentAtMs >= kSendEveryMs && v != c.sentVol) sendVolume(c, v);
      break;
    case LV_EVENT_RELEASED:
    case LV_EVENT_PRESS_LOST:
      if (!c.dragging) break;
      c.dragging = false;
      c.guardUntil = millis() + kGuardMs;
      c.vol = v;
      if (v != c.sentVol) sendVolume(c, v);  // the final position always reaches the PC
      break;
    default:
      break;
  }
}

void onMute(lv_event_t* e) {
  const int index = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
  Col& c = cols[index];
  c.muted = !c.muted;
  c.muteGuardUntil = millis() + kGuardMs;
  paint(c, index);
  audioPlay(Sound::Tap);
  JsonDocument d;
  d["type"] = "mixer-mute";
  d["id"] = c.id;
  d["mute"] = c.muted;
  emit(d);
}

void buildCol(lv_obj_t* root, int index) {
  Col& c = cols[index];
  c = Col();
  c.box = lv_obj_create(root);
  lv_obj_remove_style_all(c.box);
  lv_obj_set_size(c.box, kColW, kTileH);
  lv_obj_clear_flag(c.box, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_flag(c.box, LV_OBJ_FLAG_HIDDEN);

  c.pct = lv_label_create(c.box);
  lv_obj_set_style_text_font(c.pct, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(c.pct, lv_color_white(), 0);
  lv_obj_set_width(c.pct, kColW);
  lv_obj_set_style_text_align(c.pct, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_pos(c.pct, 0, 4);
  lv_label_set_text(c.pct, "--");

  c.slider = lv_slider_create(c.box);  // taller than wide: a vertical fader
  lv_obj_set_size(c.slider, kSliderW, kSliderH);
  lv_obj_set_pos(c.slider, (kColW - kSliderW) / 2, kSliderTop);
  lv_slider_set_range(c.slider, 0, 100);
  lv_obj_set_ext_click_area(c.slider, 12);  // a generous target for a finger
  lv_obj_set_style_bg_color(c.slider, lv_color_hex(kTrack), LV_PART_MAIN);
  lv_obj_set_style_bg_opa(c.slider, LV_OPA_COVER, LV_PART_MAIN);
  lv_obj_set_style_radius(c.slider, 10, LV_PART_MAIN);
  lv_obj_set_style_radius(c.slider, 10, LV_PART_INDICATOR);
  lv_obj_set_style_bg_opa(c.slider, LV_OPA_COVER, LV_PART_INDICATOR);
  lv_obj_set_style_bg_opa(c.slider, LV_OPA_COVER, LV_PART_KNOB);
  lv_obj_set_style_pad_all(c.slider, 0, LV_PART_KNOB);  // knob = the fader's width, no bigger
  lv_obj_add_event_cb(c.slider, onSlider, LV_EVENT_ALL, reinterpret_cast<void*>(static_cast<intptr_t>(index)));

  c.mute = lv_btn_create(c.box);
  lv_obj_remove_style_all(c.mute);
  lv_obj_set_size(c.mute, 44, 30);
  lv_obj_set_pos(c.mute, (kColW - 44) / 2, kSliderTop + kSliderH + 18);
  lv_obj_set_style_bg_opa(c.mute, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(c.mute, lv_color_hex(kTrack), 0);
  lv_obj_set_style_bg_color(c.mute, lv_color_hex(kGrey), LV_STATE_PRESSED);
  lv_obj_set_style_radius(c.mute, 8, 0);
  lv_obj_add_event_cb(c.mute, onMute, LV_EVENT_CLICKED, reinterpret_cast<void*>(static_cast<intptr_t>(index)));
  c.muteLbl = lv_label_create(c.mute);
  lv_obj_set_style_text_font(c.muteLbl, &lv_font_montserrat_18, 0);
  lv_obj_center(c.muteLbl);

  c.name = lv_label_create(c.box);
  lv_obj_set_style_text_font(c.name, &lv_font_montserrat_14, 0);
  lv_obj_set_style_text_color(c.name, lv_color_hex(0xC5D1E3), 0);
  lv_obj_set_width(c.name, kColW);
  lv_obj_set_style_text_align(c.name, LV_TEXT_ALIGN_CENTER, 0);
  lv_label_set_long_mode(c.name, LV_LABEL_LONG_DOT);
  lv_obj_set_pos(c.name, 0, kTileH - 22);
  lv_label_set_text(c.name, "");
  paint(c, index);
}

// Puts what the PC sent on the faders. Columns are centered as a group; a fader you are touching is left alone.
void apply() {
  if (!built) return;
  const int n = haveData ? 1 + min<int>(static_cast<int>(last.apps.size()), kCols - 1) : 0;
  if (n != shownCount) {
    shownCount = n;
    const int total = n * kColW + (n > 0 ? (n - 1) * kColGap : 0);
    const int x0 = (kTileW - total) / 2;
    for (int i = 0; i < kCols; i++) {
      if (i < n) {
        lv_obj_set_pos(cols[i].box, x0 + i * (kColW + kColGap), 0);
        lv_obj_clear_flag(cols[i].box, LV_OBJ_FLAG_HIDDEN);
      } else {
        lv_obj_add_flag(cols[i].box, LV_OBJ_FLAG_HIDDEN);
      }
    }
  }
  if (haveData) lv_obj_add_flag(waiting, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_clear_flag(waiting, LV_OBJ_FLAG_HIDDEN);
  const uint32_t now = millis();
  for (int i = 0; i < n; i++) {
    const MixerFader& f = i == 0 ? last.master : last.apps[i - 1];
    Col& c = cols[i];
    c.id = f.id;
    setText(c.name, c.nameCache, sizeof c.nameCache, i == 0 ? "Master" : f.name.c_str());
    if (!c.dragging && static_cast<int32_t>(now - c.guardUntil) >= 0) {
      c.vol = f.vol;
      if (lv_slider_get_value(c.slider) != f.vol) lv_slider_set_value(c.slider, f.vol, LV_ANIM_OFF);
      showPercent(c, f.vol);
    }
    if (static_cast<int32_t>(now - c.muteGuardUntil) >= 0 && c.muted != f.mute) {
      c.muted = f.mute;
      paint(c, i);
    }
  }
}

}  // namespace

void mixerInit(UiEventFn eventSink) { sink = eventSink; }

void mixerForget() {
  built = false;
  shownCount = 0;
  waiting = nullptr;
  for (Col& c : cols) c = Col();
}

void mixerBuild(lv_obj_t* tile) {
  mixerForget();
  lv_obj_t* root = lv_obj_create(tile);
  lv_obj_remove_style_all(root);
  lv_obj_set_size(root, lv_obj_get_width(tile), lv_obj_get_height(tile));
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(root, lv_color_hex(kBg), 0);
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);
  for (int i = 0; i < kCols; i++) buildCol(root, i);
  waiting = lv_label_create(root);
  lv_obj_set_style_text_font(waiting, &lv_font_montserrat_16, 0);
  lv_obj_set_style_text_color(waiting, lv_color_hex(kMutedText), 0);
  lv_label_set_text(waiting, "Waiting for the PC...");
  lv_obj_center(waiting);
  built = true;
  apply();
}

void mixerSet(const MixerInfo& info) {
  last = info;
  haveData = true;
  apply();
}
