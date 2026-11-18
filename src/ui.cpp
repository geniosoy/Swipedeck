#include "ui.h"

#include <ArduinoJson.h>
#include <lvgl.h>

#include <vector>

#include "audio.h"
#include "deck_model.h"

namespace {

constexpr int kHeaderH = 30;
constexpr int kPad = 6;  // outer padding of the key grid
constexpr int kGap = 6;  // gap between keys

// Key sound is played on CLICKED (release without scrolling) so that starting a swipe on top of a
// key does not tick. Switch to LV_EVENT_PRESSED for an instant tick at touch-down.
constexpr lv_event_code_t kKeySoundEvent = LV_EVENT_CLICKED;

struct DesktopUi {
  lv_obj_t* tile = nullptr;
  lv_obj_t* backBtn = nullptr;
  lv_obj_t* title = nullptr;
  std::vector<lv_obj_t*> dots;   // one per desktop
  std::vector<lv_obj_t*> pages;  // lazily created page containers (nullptr until first visit)
  std::vector<int> stack;        // visited pages, back = visible one (stack[0] == 0, the root)
};

Deck deck;
std::vector<DesktopUi> uis;
lv_obj_t* tv = nullptr;
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

void updateDots() {
  for (auto& u : uis) {
    for (size_t i = 0; i < u.dots.size(); i++) {
      const bool on = static_cast<int>(i) == activeDesktop;
      lv_obj_set_style_bg_color(u.dots[i], lv_color_hex(on ? 0xFFFFFF : 0x55657A), 0);
    }
  }
}

// Makes page `p` the visible page of desktop `d` and refreshes the header.
void showPage(int d, int p) {
  DesktopUi& u = uis[d];
  if (!u.pages[p]) buildPage(d, p);
  for (size_t i = 0; i < u.pages.size(); i++) {
    if (!u.pages[i]) continue;
    if (static_cast<int>(i) == p) lv_obj_clear_flag(u.pages[i], LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(u.pages[i], LV_OBJ_FLAG_HIDDEN);
  }
  const bool root = u.stack.size() <= 1;
  if (root) lv_obj_add_flag(u.backBtn, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_clear_flag(u.backBtn, LV_OBJ_FLAG_HIDDEN);
  lv_label_set_text(u.title, deck.desktops[d].pages[p].title.c_str());
  lv_obj_align(u.title, LV_ALIGN_LEFT_MID, root ? 10 : 78, 0);
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

void onBack(lv_event_t* e) {
  audioPlay(Sound::Back);
  goBack(static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e))));
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
      updateDots();
      audioPlay(Sound::Swipe);
      JsonDocument doc;
      doc["type"] = "desktop";
      doc["index"] = activeDesktop;
      emit(doc);
    }
  }
}

