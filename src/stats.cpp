#include "stats.h"

namespace {

constexpr uint32_t kBg = 0x101722;
constexpr uint32_t kTrack = 0x2A3A4D;
constexpr uint32_t kTile = 0x17212F;
constexpr uint32_t kGreen = 0x1ED760, kAmber = 0xF5A524, kRed = 0xF04438, kMuted = 0x7F8FA6;
constexpr int kTileW = 320, kTileH = 210;  // the area below the fixed top bar

StatsInfo info;
bool built = false;

// One item on screen. Which widgets are set depends on the style: arc (gauge), bar, value (big number), sub (small
// secondary text); network items use down / up instead of value / sub.
struct Slot {
  uint8_t item = 0;
  lv_obj_t* arc = nullptr;
  lv_obj_t* bar = nullptr;
  lv_obj_t* value = nullptr;
  lv_obj_t* sub = nullptr;
  lv_obj_t* down = nullptr;
  lv_obj_t* up = nullptr;
  char cValue[12] = "", cSub[32] = "", cDown[24] = "", cUp[24] = "";
};
std::vector<Slot> slots;

// What an item currently shows.
struct View {
  const char* name = "";
  bool net = false;
  int pct = -1;  // for arcs and bars; -1 = not available
  char value[12] = "--";
  char sub[32] = "";
  uint32_t subColor = kMuted;
  char down[24] = "", up[24] = "";
};

uint32_t loadColor(int pct) { return pct < 0 ? kMuted : pct < 60 ? kGreen : pct < 85 ? kAmber : kRed; }
uint32_t tempColor(int c) { return c < 0 ? kMuted : c < 70 ? kGreen : c < 85 ? kAmber : kRed; }
bool isNet(uint8_t item) { return item == kStatNet; }

// Sets the text only when it changed (every change invalidates and redraws the label).
void setText(lv_obj_t* obj, char* cache, size_t cacheSize, const char* text) {
  if (!obj || strncmp(cache, text, cacheSize) == 0) return;
  strlcpy(cache, text, cacheSize);
  lv_label_set_text(obj, text);
}

void fmtGB(int mb, char* out, size_t n) { snprintf(out, n, "%d.%d", mb / 1024, (mb % 1024) * 10 / 1024); }

void fmtRate(int kbs, char* out, size_t n) {
  if (kbs < 0) snprintf(out, n, "--");
  else if (kbs < 1000) snprintf(out, n, "%d KB/s", kbs);
  else snprintf(out, n, "%d.%d MB/s", kbs / 1024, (kbs % 1024) * 10 / 1024);
}

void setPct(View& v, int pct) {
  v.pct = pct;
  if (pct < 0) snprintf(v.value, sizeof v.value, "--");
  else snprintf(v.value, sizeof v.value, "%d%%", pct);
}

View viewOf(uint8_t item) {
  View v;
  char a[12], b[12];
  switch (item) {
    case kStatCpu:
      v.name = "CPU";
      setPct(v, info.cpu);
      if (info.cpuTemp >= 0) snprintf(v.sub, sizeof v.sub, "%d\xC2\xB0""C", info.cpuTemp);
      else snprintf(v.sub, sizeof v.sub, "temp n/a");
      v.subColor = info.cpuTemp >= 0 ? tempColor(info.cpuTemp) : kMuted;
      break;
    case kStatGpu:
      v.name = "GPU";
      setPct(v, info.gpu);
      if (info.gpuTemp >= 0 && info.gpuPower >= 0) snprintf(v.sub, sizeof v.sub, "%d\xC2\xB0""C  %dW", info.gpuTemp, info.gpuPower);
      else if (info.gpuTemp >= 0) snprintf(v.sub, sizeof v.sub, "%d\xC2\xB0""C", info.gpuTemp);
      else snprintf(v.sub, sizeof v.sub, "n/a");
      v.subColor = tempColor(info.gpuTemp);
      break;
    case kStatRam:
      v.name = "RAM";
      setPct(v, info.ramPct);
      if (info.ramUsedMB >= 0 && info.ramTotalMB > 0) {
        fmtGB(info.ramUsedMB, a, sizeof a);
        fmtGB(info.ramTotalMB, b, sizeof b);
        snprintf(v.sub, sizeof v.sub, "%s/%s GB", a, b);
      } else snprintf(v.sub, sizeof v.sub, "n/a");
      break;
    case kStatVram:
      v.name = "VRAM";
      if (info.vramTotalMB > 0 && info.vramUsedMB >= 0) {
        setPct(v, info.vramUsedMB * 100 / info.vramTotalMB);
        fmtGB(info.vramUsedMB, a, sizeof a);
        fmtGB(info.vramTotalMB, b, sizeof b);
        snprintf(v.sub, sizeof v.sub, "%s/%s GB", a, b);
      } else {
        setPct(v, -1);
        snprintf(v.sub, sizeof v.sub, "n/a");
      }
      break;
    case kStatDisk:
      v.name = "DISK";
      setPct(v, info.diskPct);
      if (info.diskPct >= 0) snprintf(v.sub, sizeof v.sub, "%d/%d GB", info.diskUsedGB, info.diskTotalGB);
      else snprintf(v.sub, sizeof v.sub, "n/a");
      break;
    default:  // network
      v.name = "NET";
      v.net = true;
      fmtRate(info.netDownKBs, a, sizeof a);
      snprintf(v.down, sizeof v.down, LV_SYMBOL_DOWNLOAD " %s", a);
      fmtRate(info.netUpKBs, a, sizeof a);
      snprintf(v.up, sizeof v.up, LV_SYMBOL_UPLOAD " %s", a);
      break;
  }
  return v;
}

void applySlot(Slot& s) {
  const View v = viewOf(s.item);
  if (s.arc) {
    lv_arc_set_value(s.arc, v.pct < 0 ? 0 : v.pct);
    lv_obj_set_style_arc_color(s.arc, lv_color_hex(loadColor(v.pct)), LV_PART_INDICATOR);
  }
  if (s.bar) {
    lv_bar_set_value(s.bar, v.pct < 0 ? 0 : v.pct, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(s.bar, lv_color_hex(loadColor(v.pct)), LV_PART_INDICATOR);
  }
  setText(s.value, s.cValue, sizeof s.cValue, v.value);
  if (s.sub) {
    setText(s.sub, s.cSub, sizeof s.cSub, v.sub);
    lv_obj_set_style_text_color(s.sub, lv_color_hex(v.subColor), 0);
  }
  setText(s.down, s.cDown, sizeof s.cDown, v.down);
  setText(s.up, s.cUp, sizeof s.cUp, v.up);
}

void applyAll() {
  if (!built) return;
  for (Slot& s : slots) applySlot(s);
}

// ---- widget helpers ---------------------------------------------------------------------------------

lv_obj_t* makeLabel(lv_obj_t* parent, const lv_font_t* font, uint32_t color, lv_opa_t opa, int x, int y, int w,
                    lv_text_align_t align, const char* text = "") {
  lv_obj_t* l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
  lv_obj_set_style_text_opa(l, opa, 0);
  lv_obj_set_style_text_align(l, align, 0);
  lv_obj_set_pos(l, x, y);
  lv_obj_set_width(l, w);
  lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
  lv_label_set_text(l, text);
  return l;
}

lv_obj_t* makeBar(lv_obj_t* parent, int x, int y, int w, int h) {
  lv_obj_t* b = lv_bar_create(parent);
  lv_obj_set_pos(b, x, y);
  lv_obj_set_size(b, w, h);
  lv_bar_set_range(b, 0, 100);
  lv_obj_set_style_bg_color(b, lv_color_hex(kTrack), LV_PART_MAIN);
  lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_MAIN);
  lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_INDICATOR);
  lv_obj_set_style_radius(b, h / 2, LV_PART_MAIN);
  lv_obj_set_style_radius(b, h / 2, LV_PART_INDICATOR);
  return b;
}

