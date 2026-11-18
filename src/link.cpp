#include "link.h"

#include <ArduinoJson.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <lvgl.h>
#include <mbedtls/base64.h>

#include "audio.h"
#include "deck_model.h"
#include "display_hal.h"
#include "nowplaying.h"
#include "stats.h"
#include "ui.h"

namespace {

constexpr const char* kFirmware = "0.7.0";
constexpr const char* kLayoutFile = "/layout.json";
constexpr const char* kLayoutTmp = "/layout.tmp";

struct Settings {
  int volume = 70;
  bool sounds = true;
  int brightness = 255;
};

Settings settings;
Preferences prefs;
bool fsOk = false;

String layoutJson;      // text of the layout currently shown ("" = built-in demo)
uint32_t layoutCrc = 0;

// chunked layout transfer state
String pending;
int expectSeq = 0;

uint32_t fnv1a(const String& s) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < s.length(); i++) {
    h ^= static_cast<uint8_t>(s[i]);
    h *= 16777619u;
  }
  return h;
}

String crcHex(uint32_t crc) {
  char b[9];
  snprintf(b, sizeof b, "%08x", static_cast<unsigned>(crc));
  return String(b);
}

void send(JsonDocument& doc) {
  String s;
  serializeJson(doc, s);
  Serial.println(s);
}

void putSettings(JsonObject o) {
  o["volume"] = settings.volume;
  o["sounds"] = settings.sounds;
  o["brightness"] = settings.brightness;
}

void applySettings() {
  audioSetVolume(settings.volume);
  audioSetEnabled(settings.sounds);
  setBacklight(static_cast<uint8_t>(settings.brightness));
}

void saveSettings() {
  prefs.begin("deck", false);
  prefs.putInt("vol", settings.volume);
  prefs.putBool("snd", settings.sounds);
  prefs.putInt("bri", settings.brightness);
  prefs.end();
}

void loadSettings() {
  prefs.begin("deck", true);
  settings.volume = prefs.getInt("vol", settings.volume);
  settings.sounds = prefs.getBool("snd", settings.sounds);
  settings.brightness = prefs.getInt("bri", settings.brightness);
  prefs.end();
}

bool writeLayoutFile(const String& text) {
  if (!fsOk) return false;
  File f = LittleFS.open(kLayoutTmp, "w");
  if (!f) return false;
  const size_t n = f.print(text);
  f.close();
  if (n != text.length()) return false;
  LittleFS.remove(kLayoutFile);
  return LittleFS.rename(kLayoutTmp, kLayoutFile);
}

// Shows `text` and (when `save`) stores it. On failure the previous layout stays.
bool applyLayout(const String& text, bool save, String& err) {
  if (!uiLoadLayout(text.c_str(), err)) return false;
  layoutJson = text;
  layoutCrc = fnv1a(text);
  if (save && !writeLayoutFile(text)) Serial.println("[link] warning: could not store the layout");
  return true;
}

void sendLayoutAck(bool ok, const String& err) {
  JsonDocument d;
  d["type"] = "layout-ack";
  d["ok"] = ok;
  if (ok) d["crc"] = crcHex(layoutCrc);
  else d["error"] = err;
  send(d);
}

void onHello() {
  JsonDocument d;
  d["type"] = "hello";
  d["protocol"] = 1;
  d["firmware"] = kFirmware;
  d["uptimeMs"] = millis();
  d["coverPx"] = kCoverPx;
  d["desktop"] = uiActiveDesktop();
  d["screen"]["w"] = lv_disp_get_hor_res(nullptr);
  d["screen"]["h"] = lv_disp_get_ver_res(nullptr);
  d["layoutCrc"] = layoutJson.isEmpty() ? "" : crcHex(layoutCrc);
  putSettings(d["settings"].to<JsonObject>());
  send(d);
}

