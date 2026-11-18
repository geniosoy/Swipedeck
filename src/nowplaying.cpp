#include "nowplaying.h"

#include <esp_heap_caps.h>
#include <stdarg.h>

#include "audio.h"

namespace {

// ---- geometry (the tile is 320 x 210: the fixed top bar is outside of it) ----------------------
constexpr int kTileW = 320;
constexpr int kCoverX = 9, kCoverY = 9;
constexpr int kTextX = kCoverX + kCoverPx + 12;  // text column right of the cover
constexpr int kTextW = kTileW - kTextX - 9;
constexpr int kAlbumY = 8, kTitleY = 28, kTitleH = 68, kArtistY = 100, kTimeY = 134;
constexpr int kSliderY = 158;                    // thin progress line between content and control bar
constexpr int kBarY = 162, kBarH = 48;           // control bar
constexpr uint32_t kDefaultBg = 0x1B2535;
constexpr uint32_t kAccent = 0x1ED760;           // active shuffle / repeat

// Marquee: text that does not fit scrolls to its end, rests, scrolls back, then waits before the next pass.
constexpr uint32_t kMarqueeStartDelayMs = 2500;
constexpr uint32_t kMarqueeEndPauseMs = 1500;
constexpr uint32_t kMarqueeRepeatDelayMs = 2500;
constexpr uint32_t kMarqueeMsPerPx = 28;

UiEventFn sink = nullptr;

NpInfo info;
uint32_t stampMs = 0;         // millis() when info.posMs was valid
uint32_t seekGuardUntil = 0;  // ignore position updates from the PC briefly after the user seeked

struct Marquee {
  lv_obj_t* clip = nullptr;  // fixed-size window
  lv_obj_t* lbl = nullptr;   // single-line label, as wide as its text, slid inside the window
  int w = 0;
  String shown;
};

// widgets (stale after npForget until npBuild runs again)
lv_obj_t *root, *bar, *coverBox, *coverImg, *coverPlaceholder, *titleWrap, *timeLbl, *slider;
lv_obj_t *btnShuffle, *btnPrev, *btnPlay, *btnNext, *btnRepeat;
lv_obj_t *shuffleLbl, *playLbl, *repeatLbl, *repeatOneLbl;
Marquee albumM, titleM, artistM;
bool built = false;
bool dragging = false;

String shownTitle;
char shownTime[28];

// cover: two PSRAM buffers, one shown and one being filled
uint8_t* coverBuf[2] = {nullptr, nullptr};
int coverShown = -1;  // index of the displayed buffer, -1 = no cover
size_t coverTotal = 0, coverGot = 0;
lv_img_dsc_t coverDsc;
uint32_t bgColor = kDefaultBg, pendingBg = kDefaultBg;

void emitf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void emitf(const char* fmt, ...) {
  if (!sink) return;
  char buf[96];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  sink(buf);
}

// ---- marquee ---------------------------------------------------------------------------------

void setX(void* obj, int32_t x) { lv_obj_set_x(static_cast<lv_obj_t*>(obj), x); }

void marqueeCreate(Marquee& m, lv_obj_t* parent, const lv_font_t* font, uint32_t color, lv_opa_t opa, int x, int y,
                   int w) {
  m.w = w;
  m.shown = "\x01";  // never equal to real text
  m.clip = lv_obj_create(parent);
  lv_obj_remove_style_all(m.clip);
  lv_obj_set_pos(m.clip, x, y);
  lv_obj_set_size(m.clip, w, font->line_height + 2);
  lv_obj_clear_flag(m.clip, LV_OBJ_FLAG_SCROLLABLE);
  m.lbl = lv_label_create(m.clip);
  lv_obj_set_style_text_font(m.lbl, font, 0);
  lv_obj_set_style_text_color(m.lbl, lv_color_hex(color), 0);
  lv_obj_set_style_text_opa(m.lbl, opa, 0);
  lv_label_set_long_mode(m.lbl, LV_LABEL_LONG_WRAP);
  lv_obj_set_width(m.lbl, LV_SIZE_CONTENT);  // as wide as the text (single line)
  lv_obj_set_pos(m.lbl, 0, 0);
  lv_label_set_text(m.lbl, "");
}

void marqueeStop(Marquee& m) {
  if (!m.lbl) return;
  lv_anim_del(m.lbl, setX);
  lv_obj_set_x(m.lbl, 0);
}

void marqueeSet(Marquee& m, const String& text) {
  if (!m.lbl || m.shown == text) return;  // re-setting identical text would restart the scroll
  m.shown = text;
  marqueeStop(m);
  lv_label_set_text(m.lbl, text.c_str());
  lv_obj_update_layout(m.lbl);
  const int over = lv_obj_get_width(m.lbl) - m.w;
  if (over <= 0) return;  // fits: stays still
  const uint32_t t = max<uint32_t>(700, over * kMarqueeMsPerPx);
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, m.lbl);
  lv_anim_set_exec_cb(&a, setX);
  lv_anim_set_values(&a, 0, -over);
  lv_anim_set_time(&a, t);
  lv_anim_set_playback_time(&a, t);
  lv_anim_set_delay(&a, kMarqueeStartDelayMs);
  lv_anim_set_playback_delay(&a, kMarqueeEndPauseMs);
  lv_anim_set_repeat_delay(&a, kMarqueeRepeatDelayMs);
  lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
  lv_anim_start(&a);
}