const char* itemName(uint8_t item) {
  static const char* names[] = {"CPU", "GPU", "RAM", "VRAM", "DISK", "NET"};
  return item < kStatItemCount ? names[item] : "";
}

// ---- style: gauges ----------------------------------------------------------------------------------
// Up to three round gauges, the other percentage items as bar rows below, the network as one line.

void buildGauges(lv_obj_t* root, const std::vector<uint8_t>& items) {
  std::vector<uint8_t> pct;
  bool net = false;
  for (uint8_t it : items) {
    if (isNet(it)) net = true;
    else pct.push_back(it);
  }
  const int ng = pct.size() < 3 ? pct.size() : 3;
  const int cxs[3][3] = {{160, 0, 0}, {100, 220, 0}, {54, 160, 266}};
  constexpr int kSize = 92;
  for (int i = 0; i < ng; i++) {
    const int cx = cxs[ng - 1][i];
    Slot s;
    s.item = pct[i];
    s.arc = lv_arc_create(root);
    lv_obj_set_size(s.arc, kSize, kSize);
    lv_obj_set_pos(s.arc, cx - kSize / 2, 8);
    lv_arc_set_rotation(s.arc, 135);
    lv_arc_set_bg_angles(s.arc, 0, 270);
    lv_arc_set_range(s.arc, 0, 100);
    lv_arc_set_value(s.arc, 0);
    lv_obj_remove_style(s.arc, nullptr, LV_PART_KNOB);
    lv_obj_clear_flag(s.arc, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_width(s.arc, 9, LV_PART_MAIN);
    lv_obj_set_style_arc_width(s.arc, 9, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(s.arc, lv_color_hex(kTrack), LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(s.arc, true, LV_PART_INDICATOR);
    s.value = makeLabel(root, &lv_font_montserrat_24, 0xFFFFFF, LV_OPA_COVER, cx - 36, 8 + 28, 72, LV_TEXT_ALIGN_CENTER);
    makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_60, cx - 36, 8 + 58, 72, LV_TEXT_ALIGN_CENTER, itemName(s.item));
    s.sub = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_COVER, cx - 52, 104, 104, LV_TEXT_ALIGN_CENTER);
    slots.push_back(s);
  }
  int rowY = ng > 0 ? 136 : 20;
  const int rowH = ng > 0 ? 24 : 44;
  for (size_t i = ng; i < pct.size(); i++, rowY += rowH) {
    Slot s;
    s.item = pct[i];
    makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_60, 10, rowY, 46, LV_TEXT_ALIGN_LEFT, itemName(s.item));
    s.bar = makeBar(root, 58, rowY + 5, 186, 8);
    s.sub = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 250, rowY, 62, LV_TEXT_ALIGN_RIGHT);
    slots.push_back(s);
  }
  if (net) {
    Slot s;
    s.item = kStatNet;
    if (pct.empty()) {  // nothing but the network: show it big
      s.down = makeLabel(root, &lv_font_montserrat_24, 0xFFFFFF, LV_OPA_COVER, 10, 60, 300, LV_TEXT_ALIGN_CENTER);
      s.up = makeLabel(root, &lv_font_montserrat_24, 0xFFFFFF, LV_OPA_COVER, 10, 110, 300, LV_TEXT_ALIGN_CENTER);
    } else {
      s.down = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 10, rowY, 150, LV_TEXT_ALIGN_LEFT);
      s.up = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 165, rowY, 145, LV_TEXT_ALIGN_LEFT);
    }
    slots.push_back(s);
  }
}

