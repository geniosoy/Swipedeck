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
#include "mixer.h"
#include "clockscreen.h"
#include "ui.h"

namespace {

constexpr const char* kFirmware = "0.14.1";
constexpr const char* kLayoutFile = "/layout.json";
constexpr const char* kLayoutTmp = "/layout.tmp";

struct Settings {
  int volume = 70;
  bool sounds = true;        // key sounds on / off
  int brightness = 255;
  int theme = 0;             // sound theme, see kThemes
  bool navSounds = true;     // folder / back sounds
  int dimAfter = 0;          // seconds without a touch before the screen dims (0 = never)
  int sleepAfter = 0;        // ... before the backlight switches off (0 = never)
  bool flip = false;         // screen upside down
  int startDesktop = 0;      // desktop shown after a layout loads / the board boots
  int keyRadius = 12;        // key corner radius, px
  int keyGap = 6;            // gap between keys, px
  bool wrap = false;         // endless scrolling between desktops
  int swipe = 1;             // swipe length needed to pass to another desktop, see kSwipes
  bool sleepWithPc = false;  // screen off while the PC is locked, or silent for kLinkTimeoutMs
};

constexpr uint32_t kLinkTimeoutMs = 15000;  // the companion pings every 2 s

const char* const kSwipes[] = {"short", "normal", "long"};
const char* const kThemes[] = {"classic", "soft", "click", "chirp"};
static_assert(sizeof(kThemes) / sizeof(kThemes[0]) == kAudioThemes, "theme names and audio themes must match");

Settings settings;
Preferences prefs;
bool fsOk = false;
bool pcLocked = false;     // the PC said its session is locked
bool linkLost = false;     // nothing heard from the PC for kLinkTimeoutMs
uint32_t lastRxMs = 0;

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
  o["soundTheme"] = kThemes[settings.theme];
  o["navSounds"] = settings.navSounds;
  o["dimAfter"] = settings.dimAfter;
  o["sleepAfter"] = settings.sleepAfter;
  o["flip"] = settings.flip;
  o["startDesktop"] = settings.startDesktop;
  o["keyRadius"] = settings.keyRadius;
  o["keyGap"] = settings.keyGap;
  o["wrap"] = settings.wrap;
  o["swipe"] = kSwipes[settings.swipe];
  o["sleepWithPc"] = settings.sleepWithPc;
}

// The screen goes off while the PC is away (locked, asleep, off, companion not running), if the user wants that.
void applyPresence() { displaySetPcAway(settings.sleepWithPc && (pcLocked || linkLost)); }

void applySettings() {
  static int rotation = 1;
  audioSetTheme(settings.theme);
  audioSetVolume(settings.volume);
  audioSetEnabled(settings.sounds);
  audioSetNavSounds(settings.navSounds);
  displaySetBrightness(settings.brightness);
  displaySetPower(settings.dimAfter, settings.sleepAfter);
  const int want = settings.flip ? 3 : 1;
  if (want != rotation) {
    rotation = want;
    displaySetRotation(rotation);
  }
  uiSetKeyLook(settings.keyRadius, settings.keyGap);
  uiSetWrap(settings.wrap);
  displaySetSwipe(settings.swipe);
  applyPresence();
}

// The desktop the board opens on; a start desktop that no longer exists means the first one.
void showStartDesktop() {
  const int n = uiDesktopCount();
  const int d = settings.startDesktop < n ? settings.startDesktop : 0;
  if (d > 0) uiShowDesktop(d, false);
}

void saveSettings() {
  prefs.begin("deck", false);
  prefs.putInt("vol", settings.volume);
  prefs.putBool("snd", settings.sounds);
  prefs.putInt("bri", settings.brightness);
  prefs.putInt("thm", settings.theme);
  prefs.putBool("nsn", settings.navSounds);
  prefs.putInt("dim", settings.dimAfter);
  prefs.putInt("slp", settings.sleepAfter);
  prefs.putBool("flp", settings.flip);
  prefs.putInt("std", settings.startDesktop);
  prefs.putInt("krd", settings.keyRadius);
  prefs.putInt("kgp", settings.keyGap);
  prefs.putBool("wrp", settings.wrap);
  prefs.putInt("swp", settings.swipe);
  prefs.putBool("spc", settings.sleepWithPc);
  prefs.end();
}

