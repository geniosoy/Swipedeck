#include "deck_model.h"

#include <ArduinoJson.h>

#include <algorithm>

static uint32_t parseColor(const char* s, uint32_t fallback) {
  if (!s || s[0] != '#' || strlen(s) != 7) return fallback;
  return static_cast<uint32_t>(strtoul(s + 1, nullptr, 16));
}

bool deckParse(const char* json, Deck& out, String& err) {
  JsonDocument doc;
  const DeserializationError e = deserializeJson(doc, json);
  if (e) {
    err = String("json: ") + e.c_str();
    return false;
  }
  JsonArray ds = doc["desktops"];
  if (ds.isNull() || ds.size() == 0) {
    err = "no desktops";
    return false;
  }

  Deck deck;
  bool hasNowPlaying = false, hasStats = false;
  for (JsonObject d : ds) {
    if (static_cast<int>(deck.desktops.size()) >= kMaxDesktops) break;
    DeckDesktop dk;
    dk.name = d["name"] | "Desktop";
    const String kind = d["kind"] | "keys";
    if (kind == "nowplaying" || kind == "stats") {
      bool& seen = kind == "nowplaying" ? hasNowPlaying : hasStats;
      if (seen) {
        err = "only one " + kind + " desktop is supported";
        return false;
      }
      seen = true;
      dk.kind = kind == "nowplaying" ? DeskKind::NowPlaying : DeskKind::Stats;
      JsonObject options = d["options"];  // missing = all defaults
      if (dk.kind == DeskKind::NowPlaying) {
        dk.np.tint = options["tint"] | true;
        dk.np.album = options["album"] | true;
        dk.np.basicControls = String(options["controls"] | "full") == "basic";
        dk.np.appIcon = options["appIcon"] | false;
      } else {
        const String style = options["style"] | "gauges";
        dk.stats.style = style == "bars" ? StatsStyle::Bars : style == "tiles" ? StatsStyle::Tiles : StatsStyle::Gauges;
        JsonArray items = options["items"];
        if (!items.isNull() && items.size() > 0) {
          static const char* const kNames[kStatItemCount] = {"cpu", "gpu", "ram", "vram", "disk", "net"};
          std::vector<uint8_t> chosen;
          for (const char* name : items) {
            for (uint8_t i = 0; i < kStatItemCount; i++) {
              const bool repeated = std::find(chosen.begin(), chosen.end(), i) != chosen.end();
              if (name && strcmp(name, kNames[i]) == 0 && !repeated) chosen.push_back(i);
            }
          }
          if (!chosen.empty()) dk.stats.items = chosen;
        }
      }
      deck.desktops.push_back(std::move(dk));
      continue;
    }
    if (kind != "keys") {
      err = "unknown desktop kind '" + kind + "'";
      return false;
    }
    JsonArray ps = d["pages"];
    if (ps.isNull() || ps.size() == 0) {
      err = "desktop '" + dk.name + "' has no pages";
      return false;
    }

    // Pass 1: page ids -> indices (folders refer to pages by id).
    std::vector<String> ids;
    for (JsonObject p : ps) {
      if (static_cast<int>(ids.size()) >= kMaxPages) break;
      ids.push_back(String(p["id"] | ""));
    }

    // Pass 2: pages and keys.
    int pageIndex = 0;
    for (JsonObject p : ps) {
      if (pageIndex >= static_cast<int>(ids.size())) break;
      DeckPage page;
      page.id = ids[pageIndex++];
      page.title = p["title"] | dk.name.c_str();
      page.cols = constrain(static_cast<int>(p["cols"] | 4), 1, 6);
      page.rows = constrain(static_cast<int>(p["rows"] | 3), 1, 5);
      page.keys.resize(static_cast<size_t>(page.cols) * page.rows);
      int slot = 0;
      for (JsonObject k : p["keys"].as<JsonArray>()) {
        if (slot >= page.cols * page.rows) break;
        DeckKey& key = page.keys[slot++];
        if (k.size() == 0) continue;  // {} = empty slot
        key.empty = false;
        key.label = k["label"] | "";
        key.icon = k["icon"] | "";
        key.id = k["id"] | "";
        key.color = parseColor(k["color"] | "", key.color);
        const char* folder = k["folder"] | "";
        if (folder[0]) {
          for (size_t i = 0; i < ids.size(); i++) {
            if (ids[i] == folder) key.folder = static_cast<int>(i);
          }
          if (key.folder < 0) {
            err = "unknown folder '" + String(folder) + "' on desktop '" + dk.name + "'";
            return false;
          }
        }
      }
      dk.pages.push_back(std::move(page));
    }
    deck.desktops.push_back(std::move(dk));
  }
  out = std::move(deck);
  return true;
}