void onLayoutChunk(JsonDocument& m) {
  const int seq = m["seq"] | 0;
  const int of = m["of"] | 1;
  const char* data = m["data"] | "";
  if (seq == 0) {
    pending = "";
    expectSeq = 0;
  }
  if (seq != expectSeq) {
    pending = "";
    expectSeq = 0;
    sendLayoutAck(false, "chunk out of order");
    return;
  }
  pending += data;
  expectSeq++;
  if (expectSeq < of) {
    JsonDocument d;
    d["type"] = "layout-part";
    d["seq"] = seq;
    send(d);
    return;
  }
  String err;
  const bool ok = applyLayout(pending, true, err);
  pending = "";
  expectSeq = 0;
  sendLayoutAck(ok, err);
}

void onNowPlaying(JsonDocument& m) {
  NpInfo n;
  const String st = m["state"] | "none";
  n.state = st == "playing" ? NpState::Playing : st == "paused" ? NpState::Paused : st == "stopped" ? NpState::Stopped : NpState::None;
  n.title = m["title"] | "";
  n.artist = m["artist"] | "";
  n.album = m["album"] | "";
  n.posMs = m["posMs"] | 0;
  n.durMs = m["durMs"] | 0;
  n.rate = m["rate"] | 1.0f;
  n.canSeek = m["canSeek"] | false;
  n.canPrev = m["canPrev"] | false;
  n.canNext = m["canNext"] | false;
  n.canPlay = m["canPlay"] | false;
  n.shuffle = m["shuffle"] | false;
  n.repeat = m["repeat"] | 0;
  n.canShuffle = m["canShuffle"] | false;
  n.canRepeat = m["canRepeat"] | false;
  npSetInfo(n);
}

void onStats(JsonDocument& m) {
  StatsInfo s;
  s.cpu = m["cpu"] | -1;
  s.cpuTemp = m["cpuTemp"] | -1;
  s.gpu = m["gpu"] | -1;
  s.gpuTemp = m["gpuTemp"] | -1;
  s.gpuPower = m["gpuPower"] | -1;
  s.ramPct = m["ramPct"] | -1;
  s.ramUsedMB = m["ramUsedMB"] | -1;
  s.ramTotalMB = m["ramTotalMB"] | -1;
  s.vramUsedMB = m["vramUsedMB"] | -1;
  s.vramTotalMB = m["vramTotalMB"] | -1;
  s.diskPct = m["diskPct"] | -1;
  s.diskUsedGB = m["diskUsedGB"] | -1;
  s.diskTotalGB = m["diskTotalGB"] | -1;
  s.netDownKBs = m["netDownKBs"] | -1;
  s.netUpKBs = m["netUpKBs"] | -1;
  statsSet(s);
}

void sendCoverAck(bool ok, const char* id, const char* err) {
  JsonDocument d;
  d["type"] = "cover-ack";
  d["ok"] = ok;
  if (ok) d["id"] = id;
  else d["error"] = err;
  send(d);
}

// Cover image in pieces of raw RGB565 (base64 in the message); stop-and-wait: one ack per piece.
void onCover(JsonDocument& m) {
  static uint8_t raw[4096];
  const size_t off = m["off"] | 0;
  const size_t total = m["total"] | 0;
  const char* data = m["data"] | "";
  const char* id = m["id"] | "";
  if (off == 0) {
    const char* bg = m["bg"] | "";  // "#RRGGBB": screen background derived from the cover
    const uint32_t color = (bg[0] == '#' && strlen(bg) == 7) ? strtoul(bg + 1, nullptr, 16) : 0x1B2535;
    if (!npCoverBegin(m["px"] | 0, total, color)) return sendCoverAck(false, id, "unsupported cover size");
  }
  size_t n = 0;
  if (mbedtls_base64_decode(raw, sizeof raw, &n, reinterpret_cast<const uint8_t*>(data), strlen(data)) != 0 ||
      !npCoverWrite(off, raw, n)) {
    return sendCoverAck(false, id, "bad cover piece");
  }
  if (off + n < total) {
    JsonDocument d;
    d["type"] = "cover-part";
    d["off"] = off;
    send(d);
    return;
  }
  sendCoverAck(npCoverEnd(), id, "incomplete cover");
}