// ---- state -> widgets ------------------------------------------------------------------------

int32_t currentPos() {
  int32_t p = info.posMs;
  if (info.state == NpState::Playing) p += static_cast<int32_t>((millis() - stampMs) * info.rate);
  if (info.durMs > 0 && p > info.durMs) p = info.durMs;
  return p < 0 ? 0 : p;
}

void fmtTime(int32_t ms, char* out, size_t n) {
  const int32_t s = ms / 1000;
  if (s >= 3600) snprintf(out, n, "%d:%02d:%02d", (int)(s / 3600), (int)(s / 60 % 60), (int)(s % 60));
  else snprintf(out, n, "%d:%02d", (int)(s / 60), (int)(s % 60));
}

// Big title: the largest font that fits in 2-3 lines; if even the smallest does not, one scrolling line.
void setTitle(const String& t) {
  if (shownTitle == t) return;
  shownTitle = t;
  struct Option {
    const lv_font_t* font;
    int maxLines;
  };
  const Option options[] = {{&lv_font_montserrat_28, 2}, {&lv_font_montserrat_24, 2}, {&lv_font_montserrat_20, 3}};
  for (const Option& o : options) {
    lv_point_t size;
    lv_txt_get_size(&size, t.c_str(), o.font, 0, 0, kTextW, LV_TEXT_FLAG_NONE);
    if (size.y <= o.maxLines * o.font->line_height) {
      marqueeStop(titleM);
      titleM.shown = "\x01";
      lv_obj_add_flag(titleM.clip, LV_OBJ_FLAG_HIDDEN);
      lv_obj_clear_flag(titleWrap, LV_OBJ_FLAG_HIDDEN);
      lv_obj_set_style_text_font(titleWrap, o.font, 0);
      lv_label_set_text(titleWrap, t.c_str());
      return;
    }
  }
  lv_obj_add_flag(titleWrap, LV_OBJ_FLAG_HIDDEN);
  lv_obj_clear_flag(titleM.clip, LV_OBJ_FLAG_HIDDEN);
  marqueeSet(titleM, t);
}

void applyTexts() {
  if (!built) return;
  if (info.state == NpState::None) {
    marqueeSet(albumM, "");
    setTitle("Nothing playing");
    marqueeSet(artistM, "Play something on your PC");
  } else {
    marqueeSet(albumM, info.album);
    setTitle(info.title.isEmpty() ? String("Unknown title") : info.title);
    marqueeSet(artistM, info.artist);
  }
}

void setEnabled(lv_obj_t* obj, bool on) {
  if (on) lv_obj_clear_state(obj, LV_STATE_DISABLED);
  else lv_obj_add_state(obj, LV_STATE_DISABLED);
}

