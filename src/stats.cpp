#include "stats.h"

namespace {

constexpr uint32_t kBg = 0x101722;
constexpr uint32_t kTrack = 0x2A3A4D;
constexpr uint32_t kGreen = 0x1ED760, kAmber = 0xF5A524, kRed = 0xF04438, kMuted = 0x7F8FA6;

struct Gauge {
  lv_obj_t* arc = nullptr;
  lv_obj_t* value = nullptr;
  lv_obj_t* sub = nullptr;
  char shownValue[12] = "";
  char shownSub[32] = "";
};

struct Row {  // label + thin bar + text on the right
  lv_obj_t* bar = nullptr;
  lv_obj_t* text = nullptr;
  char shown[32] = "";
};

StatsInfo info;
bool built = false;
Gauge gCpu, gGpu, gRam;
Row rVram, rDisk;
lv_obj_t *netDown = nullptr, *netUp = nullptr;
char shownDown[24] = "", shownUp[24] = "";

uint32_t loadColor(int pct) { return pct < 0 ? kMuted : pct < 60 ? kGreen : pct < 85 ? kAmber : kRed; }
uint32_t tempColor(int c) { return c < 0 ? kMuted : c < 70 ? kGreen : c < 85 ? kAmber : kRed; }

// Sets the text only when it changed (every change invalidates and redraws the label).
void setText(lv_obj_t* obj, char* cache, size_t cacheSize, const char* text) {
  if (strncmp(cache, text, cacheSize) == 0) return;
  strlcpy(cache, text, cacheSize);
  lv_label_set_text(obj, text);
}

void fmtGB(int mb, char* out, size_t n) { snprintf(out, n, "%d.%d", mb / 1024, (mb % 1024) * 10 / 1024); }

void fmtRate(int kbs, char* out, size_t n) {
  if (kbs < 0) snprintf(out, n, "--");
  else if (kbs < 1000) snprintf(out, n, "%d KB/s", kbs);
  else snprintf(out, n, "%d.%d MB/s", kbs / 1024, (kbs % 1024) * 10 / 1024);
}

void applyGauge(Gauge& g, int pct, const char* sub, uint32_t subColor) {
  char buf[12];
  if (pct < 0) snprintf(buf, sizeof buf, "--");
  else snprintf(buf, sizeof buf, "%d%%", pct);
  setText(g.value, g.shownValue, sizeof g.shownValue, buf);
  lv_arc_set_value(g.arc, pct < 0 ? 0 : pct);
  lv_obj_set_style_arc_color(g.arc, lv_color_hex(loadColor(pct)), LV_PART_INDICATOR);
  setText(g.sub, g.shownSub, sizeof g.shownSub, sub);
  lv_obj_set_style_text_color(g.sub, lv_color_hex(subColor), 0);
}

void applyRow(Row& r, int pct, const char* text) {
  lv_bar_set_value(r.bar, pct < 0 ? 0 : pct, LV_ANIM_OFF);
  lv_obj_set_style_bg_color(r.bar, lv_color_hex(loadColor(pct)), LV_PART_INDICATOR);
  setText(r.text, r.shown, sizeof r.shown, text);
}

void applyAll() {
  if (!built) return;
  char sub[32], a[12], b[12];

  // CPU: usage; temperature only when a sensor is available
  if (info.cpuTemp >= 0) snprintf(sub, sizeof sub, "%d\xC2\xB0""C", info.cpuTemp);
  else snprintf(sub, sizeof sub, "temp n/a");
  applyGauge(gCpu, info.cpu, sub, info.cpuTemp >= 0 ? tempColor(info.cpuTemp) : kMuted);

  // GPU: usage, temperature and power
  if (info.gpuTemp >= 0 && info.gpuPower >= 0) snprintf(sub, sizeof sub, "%d\xC2\xB0""C  %dW", info.gpuTemp, info.gpuPower);
  else if (info.gpuTemp >= 0) snprintf(sub, sizeof sub, "%d\xC2\xB0""C", info.gpuTemp);
  else snprintf(sub, sizeof sub, "n/a");
  applyGauge(gGpu, info.gpu, sub, tempColor(info.gpuTemp));

  // RAM: percentage and used / total
  if (info.ramUsedMB >= 0 && info.ramTotalMB > 0) {
    fmtGB(info.ramUsedMB, a, sizeof a);
    fmtGB(info.ramTotalMB, b, sizeof b);
    snprintf(sub, sizeof sub, "%s/%s GB", a, b);
  } else snprintf(sub, sizeof sub, "n/a");
  applyGauge(gRam, info.ramPct, sub, kMuted);

  // VRAM and disk bars
  if (info.vramTotalMB > 0 && info.vramUsedMB >= 0) {
    fmtGB(info.vramUsedMB, a, sizeof a);
    fmtGB(info.vramTotalMB, b, sizeof b);
    snprintf(sub, sizeof sub, "%s/%s GB", a, b);
    applyRow(rVram, info.vramUsedMB * 100 / info.vramTotalMB, sub);
  } else applyRow(rVram, -1, "n/a");
  if (info.diskPct >= 0) {
    snprintf(sub, sizeof sub, "%d/%d GB", info.diskUsedGB, info.diskTotalGB);
    applyRow(rDisk, info.diskPct, sub);
  } else applyRow(rDisk, -1, "n/a");

  // network speed
  fmtRate(info.netDownKBs, a, sizeof a);
  snprintf(sub, sizeof sub, LV_SYMBOL_DOWNLOAD " %s", a);
  setText(netDown, shownDown, sizeof shownDown, sub);
  fmtRate(info.netUpKBs, a, sizeof a);
  snprintf(sub, sizeof sub, LV_SYMBOL_UPLOAD " %s", a);
  setText(netUp, shownUp, sizeof shownUp, sub);
}

lv_obj_t* makeLabel(lv_obj_t* parent, const lv_font_t* font, uint32_t color, lv_opa_t opa, int x, int y, int w,
                    lv_text_align_t align) {
  lv_obj_t* l = lv_label_create(parent);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
  lv_obj_set_style_text_opa(l, opa, 0);
  lv_obj_set_style_text_align(l, align, 0);
  lv_obj_set_pos(l, x, y);
  lv_obj_set_width(l, w);
  lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
  lv_label_set_text(l, "");
  return l;
}

void makeGauge(Gauge& g, lv_obj_t* root, int cx, const char* name) {
  constexpr int kSize = 92;
  g.arc = lv_arc_create(root);
  lv_obj_set_size(g.arc, kSize, kSize);
  lv_obj_set_pos(g.arc, cx - kSize / 2, 8);
  lv_arc_set_rotation(g.arc, 135);
  lv_arc_set_bg_angles(g.arc, 0, 270);
  lv_arc_set_range(g.arc, 0, 100);
  lv_arc_set_value(g.arc, 0);
  lv_obj_remove_style(g.arc, nullptr, LV_PART_KNOB);
  lv_obj_clear_flag(g.arc, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_arc_width(g.arc, 9, LV_PART_MAIN);
  lv_obj_set_style_arc_width(g.arc, 9, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(g.arc, lv_color_hex(kTrack), LV_PART_MAIN);
  lv_obj_set_style_arc_rounded(g.arc, true, LV_PART_INDICATOR);

  g.value = makeLabel(root, &lv_font_montserrat_24, 0xFFFFFF, LV_OPA_COVER, cx - 36, 8 + 28, 72, LV_TEXT_ALIGN_CENTER);
  makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_60, cx - 36, 8 + 58, 72, LV_TEXT_ALIGN_CENTER);
  lv_label_set_text(lv_obj_get_child(root, -1), name);  // the label just created: the gauge's name
  g.sub = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_COVER, cx - 52, 104, 104, LV_TEXT_ALIGN_CENTER);
}

void makeRow(Row& r, lv_obj_t* root, int y, const char* name) {
  makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_60, 10, y, 46, LV_TEXT_ALIGN_LEFT);
  lv_label_set_text(lv_obj_get_child(root, -1), name);
  r.bar = lv_bar_create(root);
  lv_obj_set_pos(r.bar, 58, y + 5);
  lv_obj_set_size(r.bar, 186, 8);
  lv_bar_set_range(r.bar, 0, 100);
  lv_obj_set_style_bg_color(r.bar, lv_color_hex(kTrack), LV_PART_MAIN);
  lv_obj_set_style_bg_opa(r.bar, LV_OPA_COVER, LV_PART_MAIN);
  lv_obj_set_style_bg_opa(r.bar, LV_OPA_COVER, LV_PART_INDICATOR);
  lv_obj_set_style_radius(r.bar, 4, LV_PART_MAIN);
  lv_obj_set_style_radius(r.bar, 4, LV_PART_INDICATOR);
  r.text = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 250, y, 62, LV_TEXT_ALIGN_RIGHT);
}

}  // namespace

void statsForget() {
  built = false;
  gCpu = Gauge();
  gGpu = Gauge();
  gRam = Gauge();
  rVram = Row();
  rDisk = Row();
  shownDown[0] = shownUp[0] = 0;
}

void statsBuild(lv_obj_t* tile) {
  lv_obj_t* root = lv_obj_create(tile);
  lv_obj_remove_style_all(root);
  lv_obj_set_size(root, lv_obj_get_width(tile), lv_obj_get_height(tile));
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(root, lv_color_hex(kBg), 0);
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);

  makeGauge(gCpu, root, 54, "CPU");
  makeGauge(gGpu, root, 160, "GPU");
  makeGauge(gRam, root, 266, "RAM");
  makeRow(rVram, root, 136, "VRAM");
  makeRow(rDisk, root, 160, "DISK");
  netDown = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 10, 186, 150, LV_TEXT_ALIGN_LEFT);
  netUp = makeLabel(root, &lv_font_montserrat_14, 0xFFFFFF, LV_OPA_80, 165, 186, 145, LV_TEXT_ALIGN_LEFT);

  built = true;
  applyAll();
}

void statsSet(const StatsInfo& s) {
  info = s;
  applyAll();
}
