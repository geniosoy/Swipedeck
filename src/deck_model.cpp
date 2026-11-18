#include "deck_model.h"

#include <ArduinoJson.h>

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
  for (JsonObject d : ds) {
    if (static_cast<int>(deck.desktops.size()) >= kMaxDesktops) break;
    DeckDesktop dk;
    dk.name = d["name"] | "Desktop";
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