void applyControls() {
  if (!built) return;
  lv_label_set_text(playLbl, info.state == NpState::Playing ? LV_SYMBOL_PAUSE : LV_SYMBOL_PLAY);
  setEnabled(btnPrev, info.canPrev);
  setEnabled(btnNext, info.canNext);
  setEnabled(btnPlay, info.state != NpState::None);
  setEnabled(btnShuffle, info.canShuffle);
  setEnabled(btnRepeat, info.canRepeat);
  lv_obj_set_style_text_color(shuffleLbl, lv_color_hex(info.shuffle ? kAccent : 0xFFFFFF), 0);
  lv_obj_set_style_text_color(repeatLbl, lv_color_hex(info.repeat ? kAccent : 0xFFFFFF), 0);
  if (info.repeat == 1) lv_obj_clear_flag(repeatOneLbl, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_add_flag(repeatOneLbl, LV_OBJ_FLAG_HIDDEN);
  setEnabled(slider, info.canSeek && info.durMs > 0);
  lv_slider_set_range(slider, 0, info.durMs > 0 ? info.durMs : 1);
}

void applyProgress() {
  if (!built) return;
  const int32_t pos = dragging ? lv_slider_get_value(slider) : currentPos();
  if (!dragging) lv_slider_set_value(slider, info.durMs > 0 ? pos : 0, LV_ANIM_OFF);
  char a[12], b[12], line[28];
  fmtTime(pos, a, sizeof a);
  if (info.durMs > 0) fmtTime(info.durMs, b, sizeof b);
  else strcpy(b, "--:--");
  snprintf(line, sizeof line, "%s / %s", a, b);
  if (strcmp(line, shownTime) != 0) {
    strcpy(shownTime, line);
    lv_label_set_text(timeLbl, line);
  }
}

void applyBackground() {
  if (!built) return;
  const lv_color_t c = lv_color_hex(bgColor);
  lv_obj_set_style_bg_color(root, c, 0);
  lv_obj_set_style_bg_color(bar, lv_color_darken(c, 90), 0);
  lv_obj_set_style_bg_color(coverBox, lv_color_lighten(c, 30), 0);
}

void applyCover() {
  if (!built) return;
  if (coverShown >= 0) {
    lv_img_cache_invalidate_src(&coverDsc);
    lv_img_set_src(coverImg, &coverDsc);
    lv_obj_clear_flag(coverImg, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(coverPlaceholder, LV_OBJ_FLAG_HIDDEN);
    lv_obj_invalidate(coverImg);
  } else {
    lv_obj_add_flag(coverImg, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(coverPlaceholder, LV_OBJ_FLAG_HIDDEN);
  }
  applyBackground();
}

void tick(lv_timer_t*) { applyProgress(); }

// ---- input -----------------------------------------------------------------------------------

void onTransport(lv_event_t* e) {
  const char* cmd = static_cast<const char*>(lv_event_get_user_data(e));
  audioPlay(Sound::Tap);
  emitf("{\"type\":\"media\",\"cmd\":\"%s\"}", cmd);
  if (strcmp(cmd, "toggle") == 0 && info.state != NpState::None) {
    // Optimistic: flip right away, the PC confirms (or corrects) within a moment.
    info.posMs = currentPos();
    stampMs = millis();
    info.state = info.state == NpState::Playing ? NpState::Paused : NpState::Playing;
    applyControls();
  }  // shuffle / repeat wait for the PC's answer so the board never shows a state the player does not have
}

void onSlider(lv_event_t* e) {
  switch (lv_event_get_code(e)) {
    case LV_EVENT_PRESSED:
      dragging = true;
      break;
    case LV_EVENT_VALUE_CHANGED:
      applyProgress();  // elapsed time follows the finger
      break;
    case LV_EVENT_RELEASED:
    case LV_EVENT_PRESS_LOST:
      if (!dragging) break;
      dragging = false;
      info.posMs = lv_slider_get_value(slider);
      stampMs = millis();
      seekGuardUntil = millis() + 1200;
      emitf("{\"type\":\"seek\",\"posMs\":%ld}", static_cast<long>(info.posMs));
      break;
    default:
      break;
  }
}

// ---- building --------------------------------------------------------------------------------

// A control is just its icon: no box. The touch area is the invisible w x h rectangle centered on (cx, cy);
// the icon dims while pressed and fades when the control is unavailable. The icon label is child 0.
lv_obj_t* makeIconButton(lv_obj_t* parent, const char* symbol, const lv_font_t* font, int cx, int cy, int w, int h,
                         const char* cmd) {
  lv_obj_t* b = lv_btn_create(parent);
  lv_obj_remove_style_all(b);  // no theme background, border, shadow or press animation
  lv_obj_set_size(b, w, h);
  lv_obj_set_pos(b, cx - w / 2, cy - h / 2);
  lv_obj_set_style_text_color(b, lv_color_white(), 0);
  lv_obj_set_style_text_opa(b, LV_OPA_50, LV_STATE_PRESSED);
  lv_obj_set_style_text_opa(b, LV_OPA_30, LV_STATE_DISABLED);
  lv_obj_add_event_cb(b, onTransport, LV_EVENT_CLICKED, const_cast<char*>(cmd));
  lv_obj_t* l = lv_label_create(b);
  lv_obj_set_style_text_font(l, font, 0);
  lv_label_set_text(l, symbol);
  lv_obj_center(l);
  return b;
}

}  // namespace

void npInit(UiEventFn eventSink) {
  sink = eventSink;
  lv_timer_create(tick, 250, nullptr);
}

void npForget() {
  built = false;
  dragging = false;
}

void npBuild(lv_obj_t* tile) {
  root = lv_obj_create(tile);
  lv_obj_remove_style_all(root);
  lv_obj_set_size(root, lv_obj_get_width(tile), lv_obj_get_height(tile));
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);

  bar = lv_obj_create(root);
  lv_obj_remove_style_all(bar);
  lv_obj_set_pos(bar, 0, kBarY);
  lv_obj_set_size(bar, kTileW, kBarH);
  lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
  lv_obj_clear_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

  // cover: placeholder box with a note symbol, image on top once a cover has arrived (square corners)
  coverBox = lv_obj_create(root);
  lv_obj_remove_style_all(coverBox);
  lv_obj_set_pos(coverBox, kCoverX, kCoverY);
  lv_obj_set_size(coverBox, kCoverPx, kCoverPx);
  lv_obj_set_style_bg_opa(coverBox, LV_OPA_COVER, 0);
  lv_obj_clear_flag(coverBox, LV_OBJ_FLAG_SCROLLABLE);
  coverPlaceholder = lv_label_create(coverBox);
  lv_obj_set_style_text_font(coverPlaceholder, &lv_font_montserrat_40, 0);
  lv_obj_set_style_text_color(coverPlaceholder, lv_color_white(), 0);
  lv_obj_set_style_text_opa(coverPlaceholder, LV_OPA_40, 0);
  lv_label_set_text(coverPlaceholder, LV_SYMBOL_AUDIO);
  lv_obj_center(coverPlaceholder);
  coverImg = lv_img_create(coverBox);
  lv_obj_set_pos(coverImg, 0, 0);
  lv_obj_add_flag(coverImg, LV_OBJ_FLAG_HIDDEN);

  // text column: album (small), title (big), artist, time
  marqueeCreate(albumM, root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_60, kTextX, kAlbumY, kTextW);
  titleWrap = lv_label_create(root);
  lv_obj_set_pos(titleWrap, kTextX, kTitleY);
  lv_obj_set_size(titleWrap, kTextW, kTitleH);
  lv_obj_set_style_text_color(titleWrap, lv_color_white(), 0);
  lv_label_set_long_mode(titleWrap, LV_LABEL_LONG_DOT);
  lv_label_set_text(titleWrap, "");
  marqueeCreate(titleM, root, &lv_font_montserrat_24, 0xFFFFFF, LV_OPA_COVER, kTextX, kTitleY + 20, kTextW);
  lv_obj_add_flag(titleM.clip, LV_OBJ_FLAG_HIDDEN);
  marqueeCreate(artistM, root, &lv_font_montserrat_16, 0xFFFFFF, LV_OPA_90, kTextX, kArtistY, kTextW);
  timeLbl = lv_label_create(root);
  lv_obj_set_pos(timeLbl, kTextX, kTimeY);
  lv_obj_set_style_text_font(timeLbl, &lv_font_montserrat_14, 0);
  lv_obj_set_style_text_color(timeLbl, lv_color_white(), 0);
  lv_obj_set_style_text_opa(timeLbl, LV_OPA_60, 0);
  lv_label_set_text(timeLbl, "");

  // thin progress line: draggable, the knob only appears while touching it
  slider = lv_slider_create(root);
  lv_obj_set_pos(slider, 0, kSliderY);
  lv_obj_set_size(slider, kTileW, 4);
  lv_obj_set_ext_click_area(slider, 14);
  lv_obj_clear_flag(slider, LV_OBJ_FLAG_SCROLL_CHAIN);  // dragging the bar must not swipe the desktop
  lv_obj_set_style_radius(slider, 0, LV_PART_MAIN);
  lv_obj_set_style_radius(slider, 0, LV_PART_INDICATOR);
  lv_obj_set_style_bg_color(slider, lv_color_white(), LV_PART_MAIN);
  lv_obj_set_style_bg_opa(slider, LV_OPA_30, LV_PART_MAIN);
  lv_obj_set_style_bg_color(slider, lv_color_white(), LV_PART_INDICATOR);
  lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_INDICATOR);
  lv_obj_set_style_bg_opa(slider, LV_OPA_TRANSP, LV_PART_KNOB);
  lv_obj_set_style_pad_all(slider, 0, LV_PART_KNOB);
  lv_obj_set_style_bg_color(slider, lv_color_white(), LV_PART_KNOB | LV_STATE_PRESSED);
  lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_KNOB | LV_STATE_PRESSED);
  lv_obj_set_style_pad_all(slider, 6, LV_PART_KNOB | LV_STATE_PRESSED);
  lv_obj_add_event_cb(slider, onSlider, LV_EVENT_ALL, nullptr);

  // control bar: shuffle, previous, play/pause, next, repeat (created after the slider so they win overlaps)
  const int cy = kBarY + kBarH / 2;
  btnShuffle = makeIconButton(root, LV_SYMBOL_SHUFFLE, &lv_font_montserrat_24, 32, cy, 64, kBarH, "shuffle");
  btnPrev = makeIconButton(root, LV_SYMBOL_PREV, &lv_font_montserrat_28, 96, cy, 64, kBarH, "prev");
  btnPlay = makeIconButton(root, LV_SYMBOL_PLAY, &lv_font_montserrat_40, 160, cy, 64, kBarH, "toggle");
  btnNext = makeIconButton(root, LV_SYMBOL_NEXT, &lv_font_montserrat_28, 224, cy, 64, kBarH, "next");
  btnRepeat = makeIconButton(root, LV_SYMBOL_LOOP, &lv_font_montserrat_24, 288, cy, 64, kBarH, "repeat");
  shuffleLbl = lv_obj_get_child(btnShuffle, 0);
  playLbl = lv_obj_get_child(btnPlay, 0);
  repeatLbl = lv_obj_get_child(btnRepeat, 0);
  repeatOneLbl = lv_label_create(btnRepeat);  // small "1" for repeat-one
  lv_obj_set_style_text_font(repeatOneLbl, &lv_font_montserrat_14, 0);
  lv_obj_set_style_text_color(repeatOneLbl, lv_color_hex(kAccent), 0);
  lv_label_set_text(repeatOneLbl, "1");
  lv_obj_align(repeatOneLbl, LV_ALIGN_CENTER, 14, 8);
  lv_obj_add_flag(repeatOneLbl, LV_OBJ_FLAG_HIDDEN);

  built = true;
  shownTitle = "\x01";
  shownTime[0] = 0;
  applyTexts();
  applyControls();
  applyProgress();
  applyCover();
}