void buildPage(int d, int p) {
  DesktopUi& u = uis[d];
  const DeckPage& page = deck.desktops[d].pages[p];
  const int w = lv_obj_get_width(u.tile);
  const int h = lv_obj_get_height(u.tile) - kHeaderH;

  lv_obj_t* cont = lv_obj_create(u.tile);
  u.pages[p] = cont;
  lv_obj_remove_style_all(cont);
  lv_obj_set_size(cont, w, h);
  lv_obj_set_pos(cont, 0, kHeaderH);
  lv_obj_clear_flag(cont, LV_OBJ_FLAG_SCROLLABLE);

  const int keyW = (w - 2 * kPad - (page.cols - 1) * kGap) / page.cols;
  const int keyH = (h - 2 * kPad - (page.rows - 1) * kGap) / page.rows;
  const lv_font_t* iconFont = keyH >= 86 ? &lv_font_montserrat_32 : keyH >= 62 ? &lv_font_montserrat_24 : &lv_font_montserrat_20;
  const lv_font_t* textFont = keyH >= 86 ? &lv_font_montserrat_16 : &lv_font_montserrat_14;

  for (int k = 0; k < page.cols * page.rows; k++) {
    const DeckKey& key = page.keys[k];
    if (key.empty) continue;
    lv_obj_t* b = lv_btn_create(cont);
    lv_obj_set_size(b, keyW, keyH);
    lv_obj_set_pos(b, kPad + (k % page.cols) * (keyW + kGap), kPad + (k / page.cols) * (keyH + kGap));
    lv_obj_set_style_radius(b, 12, 0);
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
  uis.clear();
  uis.resize(n);
  for (int d = 0; d < n; d++) {
    DesktopUi& u = uis[d];
    u.tile = lv_tileview_add_tile(tv, d, 0, static_cast<lv_dir_t>((d > 0 ? LV_DIR_LEFT : 0) | (d < n - 1 ? LV_DIR_RIGHT : 0)));
    u.pages.assign(deck.desktops[d].pages.size(), nullptr);
    u.stack = {0};

    lv_obj_t* hdr = lv_obj_create(u.tile);
    lv_obj_remove_style_all(hdr);
    lv_obj_set_size(hdr, lv_obj_get_width(u.tile), kHeaderH);
    lv_obj_set_style_bg_opa(hdr, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(hdr, lv_color_hex(0x111A26), 0);
    lv_obj_clear_flag(hdr, LV_OBJ_FLAG_SCROLLABLE);

    u.title = lv_label_create(hdr);
    lv_obj_set_style_text_font(u.title, &lv_font_montserrat_16, 0);

    u.backBtn = lv_btn_create(hdr);
    lv_obj_set_size(u.backBtn, 64, 24);
    lv_obj_align(u.backBtn, LV_ALIGN_LEFT_MID, 4, 0);
    lv_obj_set_style_radius(u.backBtn, 8, 0);
    lv_obj_set_style_shadow_width(u.backBtn, 0, 0);
    lv_obj_set_style_bg_color(u.backBtn, lv_color_hex(0x2A3A4D), 0);
    lv_obj_set_style_bg_color(u.backBtn, lv_color_hex(0x4A6078), LV_STATE_PRESSED);
    lv_obj_set_style_pad_all(u.backBtn, 0, 0);
    lv_obj_add_event_cb(u.backBtn, onBack, kKeySoundEvent, reinterpret_cast<void*>(static_cast<intptr_t>(d)));
    lv_obj_t* bl = lv_label_create(u.backBtn);
    lv_label_set_text(bl, LV_SYMBOL_LEFT " Back");
    lv_obj_center(bl);

    // desktop indicator dots (right side), only when there is more than one desktop
    if (n > 1) {
      for (int i = 0; i < n; i++) {
        lv_obj_t* dot = lv_obj_create(hdr);
        lv_obj_remove_style_all(dot);
        lv_obj_set_size(dot, 8, 8);
        lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
        lv_obj_align(dot, LV_ALIGN_RIGHT_MID, -10 - (n - 1 - i) * 14, 0);
        u.dots.push_back(dot);
      }
    }
    showPage(d, 0);
  }
  activeDesktop = 0;
  updateDots();
  lv_obj_set_tile(tv, uis[0].tile, LV_ANIM_OFF);
}

}  // namespace

void uiInit(UiEventFn eventSink) {
  sink = eventSink;
  lv_obj_t* scr = lv_scr_act();
  lv_obj_set_scrollbar_mode(scr, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0x0B1220), 0);
  lv_obj_set_style_text_color(scr, lv_color_hex(0xE6EDF7), 0);
  tv = lv_tileview_create(scr);
  lv_obj_set_size(tv, lv_obj_get_width(scr), lv_obj_get_height(scr));
  lv_obj_set_scrollbar_mode(tv, LV_SCROLLBAR_MODE_OFF);
  lv_obj_set_style_bg_opa(tv, LV_OPA_TRANSP, 0);
  lv_obj_add_event_cb(tv, onDesktopChanged, LV_EVENT_VALUE_CHANGED, nullptr);
}

bool uiLoadLayout(const char* json, String& err) {
  Deck fresh;
  if (!deckParse(json, fresh, err)) return false;
  lv_obj_clean(tv);
  uis.clear();
  deck = std::move(fresh);
  buildDesktops();
  return true;
}

void uiShowDesktop(int index) {
  if (index < 0 || index >= static_cast<int>(uis.size())) return;
  lv_obj_set_tile(tv, uis[index].tile, LV_ANIM_ON);
}

void uiGoBack() {
  if (activeDesktop < static_cast<int>(uis.size())) goBack(activeDesktop);
}