void loadSettings() {
  prefs.begin("deck", true);
  settings.volume = prefs.getInt("vol", settings.volume);
  settings.sounds = prefs.getBool("snd", settings.sounds);
  settings.brightness = prefs.getInt("bri", settings.brightness);
  settings.theme = constrain(prefs.getInt("thm", settings.theme), 0, kAudioThemes - 1);
  settings.navSounds = prefs.getBool("nsn", settings.navSounds);
  settings.dimAfter = prefs.getInt("dim", settings.dimAfter);
  settings.sleepAfter = prefs.getInt("slp", settings.sleepAfter);
  settings.flip = prefs.getBool("flp", settings.flip);
  settings.startDesktop = prefs.getInt("std", settings.startDesktop);
  settings.keyRadius = prefs.getInt("krd", settings.keyRadius);
  settings.keyGap = prefs.getInt("kgp", settings.keyGap);
  settings.wrap = prefs.getBool("wrp", settings.wrap);
  settings.swipe = constrain(prefs.getInt("swp", settings.swipe), 0, 2);
  settings.sleepWithPc = prefs.getBool("spc", settings.sleepWithPc);
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
  showStartDesktop();
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
  d["iconPx"] = kAppIconPx;
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

// {"type":"mixer","master":{"vol":50,"mute":false},"apps":[{"id":"chrome","name":"Chrome","vol":80,"mute":false}, ...]}
void onMixer(JsonDocument& m) {
  MixerInfo info;
  info.master.id = "master";
  info.master.name = "Master";
  info.master.vol = constrain(m["master"]["vol"] | 0, 0, 100);
  info.master.mute = m["master"]["mute"] | false;
  for (JsonObject a : m["apps"].as<JsonArray>()) {
    if (info.apps.size() >= 4) break;
    MixerFader f;
    f.id = String(a["id"] | "");
    f.name = String(a["name"] | "");
    f.vol = constrain(a["vol"] | 0, 0, 100);
    f.mute = a["mute"] | false;
    if (!f.id.isEmpty()) info.apps.push_back(f);
  }
  mixerSet(info);
}

// {"type":"clock","epoch":1731999999,"offsetMin":-180}
void onClock(JsonDocument& m) {
  if (m["epoch"].isNull()) return;
  clockSync(m["epoch"].as<int64_t>(), m["offsetMin"] | 0);
}

// {"type":"weather","ok":true,"temp":9,"unit":"C","text":"Overcast","hi":10,"lo":6,"place":"London"}
void onWeather(JsonDocument& m) {
  WeatherInfo w;
  w.valid = true;
  w.ok = m["ok"] | false;
  w.temp = m["temp"] | 0;
  w.hi = m["hi"] | 0;
  w.lo = m["lo"] | 0;
  w.unit = String(m["unit"] | "C")[0];
  w.text = String(m["text"] | "");
  w.place = String(m["place"] | "");
  clockSetWeather(w);
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

// The icon of the playing app: one small message (20 x 20 x 3 bytes as base64).
void onAppIcon(JsonDocument& m) {
  static uint8_t raw[2048];
  const char* data = m["data"] | "";
  size_t n = 0;
  const bool ok = strlen(data) < 3000 &&
                  mbedtls_base64_decode(raw, sizeof raw, &n, reinterpret_cast<const uint8_t*>(data), strlen(data)) == 0 &&
                  npAppIconSet(m["px"] | 0, raw, n);
  JsonDocument d;
  d["type"] = "appicon-ack";
  d["ok"] = ok;
  send(d);
}

// "preview": true applies the values without storing them (the editor's live preview); they are
// stored when the PC sends the same settings without it (on save).
void onSettings(JsonDocument& m) {
  if (m["volume"].is<int>()) settings.volume = constrain(m["volume"].as<int>(), 0, 100);
  if (m["sounds"].is<bool>()) settings.sounds = m["sounds"].as<bool>();
  if (m["brightness"].is<int>()) settings.brightness = constrain(m["brightness"].as<int>(), 5, 255);
  if (m["soundTheme"].is<const char*>()) {
    const String name = m["soundTheme"].as<const char*>();
    for (int i = 0; i < kAudioThemes; i++)
      if (name == kThemes[i]) settings.theme = i;
  }
  if (m["navSounds"].is<bool>()) settings.navSounds = m["navSounds"].as<bool>();
  if (m["dimAfter"].is<int>()) settings.dimAfter = constrain(m["dimAfter"].as<int>(), 0, 3600);
  if (m["sleepAfter"].is<int>()) settings.sleepAfter = constrain(m["sleepAfter"].as<int>(), 0, 3600);
  if (m["flip"].is<bool>()) settings.flip = m["flip"].as<bool>();
  if (m["startDesktop"].is<int>()) settings.startDesktop = constrain(m["startDesktop"].as<int>(), 0, 31);
  if (m["keyRadius"].is<int>()) settings.keyRadius = constrain(m["keyRadius"].as<int>(), 0, 24);
  if (m["keyGap"].is<int>()) settings.keyGap = constrain(m["keyGap"].as<int>(), 0, 12);
  if (m["wrap"].is<bool>()) settings.wrap = m["wrap"].as<bool>();
  if (m["sleepWithPc"].is<bool>()) settings.sleepWithPc = m["sleepWithPc"].as<bool>();
  if (m["swipe"].is<const char*>()) {
    const String name = m["swipe"].as<const char*>();
    for (int i = 0; i < 3; i++)
      if (name == kSwipes[i]) settings.swipe = i;
  }
  applySettings();
  if (!(m["preview"] | false)) saveSettings();
  JsonDocument d;
  d["type"] = "settings-ack";
  d["preview"] = m["preview"] | false;
  putSettings(d.as<JsonObject>());
  send(d);
}

// {"type":"keystate","on":["0/0/3", ...]}: which keys show their "on" look (see link.h).
void onKeyState(JsonDocument& m) {
  std::vector<uint32_t> on;
  for (JsonVariant v : m["on"].as<JsonArray>()) {
    int d = -1, p = -1, k = -1;
    if (v.is<const char*>() && sscanf(v.as<const char*>(), "%d/%d/%d", &d, &p, &k) == 3 && d >= 0 && p >= 0 && k >= 0) {
      on.push_back(uiKeyId(d, p, k));
    }
  }
  uiSetKeyStates(on);
}

// {"type":"pc","state":"locked"|"active"}
void onPc(JsonDocument& m) {
  pcLocked = String(m["state"] | "") == "locked";
  applyPresence();
}

// {"type":"sound","name":"tap|nav|back|error"}: plays one of the UI sounds (the editor's Test buttons).
void onSound(JsonDocument& m) {
  const String name = m["name"] | "tap";
  displayWake();
  if (name == "nav") audioPlay(Sound::Nav);
  else if (name == "back") audioPlay(Sound::Back);
  else if (name == "error") audioPlay(Sound::Error);
  else if (name == "alarm") audioPlay(Sound::Alarm);
  else audioPlay(Sound::Tap);
}

void onJson(const String& line) {
  JsonDocument m;
  if (deserializeJson(m, line)) {
    Serial.println("{\"type\":\"error\",\"error\":\"bad json\"}");
    return;
  }
  lastRxMs = millis();  // the PC is talking
  if (linkLost) {
    linkLost = false;
    applyPresence();
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
  else if (type == "sound") onSound(m);
  else if (type == "keystate") onKeyState(m);
  else if (type == "pc") onPc(m);
  else if (type == "np") onNowPlaying(m);
  else if (type == "stats") onStats(m);
  else if (type == "mixer") onMixer(m);
  else if (type == "clock") onClock(m);
  else if (type == "weather") onWeather(m);
  else if (type == "cover") onCover(m);
  else if (type == "cover-clear") npCoverClear();
  else if (type == "appicon") onAppIcon(m);
  else if (type == "appicon-clear") npAppIconClear();
  else if (type == "focus") uiShowDesktop(m["desktop"] | 0);
  else if (type == "reset-layout") {
    if (fsOk) LittleFS.remove(kLayoutFile);
    String err;
    layoutJson = "";
    layoutCrc = 0;
    uiLoadLayout(kDefaultLayoutJson, err);
    showStartDesktop();
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
  else if (line == "scroll") {
    int sx, tx, n;
    bool sliding;
    uiScrollInfo(sx, tx, n, sliding);
    Serial.printf("scroll x=%d activeTile x=%d desktops=%d active=%d sliding=%d\n", sx, tx, n, uiActiveDesktop(), sliding);
  } else if (line == "swipe right" || line == "swipe left") Serial.printf("swipe %s\n", uiSwipe(line.endsWith("right")) ? "wrapped" : "nothing to wrap");
  else if (line == "reboot") ESP.restart();
  else if (line == "info") {
    Serial.printf("layout %s crc %s, vol %d sounds %d bri %d, screen %s (pc locked %d, link lost %d), heap %u KB, PSRAM %u KB\n",
                  layoutJson.isEmpty() ? "demo" : "stored", crcHex(layoutCrc).c_str(), settings.volume, settings.sounds,
                  settings.brightness, displayPowerState(), pcLocked, linkLost, (unsigned)(ESP.getFreeHeap() / 1024),
                  (unsigned)(ESP.getFreePsram() / 1024));
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
  lastRxMs = millis();
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

void linkTick() {
  if (!settings.sleepWithPc || linkLost) return;
  if (millis() - lastRxMs > kLinkTimeoutMs) {
    linkLost = true;
    applyPresence();
  }
}

void linkHandleLine(String line) {
  line.trim();
  if (line.isEmpty()) return;
  if (line[0] == '{') onJson(line);
  else onConsole(line);
}