void npSetInfo(const NpInfo& n) {
  const bool sameTrack = n.title == info.title && n.artist == info.artist && n.durMs == info.durMs;
  const bool guarded = millis() < seekGuardUntil && sameTrack && n.state == info.state;
  const int32_t keepPos = info.posMs;
  const uint32_t keepStamp = stampMs;
  info = n;
  if (guarded) {  // the user just seeked: our own position is newer than what the PC measured
    info.posMs = keepPos;
    stampMs = keepStamp;
  } else {
    stampMs = millis();
  }
  applyTexts();
  applyControls();
  applyProgress();
}

bool npCoverBegin(int px, size_t total, uint32_t bg) {
  if (px != kCoverPx || total != static_cast<size_t>(px) * px * 2) return false;
  for (auto& b : coverBuf) {
    if (!b) b = static_cast<uint8_t*>(heap_caps_malloc(total, MALLOC_CAP_SPIRAM));
    if (!b) return false;
  }
  coverTotal = total;
  coverGot = 0;
  pendingBg = bg;
  return true;
}

bool npCoverWrite(size_t off, const uint8_t* data, size_t len) {
  if (!coverBuf[0] || off != coverGot || off + len > coverTotal) return false;
  uint8_t* stage = coverBuf[coverShown == 0 ? 1 : 0];
  memcpy(stage + off, data, len);
  coverGot += len;
  return true;
}

bool npCoverEnd() {
  if (!coverBuf[0] || coverGot != coverTotal || coverTotal == 0) return false;
  coverShown = coverShown == 0 ? 1 : 0;
  memset(&coverDsc, 0, sizeof coverDsc);
  coverDsc.header.cf = LV_IMG_CF_TRUE_COLOR;
  coverDsc.header.w = kCoverPx;
  coverDsc.header.h = kCoverPx;
  coverDsc.data_size = coverTotal;
  coverDsc.data = coverBuf[coverShown];
  coverTotal = coverGot = 0;
  bgColor = pendingBg;
  applyCover();
  return true;
}

void npCoverClear() {
  coverShown = -1;
  coverTotal = coverGot = 0;
  bgColor = kDefaultBg;
  applyCover();
}