void onSettings(JsonDocument& m) {
  if (m["volume"].is<int>()) settings.volume = constrain(m["volume"].as<int>(), 0, 100);
  if (m["sounds"].is<bool>()) settings.sounds = m["sounds"].as<bool>();
  if (m["brightness"].is<int>()) settings.brightness = constrain(m["brightness"].as<int>(), 5, 255);
  applySettings();
  saveSettings();
  JsonDocument d;
  d["type"] = "settings-ack";
  putSettings(d.as<JsonObject>());
  send(d);
}

void onJson(const String& line) {
  JsonDocument m;
  if (deserializeJson(m, line)) {
    Serial.println("{\"type\":\"error\",\"error\":\"bad json\"}");
    return;
  }
  const String type = m["type"] | "";
  if (type == "hello") onHello();
  else if (type == "ping") {
    JsonDocument d;
    d["type"] = "pong";
    if (!m["id"].isNull()) d["id"] = m["id"];
    send(d);
  } else if (type == "layout") onLayoutChunk(m);
  else if (type == "settings") onSettings(m);
  else if (type == "np") onNowPlaying(m);
  else if (type == "stats") onStats(m);
  else if (type == "cover") onCover(m);
  else if (type == "cover-clear") npCoverClear();
  else if (type == "focus") uiShowDesktop(m["desktop"] | 0);
  else if (type == "reset-layout") {
    if (fsOk) LittleFS.remove(kLayoutFile);
    String err;
    layoutJson = "";
    layoutCrc = 0;
    uiLoadLayout(kDefaultLayoutJson, err);
    sendLayoutAck(true, "");
  } else {
    JsonDocument d;
    d["type"] = "error";
    d["error"] = "unknown type";
    send(d);
  }
}

void onConsole(const String& line) {
  if (line == "ping") Serial.println("{\"type\":\"pong\"}");
  else if (line == "beep") audioPlay(Sound::Tap);
  else if (line == "back") uiGoBack();
  else if (line == "reboot") ESP.restart();
  else if (line == "info") {
    Serial.printf("layout %s crc %s, vol %d sounds %d bri %d, heap %u KB, PSRAM %u KB\n",
                  layoutJson.isEmpty() ? "demo" : "stored", crcHex(layoutCrc).c_str(), settings.volume, settings.sounds,
                  settings.brightness, (unsigned)(ESP.getFreeHeap() / 1024), (unsigned)(ESP.getFreePsram() / 1024));
  } else if (line.startsWith("desktop ")) uiShowDesktop(line.substring(8).toInt());
  else if (line.startsWith("vol ")) {
    settings.volume = constrain(line.substring(4).toInt(), 0, 100);
    applySettings();
    saveSettings();
    Serial.printf("vol %d\n", settings.volume);
  } else if (line.startsWith("amp ")) {
    audioSetAmp(line.substring(4).toInt() != 0);
    audioPlay(Sound::Tap);
    Serial.println("amp set");
  } else if (line.length()) Serial.println("console: ping | beep | back | info | reboot | desktop N | vol N | amp 0/1  (PC link speaks JSON lines)");
}

}  // namespace

void linkSendEvent(const char* json) { Serial.println(json); }

void linkBegin() {
  fsOk = LittleFS.begin(true);  // formats on first use
  loadSettings();
  applySettings();

  String err;
  bool shown = false;
  if (fsOk && LittleFS.exists(kLayoutFile)) {
    File f = LittleFS.open(kLayoutFile, "r");
    const String text = f ? f.readString() : String();
    if (f) f.close();
    if (text.length() && applyLayout(text, false, err)) shown = true;
    else Serial.printf("[link] stored layout rejected: %s\n", err.c_str());
  }
  if (!shown) uiLoadLayout(kDefaultLayoutJson, err);
}

void linkHandleLine(String line) {
  line.trim();
  if (line.isEmpty()) return;
  if (line[0] == '{') onJson(line);
  else onConsole(line);
}
