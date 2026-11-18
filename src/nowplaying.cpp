#include "nowplaying.h"

#include <esp_heap_caps.h>
#include <stdarg.h>

#include "audio.h"

namespace {

UiEventFn sink = nullptr;

NpInfo info;
uint32_t stampMs = 0;         // millis() when info.posMs was valid
uint32_t seekGuardUntil = 0;  // ignore position updates from the PC briefly after the user seeked

// widgets (nullptr until npBuild, and after npForget)
lv_obj_t *coverBox, *coverImg, *coverPlaceholder, *titleLbl, *artistLbl, *albumLbl;
lv_obj_t *btnPrev, *btnPlay, *btnNext, *playLbl, *slider, *elapsedLbl, *totalLbl;
bool built = false;
bool dragging = false;

// what the labels currently show (set text only on change: it would restart the marquee scroll)
String shownTitle, shownArtist, shownAlbum;
char shownElapsed[12], shownTotal[12];

// cover: two PSRAM buffers, one shown and one being filled
uint8_t* coverBuf[2] = {nullptr, nullptr};
int coverShown = -1;  // index of the displayed buffer, -1 = no cover
size_t coverTotal = 0, coverGot = 0;
lv_img_dsc_t coverDsc;

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

void setLabel(lv_obj_t* lbl, String& shown, const String& text) {
  if (shown == text) return;
  shown = text;
  lv_label_set_text(lbl, text.c_str());
}

void applyTexts() {
  if (!built) return;
  if (info.state == NpState::None) {
    setLabel(titleLbl, shownTitle, "Nothing playing");
    setLabel(artistLbl, shownArtist, "Play something on your PC");
    setLabel(albumLbl, shownAlbum, "");
  } else {
    setLabel(titleLbl, shownTitle, info.title.isEmpty() ? String("Unknown title") : info.title);
    setLabel(artistLbl, shownArtist, info.artist);
    setLabel(albumLbl, shownAlbum, info.album);
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
  const bool seekable = info.canSeek && info.durMs > 0;
  setEnabled(slider, seekable);
  lv_slider_set_range(slider, 0, info.durMs > 0 ? info.durMs : 1);
}

void applyProgress() {
  if (!built) return;
  const int32_t pos = dragging ? lv_slider_get_value(slider) : currentPos();
  if (!dragging) lv_slider_set_value(slider, info.durMs > 0 ? pos : 0, LV_ANIM_OFF);
  char buf[12];
  fmtTime(pos, buf, sizeof buf);
  if (strcmp(buf, shownElapsed) != 0) {
    strcpy(shownElapsed, buf);
    lv_label_set_text(elapsedLbl, buf);
  }
  if (info.durMs > 0) fmtTime(info.durMs, buf, sizeof buf);
  else strcpy(buf, "--:--");
  if (strcmp(buf, shownTotal) != 0) {
    strcpy(shownTotal, buf);
    lv_label_set_text(totalLbl, buf);
  }
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
  }
}

void onSlider(lv_event_t* e) {
  switch (lv_event_get_code(e)) {
    case LV_EVENT_PRESSED:
      dragging = true;
      break;
    case LV_EVENT_VALUE_CHANGED:
      applyProgress();  // elapsed label follows the finger
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

lv_obj_t* makeLabel(lv_obj_t* parent, const lv_font_t* font, uint32_t color, int x, int y, int w) {
  lv_obj_t* l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
  lv_obj_set_pos(l, x, y);
  lv_obj_set_width(l, w);
  lv_label_set_long_mode(l, LV_LABEL_LONG_SCROLL_CIRCULAR);
  lv_label_set_text(l, "");
  return l;
}

// A transport control is just its icon: no box. The touch area is the (invisible) w x h rectangle;
// the icon turns blue while pressed and fades when the control is unavailable.
lv_obj_t* makeButton(lv_obj_t* parent, const char* symbol, const lv_font_t* font, int x, int y, int w, int h,
                     const char* cmd, lv_obj_t** labelOut = nullptr) {
  lv_obj_t* b = lv_btn_create(parent);
  lv_obj_remove_style_all(b);  // no theme background, border, shadow or press animation
  lv_obj_set_pos(b, x, y);
  lv_obj_set_size(b, w, h);
  lv_obj_set_style_text_color(b, lv_color_white(), 0);
  lv_obj_set_style_text_color(b, lv_color_hex(0x5B9BFF), LV_STATE_PRESSED);
  lv_obj_set_style_text_opa(b, LV_OPA_30, LV_STATE_DISABLED);
  lv_obj_add_event_cb(b, onTransport, LV_EVENT_CLICKED, const_cast<char*>(cmd));
  lv_obj_t* l = lv_label_create(b);
  lv_obj_set_style_text_font(l, font, 0);
  lv_label_set_text(l, symbol);
  lv_obj_center(l);
  if (labelOut) *labelOut = l;
  return b;
}

void resetShown() {
  shownTitle = shownArtist = shownAlbum = String("\x01");  // never equal to real text
  shownElapsed[0] = shownTotal[0] = 0;
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
  lv_obj_t* root = lv_obj_create(tile);
  lv_obj_remove_style_all(root);
  lv_obj_set_size(root, lv_obj_get_width(tile), lv_obj_get_height(tile));
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);

  // cover: placeholder box with a note symbol, image on top once a cover has arrived
  coverBox = lv_obj_create(root);
  lv_obj_remove_style_all(coverBox);
  lv_obj_set_pos(coverBox, 8, 8);
  lv_obj_set_size(coverBox, kCoverPx, kCoverPx);
  lv_obj_set_style_bg_opa(coverBox, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(coverBox, lv_color_hex(0x1B2838), 0);
  lv_obj_clear_flag(coverBox, LV_OBJ_FLAG_SCROLLABLE);
  coverPlaceholder = lv_label_create(coverBox);
  lv_obj_set_style_text_font(coverPlaceholder, &lv_font_montserrat_40, 0);
  lv_obj_set_style_text_color(coverPlaceholder, lv_color_hex(0x55657A), 0);
  lv_label_set_text(coverPlaceholder, LV_SYMBOL_AUDIO);
  lv_obj_center(coverPlaceholder);
  coverImg = lv_img_create(coverBox);
  lv_obj_set_pos(coverImg, 0, 0);
  lv_obj_add_flag(coverImg, LV_OBJ_FLAG_HIDDEN);

  const int tx = 8 + kCoverPx + 10, tw = 320 - tx - 8;  // text/controls column right of the cover
  titleLbl = makeLabel(root, &lv_font_montserrat_20, 0xFFFFFF, tx, 8, tw);
  artistLbl = makeLabel(root, &lv_font_montserrat_16, 0xB8C4D6, tx, 40, tw);
  albumLbl = makeLabel(root, &lv_font_montserrat_14, 0x7F8FA6, tx, 64, tw);

  // three icons side by side across the column, vertically centered on the cover
  const int cy = 8 + kCoverPx / 2 + 14;
  btnPrev = makeButton(root, LV_SYMBOL_PREV, &lv_font_montserrat_28, tx, cy - 26, 42, 52, "prev");
  btnPlay = makeButton(root, LV_SYMBOL_PLAY, &lv_font_montserrat_40, tx + 42, cy - 30, 50, 60, "toggle", &playLbl);
  btnNext = makeButton(root, LV_SYMBOL_NEXT, &lv_font_montserrat_28, tx + 92, cy - 26, 42, 52, "next");

  elapsedLbl = makeLabel(root, &lv_font_montserrat_14, 0xB8C4D6, 8, 184, 46);
  lv_label_set_long_mode(elapsedLbl, LV_LABEL_LONG_CLIP);
  totalLbl = makeLabel(root, &lv_font_montserrat_14, 0xB8C4D6, 270, 184, 46);
  lv_label_set_long_mode(totalLbl, LV_LABEL_LONG_CLIP);
  lv_obj_set_style_text_align(totalLbl, LV_TEXT_ALIGN_RIGHT, 0);

  slider = lv_slider_create(root);
  lv_obj_set_pos(slider, 58, 188);
  lv_obj_set_size(slider, 204, 8);
  lv_obj_set_ext_click_area(slider, 14);  // generous touch target for a small bar
  lv_obj_clear_flag(slider, LV_OBJ_FLAG_SCROLL_CHAIN);  // dragging the bar must not swipe the desktop
  lv_obj_set_style_bg_color(slider, lv_color_hex(0x2A3A4D), LV_PART_MAIN);
  lv_obj_set_style_bg_color(slider, lv_color_hex(0x2F6FDB), LV_PART_INDICATOR);
  lv_obj_set_style_bg_color(slider, lv_color_white(), LV_PART_KNOB);
  lv_obj_set_style_pad_all(slider, 3, LV_PART_KNOB);
  lv_obj_set_style_bg_opa(slider, LV_OPA_60, LV_STATE_DISABLED);
  lv_obj_add_event_cb(slider, onSlider, LV_EVENT_ALL, nullptr);

  built = true;
  resetShown();
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

bool npCoverBegin(int px, size_t total) {
  if (px != kCoverPx || total != static_cast<size_t>(px) * px * 2) return false;
  for (auto& b : coverBuf) {
    if (!b) b = static_cast<uint8_t*>(heap_caps_malloc(total, MALLOC_CAP_SPIRAM));
    if (!b) return false;
  }
  coverTotal = total;
  coverGot = 0;
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
  applyCover();
  return true;
}

void npCoverClear() {
  coverShown = -1;
  coverTotal = coverGot = 0;
  applyCover();
}
