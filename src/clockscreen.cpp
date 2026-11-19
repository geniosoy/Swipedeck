#include "clockscreen.h"

#include <time.h>

#include "audio.h"
#include "display_hal.h"
#include "ui.h"

namespace {

constexpr uint32_t kBg = 0x101722, kTrack = 0x2A3A4D, kBlue = 0x3B82F6, kGreen = 0x2E7D32, kAmber = 0xC77700, kRed = 0xB3261E;
constexpr uint32_t kMuted = 0x7F8FA6, kSoft = 0xC5D1E3;
constexpr int kTileW = 320;
constexpr uint32_t kPresetMinutes[4] = {1, 5, 10, 25};
constexpr uint32_t kRingEveryMs = 1500;
constexpr int kMaxRings = 12;  // about 18 seconds, then it gives up

// ---- time of day: told by the PC, counted here -------------------------------------------------------------

bool synced = false;
int64_t baseEpoch = 0;
uint32_t baseMs = 0;
int offsetMin = 0;

int64_t nowEpoch() { return baseEpoch + static_cast<int64_t>(static_cast<uint32_t>(millis() - baseMs) / 1000); }

// ---- the timer: independent of the screen ------------------------------------------------------------------

enum class TState : uint8_t { Idle, Running, Paused, Ringing };
TState tstate = TState::Idle;
uint32_t setMs = 5UL * 60UL * 1000UL;  // the chosen duration
uint32_t remainMs = setMs;             // what is left while Idle / Paused
uint32_t endAtMs = 0;                  // millis() at which a Running timer ends
uint32_t lastRingMs = 0;
int rings = 0;

uint32_t remainingNow() {
  if (tstate != TState::Running) return remainMs;
  const int32_t left = static_cast<int32_t>(endAtMs - millis());
  return left > 0 ? static_cast<uint32_t>(left) : 0;
}

void stopRinging() {
  tstate = TState::Idle;
  remainMs = setMs;
}

void tickTimer() {
  if (tstate == TState::Running && remainingNow() == 0) {
    tstate = TState::Ringing;
    rings = 0;
    lastRingMs = millis() - kRingEveryMs;  // the first ring comes at once
    displayWake();                         // the screen may be asleep
    uiShowClock();                         // and this is where the timer lives
  }
  if (tstate == TState::Ringing && millis() - lastRingMs >= kRingEveryMs) {
    lastRingMs = millis();
    audioPlay(Sound::Alarm);
    if (++rings >= kMaxRings) stopRinging();
  }
}

// ---- widgets -------------------------------------------------------------------------------------------------

ClockOptions opts;
bool built = false;
lv_obj_t *timeLbl, *secLbl, *ampmLbl, *dateLbl, *tempLbl, *condLbl, *rangeLbl, *placeLbl, *timerLbl, *startBtn, *startLbl, *resetBtn;
lv_obj_t* presetBtn[4];
WeatherInfo weather;
char cTime[12], cSec[6], cAmpm[4], cDate[20], cTemp[12], cCond[24], cRange[20], cPlace[28], cTimer[8], cStart[8];
uint32_t startColor = 0;

void setText(lv_obj_t* label, char* cache, size_t size, const char* text) {
  if (!label || strncmp(cache, text, size) == 0) return;
  strlcpy(cache, text, size);
  lv_label_set_text(label, text);
}

void setBtnColor(lv_obj_t* btn, uint32_t color) { lv_obj_set_style_bg_color(btn, lv_color_hex(color), 0); }

void refresh() {
  if (!built) return;
  static const char* const kDays[7] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
  static const char* const kMonths[12] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  char buf[28];

  // time of day
  if (synced) {
    const time_t t = static_cast<time_t>(nowEpoch() + static_cast<int64_t>(offsetMin) * 60);
    struct tm tmv;
    gmtime_r(&t, &tmv);
    if (opts.hour12) {
      const int h = tmv.tm_hour % 12 == 0 ? 12 : tmv.tm_hour % 12;
      snprintf(buf, sizeof buf, "%d:%02d", h, tmv.tm_min);
      setText(ampmLbl, cAmpm, sizeof cAmpm, tmv.tm_hour < 12 ? "AM" : "PM");
    } else {
      snprintf(buf, sizeof buf, "%02d:%02d", tmv.tm_hour, tmv.tm_min);
    }
    setText(timeLbl, cTime, sizeof cTime, buf);
    snprintf(buf, sizeof buf, "%02d", tmv.tm_sec);
    setText(secLbl, cSec, sizeof cSec, buf);
    snprintf(buf, sizeof buf, "%s %d %s", kDays[tmv.tm_wday % 7], tmv.tm_mday, kMonths[tmv.tm_mon % 12]);
    setText(dateLbl, cDate, sizeof cDate, buf);
  } else {
    setText(timeLbl, cTime, sizeof cTime, "--:--");
    setText(dateLbl, cDate, sizeof cDate, "Waiting for the PC");
  }

  // weather
  if (opts.weather) {
    if (weather.valid && weather.ok) {
      snprintf(buf, sizeof buf, "%d\xC2\xB0%c", weather.temp, weather.unit);
      setText(tempLbl, cTemp, sizeof cTemp, buf);
      setText(condLbl, cCond, sizeof cCond, weather.text.c_str());
      snprintf(buf, sizeof buf, "H %d\xC2\xB0  L %d\xC2\xB0", weather.hi, weather.lo);
      setText(rangeLbl, cRange, sizeof cRange, buf);
      setText(placeLbl, cPlace, sizeof cPlace, weather.place.c_str());
    } else {
      setText(tempLbl, cTemp, sizeof cTemp, "--");
      setText(condLbl, cCond, sizeof cCond, weather.valid ? "Unavailable" : "Weather...");
      setText(rangeLbl, cRange, sizeof cRange, "");
      setText(placeLbl, cPlace, sizeof cPlace, "");
    }
  }

  // timer
  if (opts.timer) {
    const uint32_t secs = (remainingNow() + 999) / 1000;
    snprintf(buf, sizeof buf, "%02u:%02u", static_cast<unsigned>(secs / 60 % 100), static_cast<unsigned>(secs % 60));
    setText(timerLbl, cTimer, sizeof cTimer, buf);
    const bool ringing = tstate == TState::Ringing;
    const bool blinkRed = ringing && ((millis() / 500) % 2 == 0);
    lv_obj_set_style_text_color(timerLbl, lv_color_hex(blinkRed ? 0xF04438 : tstate == TState::Running ? 0x1ED760 : 0xFFFFFF), 0);
    const char* startText = tstate == TState::Running ? "Pause" : tstate == TState::Paused ? "Resume" : ringing ? "Stop" : "Start";
    const uint32_t color = tstate == TState::Running ? kAmber : ringing ? kRed : kGreen;
    setText(startLbl, cStart, sizeof cStart, startText);
    if (color != startColor) {
      startColor = color;
      setBtnColor(startBtn, color);
    }
    for (int i = 0; i < 4; i++) setBtnColor(presetBtn[i], setMs == kPresetMinutes[i] * 60000UL ? kBlue : kTrack);
  }
}

void onStartStop(lv_event_t*) {
  audioPlay(Sound::Tap);
  switch (tstate) {
    case TState::Idle:
    case TState::Paused:
      if (remainMs == 0) remainMs = setMs;
      endAtMs = millis() + remainMs;
      tstate = TState::Running;
      break;
    case TState::Running:
      remainMs = remainingNow();
      tstate = TState::Paused;
      break;
    case TState::Ringing:
      stopRinging();
      break;
  }
  refresh();
}

void onReset(lv_event_t*) {
  audioPlay(Sound::Tap);
  tstate = TState::Idle;
  remainMs = setMs;
  refresh();
}

void onPreset(lv_event_t* e) {
  const int i = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
  audioPlay(Sound::Tap);
  setMs = kPresetMinutes[i] * 60000UL;
  tstate = TState::Idle;
  remainMs = setMs;
  refresh();
}

void tick(lv_timer_t*) {
  tickTimer();
  refresh();
}

lv_obj_t* makeLabel(lv_obj_t* parent, const lv_font_t* font, uint32_t color, int x, int y, int w, lv_text_align_t align = LV_TEXT_ALIGN_LEFT) {
  lv_obj_t* l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
  lv_obj_set_width(l, w);
  lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
  lv_obj_set_style_text_align(l, align, 0);
  lv_obj_set_pos(l, x, y);
  lv_label_set_text(l, "");
  return l;
}

lv_obj_t* makeButton(lv_obj_t* parent, int x, int y, int w, int h, uint32_t color, lv_event_cb_t cb, void* data, lv_obj_t** label,
                     const lv_font_t* font) {
  lv_obj_t* b = lv_btn_create(parent);
  lv_obj_remove_style_all(b);
  lv_obj_set_size(b, w, h);
  lv_obj_set_pos(b, x, y);
  lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(b, lv_color_hex(color), 0);
  lv_obj_set_style_bg_color(b, lv_color_lighten(lv_color_hex(color), 60), LV_STATE_PRESSED);
  lv_obj_set_style_radius(b, 10, 0);
  lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, data);
  lv_obj_t* l = lv_label_create(b);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, lv_color_white(), 0);
  lv_obj_center(l);
  if (label) *label = l;
  return b;
}

}  // namespace

