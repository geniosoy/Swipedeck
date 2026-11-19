#include "ui.h"

#include <ArduinoJson.h>
#include <lvgl.h>

#include <vector>

#include "audio.h"
#include "deck_model.h"
#include "nowplaying.h"
#include "stats.h"

namespace {

constexpr int kHeaderH = 30;
constexpr int kPad = 6;  // outer padding of the key grid
int keyGap = 6;     // gap between keys (user setting)
int keyRadius = 12; // corner radius of the keys (user setting)
bool wrapDesktops = false;  // swiping past the first / last desktop continues on the other end (user setting)

// Key sound is played on CLICKED (release without scrolling) so that starting a swipe on top of a
// key does not tick. Switch to LV_EVENT_PRESSED for an instant tick at touch-down.
constexpr lv_event_code_t kKeySoundEvent = LV_EVENT_CLICKED;

struct DesktopUi {
  lv_obj_t* tile = nullptr;
  std::vector<lv_obj_t*> pages;  // lazily created page containers (nullptr until first visit)
  std::vector<int> stack;        // visited pages, back = visible one (stack[0] == 0, the root)
};

Deck deck;
std::vector<DesktopUi> uis;
lv_obj_t* tv = nullptr;
// The top bar is one fixed object on the screen (it does not scroll with the desktops); it shows the
// title / back button of the active desktop and the desktop dots.
lv_obj_t* hdrTitle = nullptr;
lv_obj_t* hdrBack = nullptr;
std::vector<lv_obj_t*> hdrDots;
UiEventFn sink = nullptr;
int activeDesktop = 0;

struct IconEntry {
  const char* name;
  const char* symbol;
};
const IconEntry kIcons[] = {
    {"audio", LV_SYMBOL_AUDIO},       {"video", LV_SYMBOL_VIDEO},       {"list", LV_SYMBOL_LIST},
    {"ok", LV_SYMBOL_OK},             {"close", LV_SYMBOL_CLOSE},       {"power", LV_SYMBOL_POWER},
    {"settings", LV_SYMBOL_SETTINGS}, {"home", LV_SYMBOL_HOME},         {"download", LV_SYMBOL_DOWNLOAD},
    {"upload", LV_SYMBOL_UPLOAD},     {"refresh", LV_SYMBOL_REFRESH},   {"mute", LV_SYMBOL_MUTE},
    {"volume_down", LV_SYMBOL_VOLUME_MID}, {"volume_up", LV_SYMBOL_VOLUME_MAX}, {"image", LV_SYMBOL_IMAGE},
    {"edit", LV_SYMBOL_EDIT},         {"prev", LV_SYMBOL_PREV},         {"next", LV_SYMBOL_NEXT},
    {"stop", LV_SYMBOL_STOP},         {"play", LV_SYMBOL_PLAY},         {"pause", LV_SYMBOL_PAUSE},
    {"eject", LV_SYMBOL_EJECT},       {"left", LV_SYMBOL_LEFT},         {"right", LV_SYMBOL_RIGHT},
    {"up", LV_SYMBOL_UP},             {"down", LV_SYMBOL_DOWN},         {"plus", LV_SYMBOL_PLUS},
    {"minus", LV_SYMBOL_MINUS},       {"eye", LV_SYMBOL_EYE_OPEN},      {"eye_off", LV_SYMBOL_EYE_CLOSE},
    {"warning", LV_SYMBOL_WARNING},   {"shuffle", LV_SYMBOL_SHUFFLE},   {"loop", LV_SYMBOL_LOOP},
    {"directory", LV_SYMBOL_DIRECTORY}, {"file", LV_SYMBOL_FILE},       {"call", LV_SYMBOL_CALL},
    {"cut", LV_SYMBOL_CUT},           {"copy", LV_SYMBOL_COPY},         {"paste", LV_SYMBOL_PASTE},
    {"save", LV_SYMBOL_SAVE},         {"bell", LV_SYMBOL_BELL},         {"keyboard", LV_SYMBOL_KEYBOARD},
    {"gps", LV_SYMBOL_GPS},           {"wifi", LV_SYMBOL_WIFI},         {"bluetooth", LV_SYMBOL_BLUETOOTH},
    {"trash", LV_SYMBOL_TRASH},       {"usb", LV_SYMBOL_USB},           {"charge", LV_SYMBOL_CHARGE},
    {"battery", LV_SYMBOL_BATTERY_FULL}, {"drive", LV_SYMBOL_DRIVE},    {"backspace", LV_SYMBOL_BACKSPACE},
};

const char* iconSymbol(const String& name) {
  if (name.isEmpty()) return nullptr;
  for (const auto& e : kIcons) {
    if (name == e.name) return e.symbol;
  }
  return LV_SYMBOL_WARNING;  // unknown icon name: make the typo visible
}

void emit(JsonDocument& doc) {
  if (!sink) return;
  String s;
  serializeJson(doc, s);
  sink(s.c_str());
}

uint32_t pack(int d, int p, int k) { return (static_cast<uint32_t>(d) << 16) | (static_cast<uint32_t>(p) << 8) | static_cast<uint32_t>(k); }

void buildPage(int d, int p);

// Title, back button and dots of the fixed top bar for the active desktop.
void refreshHeader() {
  if (activeDesktop >= static_cast<int>(uis.size())) return;
  const DesktopUi& u = uis[activeDesktop];
  const DeckDesktop& dd = deck.desktops[activeDesktop];
  const bool live = dd.kind != DeskKind::Keys;  // now playing / stats: just a title, no pages
  const bool root = live || u.stack.size() <= 1;
  if (root) lv_obj_add_flag(hdrBack, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_clear_flag(hdrBack, LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text(hdrTitle, live ? dd.name.c_str() : dd.pages[u.stack.back()].title.c_str());
  lv_obj_align(hdrTitle, LV_ALIGN_LEFT_MID, root ? 10 : 78, 0);
  for (size_t i = 0; i < hdrDots.size(); i++) {
    const bool on = static_cast<int>(i) == activeDesktop;
    lv_obj_set_style_bg_color(hdrDots[i], lv_color_hex(on ? 0xFFFFFF : 0x55657A), 0);
  }
}

// Makes page `p` the visible page of desktop `d`.
void showPage(int d, int p) {
  DesktopUi& u = uis[d];
  if (!u.pages[p]) buildPage(d, p);
  for (size_t i = 0; i < u.pages.size(); i++) {
    if (!u.pages[i]) continue;
    if (static_cast<int>(i) == p) lv_obj_clear_flag(u.pages[i], LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(u.pages[i], LV_OBJ_FLAG_HIDDEN);
  }
  if (d == activeDesktop) refreshHeader();
}

void emitNav(int d, int p) {
  JsonDocument doc;
  doc["type"] = "nav";
  doc["desktop"] = d;
  doc["page"] = p;
  emit(doc);
}

void goBack(int d) {
  DesktopUi& u = uis[d];
  if (u.stack.size() <= 1) return;
  u.stack.pop_back();
  showPage(d, u.stack.back());
  emitNav(d, u.stack.back());
}

void onBack(lv_event_t*) {
  audioPlay(Sound::Back);
  goBack(activeDesktop);
}

void onKey(lv_event_t* e) {
  const uint32_t u = static_cast<uint32_t>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
  const int d = u >> 16, p = (u >> 8) & 0xFF, k = u & 0xFF;
  const DeckKey& key = deck.desktops[d].pages[p].keys[k];
  if (key.folder >= 0) {
    audioPlay(Sound::Nav);
    uis[d].stack.push_back(key.folder);
    showPage(d, key.folder);
    emitNav(d, key.folder);
    return;
  }
  audioPlay(Sound::Tap);
  JsonDocument doc;
  doc["type"] = "press";
  doc["desktop"] = d;
  doc["page"] = p;
  doc["key"] = k;
  doc["id"] = key.id;
  emit(doc);
}

void onDesktopChanged(lv_event_t*) {
  lv_obj_t* act = lv_tileview_get_tile_act(tv);  // NULL until the first layout
  if (!act) return;
  for (size_t i = 0; i < uis.size(); i++) {
    if (uis[i].tile == act && static_cast<int>(i) != activeDesktop) {
      activeDesktop = static_cast<int>(i);
      refreshHeader();  // no sound on swipe (by request)
      JsonDocument doc;
      doc["type"] = "desktop";
      doc["index"] = activeDesktop;
      emit(doc);
    }
  }
}

// Endless scrolling. The tileview cannot scroll past its first / last tile, so a swipe that does not move anything
// ends up as a gesture on the screen (LVGL sends it there while the finger is still down). The desktop at the other
// end is moved next to the current one for a moment, the view slides over to it, and then it goes back to its real
// place and becomes the active desktop. (LVGL only updates its active tile when a scroll ends with the finger up, so
// that part is done here.)
struct WrapSlide {
  bool running = false;
  int target = 0;
  lv_coord_t realX = 0;
} wrapSlide;

// lv_obj_scroll_to_x would clamp to the scrollable area, which does not include the moved desktop on its left side.
void wrapStep(void*, int32_t x) { _lv_obj_scroll_by_raw(tv, lv_obj_get_scroll_x(tv) - x, 0); }

void wrapDone(lv_anim_t*) {
  if (!wrapSlide.running) return;
  wrapSlide.running = false;
  lv_obj_set_x(uis[wrapSlide.target].tile, wrapSlide.realX);
  lv_obj_update_layout(tv);  // set_x only takes effect at the next layout pass; set_tile reads the tile's position
  lv_obj_set_tile(tv, uis[wrapSlide.target].tile, LV_ANIM_OFF);  // the tile is the active one, scroll limits as its own
  onDesktopChanged(nullptr);                                      // dots, title, event to the PC
}

void wrapCancel() {
  lv_anim_del(tv, reinterpret_cast<lv_anim_exec_xcb_t>(wrapStep));
  wrapSlide.running = false;
}

// `dir` is the way the finger moved. Returns false when nothing wraps (not at an end, option off, already sliding).
bool startWrap(lv_dir_t dir, bool fromTouch) {
  const int n = static_cast<int>(uis.size());
  if (!wrapDesktops || n < 2 || wrapSlide.running) return false;
  int target = -1;
  if (dir == LV_DIR_RIGHT && activeDesktop == 0) target = n - 1;  // finger moves right = towards the previous one
  else if (dir == LV_DIR_LEFT && activeDesktop == n - 1) target = 0;
  if (target < 0) return false;
  if (fromTouch) lv_indev_wait_release(lv_indev_get_act());  // the rest of this drag must not scroll anything

  const lv_coord_t w = lv_obj_get_content_width(tv);
  const lv_coord_t curX = lv_obj_get_x(uis[activeDesktop].tile);
  const lv_coord_t nextX = dir == LV_DIR_RIGHT ? curX - w : curX + w;  // right beside the current desktop
  wrapSlide.running = true;
  wrapSlide.target = target;
  wrapSlide.realX = lv_obj_get_x(uis[target].tile);
  lv_obj_set_x(uis[target].tile, nextX);
  lv_obj_update_layout(tv);
  lv_obj_set_scroll_dir(tv, LV_DIR_HOR);

  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, tv);
  lv_anim_set_exec_cb(&a, reinterpret_cast<lv_anim_exec_xcb_t>(wrapStep));
  lv_anim_set_values(&a, curX, nextX);
  lv_anim_set_time(&a, 260);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
  lv_anim_set_ready_cb(&a, wrapDone);
  lv_anim_start(&a);
  return true;
}

void onGesture(lv_event_t*) { startWrap(lv_indev_get_gesture_dir(lv_indev_get_act()), true); }

void buildPage(int d, int p) {
  DesktopUi& u = uis[d];
  const DeckPage& page = deck.desktops[d].pages[p];
  const int w = lv_obj_get_width(u.tile);
  const int h = lv_obj_get_height(u.tile);  // the tileview already starts below the fixed top bar

  lv_obj_t* cont = lv_obj_create(u.tile);
  u.pages[p] = cont;
  lv_obj_remove_style_all(cont);
  lv_obj_set_size(cont, w, h);
  lv_obj_set_pos(cont, 0, 0);
  lv_obj_clear_flag(cont, LV_OBJ_FLAG_SCROLLABLE);

  const int keyW = (w - 2 * kPad - (page.cols - 1) * keyGap) / page.cols;
  const int keyH = (h - 2 * kPad - (page.rows - 1) * keyGap) / page.rows;
  const lv_font_t* iconFont = keyH >= 86 ? &lv_font_montserrat_32 : keyH >= 62 ? &lv_font_montserrat_24 : &lv_font_montserrat_20;
  const lv_font_t* textFont = keyH >= 86 ? &lv_font_montserrat_16 : &lv_font_montserrat_14;

  for (int k = 0; k < page.cols * page.rows; k++) {
    const DeckKey& key = page.keys[k];
    if (key.empty) continue;
    lv_obj_t* b = lv_btn_create(cont);
    lv_obj_set_size(b, keyW, keyH);
    lv_obj_set_pos(b, kPad + (k % page.cols) * (keyW + keyGap), kPad + (k / page.cols) * (keyH + keyGap));
    lv_obj_set_style_radius(b, keyRadius, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_set_style_border_width(b, 0, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(key.color), 0);
    lv_obj_set_style_bg_color(b, lv_color_lighten(lv_color_hex(key.color), 70), LV_STATE_PRESSED);
    lv_obj_set_style_pad_all(b, 2, 0);
    lv_obj_set_style_pad_row(b, 0, 0);
    lv_obj_set_flex_flow(b, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(b, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_add_event_cb(b, onKey, kKeySoundEvent, reinterpret_cast<void*>(static_cast<intptr_t>(pack(d, p, k))));

    if (const char* sym = iconSymbol(key.icon)) {
      lv_obj_t* il = lv_label_create(b);
      lv_obj_set_style_text_font(il, iconFont, 0);
      lv_label_set_text(il, sym);
    }
    if (!key.label.isEmpty()) {
      lv_obj_t* tl = lv_label_create(b);
      lv_obj_set_style_text_font(tl, textFont, 0);
      lv_obj_set_width(tl, keyW - 8);
      lv_obj_set_style_text_align(tl, LV_TEXT_ALIGN_CENTER, 0);
      lv_label_set_long_mode(tl, LV_LABEL_LONG_DOT);
      lv_label_set_text(tl, key.label.c_str());
    }
    if (key.folder >= 0) {  // small arrow in the corner marks a folder
      lv_obj_t* arrow = lv_label_create(b);
      lv_obj_add_flag(arrow, LV_OBJ_FLAG_FLOATING);
      lv_obj_set_style_text_font(arrow, &lv_font_montserrat_14, 0);
      lv_obj_set_style_text_opa(arrow, LV_OPA_70, 0);
      lv_label_set_text(arrow, LV_SYMBOL_RIGHT);
      lv_obj_align(arrow, LV_ALIGN_TOP_RIGHT, -3, 1);
    }
  }
}

void buildDesktops() {
  const int n = static_cast<int>(deck.desktops.size());
  npForget();  // the old widgets are gone (lv_obj_clean of the tileview)
  statsForget();
  uis.clear();
  uis.resize(n);
  activeDesktop = 0;
  for (int d = 0; d < n; d++) {
    DesktopUi& u = uis[d];
    u.tile = lv_tileview_add_tile(tv, d, 0, static_cast<lv_dir_t>((d > 0 ? LV_DIR_LEFT : 0) | (d < n - 1 ? LV_DIR_RIGHT : 0)));
    u.pages.assign(deck.desktops[d].pages.size(), nullptr);
    u.stack = {0};

    if (deck.desktops[d].kind == DeskKind::NowPlaying) {
      npBuild(u.tile, deck.desktops[d].np);
      continue;
    }
    if (deck.desktops[d].kind == DeskKind::Stats) {
      statsBuild(u.tile, deck.desktops[d].stats);
      continue;
    }
    showPage(d, 0);
  }

  // Desktop indicator dots (right side of the fixed bar), only with more than one desktop.
  for (lv_obj_t* dot : hdrDots) lv_obj_del(dot);
  hdrDots.clear();
  if (n > 1) {
    for (int i = 0; i < n; i++) {
      lv_obj_t* dot = lv_obj_create(lv_obj_get_parent(hdrTitle));
      lv_obj_remove_style_all(dot);
      lv_obj_set_size(dot, 8, 8);
      lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
      lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
      lv_obj_align(dot, LV_ALIGN_RIGHT_MID, -10 - (n - 1 - i) * 14, 0);
      hdrDots.push_back(dot);
    }
  }
  activeDesktop = 0;
  refreshHeader();
  lv_obj_set_tile(tv, uis[0].tile, LV_ANIM_OFF);
  JsonDocument shown;  // tell the PC which desktop is showing (the layout reload resets it to the first)
  shown["type"] = "desktop";
  shown["index"] = 0;
  emit(shown);
}

}  // namespace

void uiInit(UiEventFn eventSink) {
  sink = eventSink;
  npInit(eventSink);
  lv_obj_t* scr = lv_scr_act();
  lv_obj_set_scrollbar_mode(scr, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0x0B1220), 0);
  lv_obj_set_style_text_color(scr, lv_color_hex(0xE6EDF7), 0);
  // Fixed top bar (never scrolls), then the tileview with the desktops below it.
  lv_obj_t* hdr = lv_obj_create(scr);
  lv_obj_remove_style_all(hdr);
  lv_obj_set_size(hdr, lv_obj_get_width(scr), kHeaderH);
  lv_obj_set_pos(hdr, 0, 0);
  lv_obj_set_style_bg_opa(hdr, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(hdr, lv_color_hex(0x111A26), 0);
  lv_obj_clear_flag(hdr, LV_OBJ_FLAG_SCROLLABLE);

  hdrTitle = lv_label_create(hdr);
  lv_obj_set_style_text_font(hdrTitle, &lv_font_montserrat_16, 0);
  lv_obj_set_style_text_color(hdrTitle, lv_color_white(), 0);

  hdrBack = lv_btn_create(hdr);
  lv_obj_set_size(hdrBack, 64, 24);
  lv_obj_align(hdrBack, LV_ALIGN_LEFT_MID, 4, 0);
  lv_obj_set_style_radius(hdrBack, 8, 0);
  lv_obj_set_style_shadow_width(hdrBack, 0, 0);
  lv_obj_set_style_bg_color(hdrBack, lv_color_hex(0x2A3A4D), 0);
  lv_obj_set_style_bg_color(hdrBack, lv_color_hex(0x4A6078), LV_STATE_PRESSED);
  lv_obj_set_style_pad_all(hdrBack, 0, 0);
  lv_obj_add_event_cb(hdrBack, onBack, kKeySoundEvent, nullptr);
  lv_obj_t* bl = lv_label_create(hdrBack);
  lv_label_set_text(bl, LV_SYMBOL_LEFT " Back");
  lv_obj_center(bl);

  tv = lv_tileview_create(scr);
  lv_obj_set_size(tv, lv_obj_get_width(scr), lv_obj_get_height(scr) - kHeaderH);
  lv_obj_set_pos(tv, 0, kHeaderH);
  lv_obj_set_scrollbar_mode(tv, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_opa(tv, LV_OPA_TRANSP, 0);
  lv_obj_add_event_cb(tv, onDesktopChanged, LV_EVENT_VALUE_CHANGED, nullptr);
  lv_obj_add_event_cb(scr, onGesture, LV_EVENT_GESTURE, nullptr);
}

void uiSetWrap(bool on) { wrapDesktops = on; }

void uiScrollInfo(int& scrollX, int& tileX, int& tileCount, bool& sliding) {
  scrollX = lv_obj_get_scroll_x(tv);
  lv_obj_t* act = lv_tileview_get_tile_act(tv);
  tileX = act ? lv_obj_get_x(act) : -9999;
  tileCount = static_cast<int>(uis.size());
  sliding = wrapSlide.running;
}

bool uiSwipe(bool fingerRight) { return startWrap(fingerRight ? LV_DIR_RIGHT : LV_DIR_LEFT, false); }

bool uiLoadLayout(const char* json, String& err) {
  Deck fresh;
  if (!deckParse(json, fresh, err)) return false;
  wrapCancel();  // a sliding desktop is about to be deleted
  lv_obj_clean(tv);
  uis.clear();
  deck = std::move(fresh);
  buildDesktops();
  return true;
}

int uiActiveDesktop() { return activeDesktop; }

void uiShowDesktop(int index, bool animate) {
  if (index < 0 || index >= static_cast<int>(uis.size())) return;
  lv_obj_set_tile(tv, uis[index].tile, animate ? LV_ANIM_ON : LV_ANIM_OFF);
}

int uiDesktopCount() { return static_cast<int>(uis.size()); }

bool uiSetKeyLook(int radius, int gap) {
  radius = constrain(radius, 0, 24);
  gap = constrain(gap, 2, 12);
  if (radius == keyRadius && gap == keyGap) return false;
  keyRadius = radius;
  keyGap = gap;
  if (deck.desktops.empty()) return true;  // nothing built yet; the next layout uses the new look
  // Only the key pages are rebuilt (their containers are deleted and the visible page is built again, the
  // others lazily when they are opened); Now playing / stats screens keep their state.
  for (size_t d = 0; d < uis.size(); d++) {
    if (deck.desktops[d].kind != DeskKind::Keys) continue;
    DesktopUi& u = uis[d];
    for (lv_obj_t*& page : u.pages) {
      if (page) lv_obj_del(page);
      page = nullptr;
    }
    showPage(static_cast<int>(d), u.stack.back());
  }
  return true;
}

void uiGoBack() {
  if (activeDesktop < static_cast<int>(uis.size())) goBack(activeDesktop);
}