// ---- style: bars ------------------------------------------------------------------------------------
// One row per item: name, bar, small detail text, big percentage.

void buildBars(lv_obj_t* root, const std::vector<uint8_t>& items) {
  const int n = items.size();
  const int rowH = n <= 3 ? 58 : n == 4 ? 48 : n == 5 ? 40 : 34;
  const int top = (kTileH - rowH * n) / 2;
  const lv_font_t* nameFont = rowH >= 48 ? &lv_font_montserrat_16 : &lv_font_montserrat_14;
  const lv_font_t* valueFont = rowH >= 40 ? &lv_font_montserrat_20 : &lv_font_montserrat_16;
  for (int i = 0; i < n; i++) {
    Slot s;
    s.item = items[i];
    const int cy = top + i * rowH + rowH / 2;
    makeLabel(root, nameFont, 0xFFFFFF, LV_OPA_60, 10, cy - nameFont->line_height / 2, 52, LV_TEXT_ALIGN_LEFT, itemName(s.item));
    if (isNet(s.item)) {
      s.down = makeLabel(root, &lv_font_montserrat_16, 0xFFFFFF, LV_OPA_COVER, 64, cy - 9, 120, LV_TEXT_ALIGN_LEFT);
      s.up = makeLabel(root, &lv_font_montserrat_16, 0xFFFFFF, LV_OPA_COVER, 190, cy - 9, 120, LV_TEXT_ALIGN_LEFT);
    } else {
      s.bar = makeBar(root, 64, cy - 5, 100, 10);
      s.sub = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 170, cy - 8, 82, LV_TEXT_ALIGN_LEFT);
      s.value = makeLabel(root, valueFont, 0xFFFFFF, LV_OPA_COVER, 254, cy - valueFont->line_height / 2, 56, LV_TEXT_ALIGN_RIGHT);
    }
    slots.push_back(s);
  }
}