void clockInit() { lv_timer_create(tick, 200, nullptr); }

void clockForget() {
  built = false;
  timeLbl = secLbl = ampmLbl = dateLbl = tempLbl = condLbl = rangeLbl = placeLbl = timerLbl = startBtn = startLbl = resetBtn = nullptr;
  for (lv_obj_t*& b : presetBtn) b = nullptr;
  cTime[0] = cSec[0] = cAmpm[0] = cDate[0] = cTemp[0] = cCond[0] = cRange[0] = cPlace[0] = cTimer[0] = cStart[0] = 0;
  startColor = 0;
}

void clockBuild(lv_obj_t* tile, const ClockOptions& options) {
  clockForget();
  opts = options;
  lv_obj_t* root = lv_obj_create(tile);
  lv_obj_remove_style_all(root);
  lv_obj_set_size(root, lv_obj_get_width(tile), lv_obj_get_height(tile));
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(root, lv_color_hex(kBg), 0);
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);

  const int yOff = opts.timer ? 0 : 36;       // without the timer the clock sits lower, nearer the middle
  const int x0 = opts.weather ? 16 : 62;      // without the weather the clock is centered

  timeLbl = makeLabel(root, &lv_font_montserrat_48, 0xFFFFFF, x0, 4 + yOff, 142);
  ampmLbl = makeLabel(root, &lv_font_montserrat_18, kMuted, x0 + 144, 8 + yOff, 40);
  secLbl = makeLabel(root, &lv_font_montserrat_24, kMuted, x0 + 144, 28 + yOff, 40);
  dateLbl = makeLabel(root, &lv_font_montserrat_18, kSoft, x0, 62 + yOff, 190);
  if (!opts.hour12) lv_obj_add_flag(ampmLbl, LV_OBJ_FLAG_HIDDEN);
  if (!opts.seconds) lv_obj_add_flag(secLbl, LV_OBJ_FLAG_HIDDEN);

  if (opts.weather) {
    tempLbl = makeLabel(root, &lv_font_montserrat_32, 0xFFFFFF, 206, 2 + yOff, 108);
    condLbl = makeLabel(root, &lv_font_montserrat_16, kSoft, 206, 40 + yOff, 108);
    lv_label_set_long_mode(condLbl, LV_LABEL_LONG_DOT);
    rangeLbl = makeLabel(root, &lv_font_montserrat_14, kMuted, 206, 61 + yOff, 108);
    placeLbl = makeLabel(root, &lv_font_montserrat_14, kMuted, 206, 79 + yOff, 108);
    lv_label_set_long_mode(placeLbl, LV_LABEL_LONG_DOT);
  }

  if (opts.timer) {
    lv_obj_t* line = lv_obj_create(root);
    lv_obj_remove_style_all(line);
    lv_obj_set_size(line, kTileW - 32, 2);
    lv_obj_set_pos(line, 16, 102);
    lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(line, lv_color_hex(kTrack), 0);

    constexpr int yT = 112;
    timerLbl = makeLabel(root, &lv_font_montserrat_40, 0xFFFFFF, 16, yT + 8, 140);
    for (int i = 0; i < 4; i++) {
      char text[8];
      snprintf(text, sizeof text, "%um", static_cast<unsigned>(kPresetMinutes[i]));
      lv_obj_t* label = nullptr;
      presetBtn[i] = makeButton(root, 158 + i * 40, yT, 34, 32, kTrack, onPreset, reinterpret_cast<void*>(static_cast<intptr_t>(i)), &label,
                                &lv_font_montserrat_14);
      lv_label_set_text(label, text);
    }
    startBtn = makeButton(root, 158, yT + 40, 92, 46, kGreen, onStartStop, nullptr, &startLbl, &lv_font_montserrat_18);
    lv_obj_t* resetLbl = nullptr;
    resetBtn = makeButton(root, 256, yT + 40, 56, 46, kTrack, onReset, nullptr, &resetLbl, &lv_font_montserrat_20);
    lv_label_set_text(resetLbl, LV_SYMBOL_REFRESH);
  }
  built = true;
  refresh();
}

void clockSync(int64_t epoch, int offset) {
  baseEpoch = epoch;
  baseMs = millis();
  offsetMin = offset;
  synced = true;
  refresh();
}

void clockSetWeather(const WeatherInfo& info) {
  weather = info;
  refresh();
}