// ---- style: tiles -----------------------------------------------------------------------------------
// Two columns of rounded tiles (an odd last tile spans the full width): name and detail on top, big number, thin bar.

void buildTiles(lv_obj_t* root, const std::vector<uint8_t>& items) {
  const int n = items.size();
  const int rows = (n + 1) / 2;
  constexpr int pad = 6, gap = 6;
  const int tileW = (kTileW - 2 * pad - gap) / 2;
  const int tileH = (kTileH - 2 * pad - (rows - 1) * gap) / rows;
  const lv_font_t* valueFont = tileH >= 80 ? &lv_font_montserrat_28 : &lv_font_montserrat_24;
  for (int i = 0; i < n; i++) {
    Slot s;
    s.item = items[i];
    const bool full = (i == n - 1) && (n % 2 == 1);
    const int x = full ? pad : pad + (i % 2) * (tileW + gap);
    const int w = full ? kTileW - 2 * pad : tileW;
    const int y = pad + (i / 2) * (tileH + gap);
    lv_obj_t* bg = lv_obj_create(root);
    lv_obj_remove_style_all(bg);
    lv_obj_set_pos(bg, x, y);
    lv_obj_set_size(bg, w, tileH);
    lv_obj_set_style_radius(bg, 10, 0);
    lv_obj_set_style_bg_opa(bg, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(bg, lv_color_hex(kTile), 0);
    lv_obj_clear_flag(bg, LV_OBJ_FLAG_SCROLLABLE);
    makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_60, x + 8, y + 4, 56, LV_TEXT_ALIGN_LEFT, itemName(s.item));
    if (isNet(s.item)) {
      s.down = makeLabel(root, &lv_font_montserrat_16, 0xFFFFFF, LV_OPA_COVER, x + 8, y + 22, w - 16, LV_TEXT_ALIGN_LEFT);
      s.up = makeLabel(root, &lv_font_montserrat_16, 0xFFFFFF, LV_OPA_COVER, x + 8, y + 41, w - 16, LV_TEXT_ALIGN_LEFT);
    } else {
      s.sub = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_COVER, x + 60, y + 4, w - 68, LV_TEXT_ALIGN_RIGHT);
      s.value = makeLabel(root, valueFont, 0xFFFFFF, LV_OPA_COVER, x + 8, y + (tileH >= 80 ? 26 : 20), w - 16, LV_TEXT_ALIGN_LEFT);
      s.bar = makeBar(root, x + 8, y + tileH - 11, w - 16, 5);
    }
    slots.push_back(s);
  }
}

}  // namespace

void statsForget() {
  built = false;
  slots.clear();
}

void statsBuild(lv_obj_t* tile, const StatsOptions& options) {
  statsForget();
  lv_obj_t* root = lv_obj_create(tile);
  lv_obj_remove_style_all(root);
  lv_obj_set_size(root, lv_obj_get_width(tile), lv_obj_get_height(tile));
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(root, lv_color_hex(kBg), 0);
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);

  const std::vector<uint8_t>& items = options.items;
  switch (options.style) {
    case StatsStyle::Bars: buildBars(root, items); break;
    case StatsStyle::Tiles: buildTiles(root, items); break;
    default: buildGauges(root, items); break;
  }
  built = true;
  applyAll();
}

void statsSet(const StatsInfo& s) {
  info = s;
  applyAll();
}
