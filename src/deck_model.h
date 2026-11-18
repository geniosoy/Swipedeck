// Deck data model: desktops (swipe between them) -> pages (folders) -> keys.
// The layout arrives as JSON (built-in demo now, from the PC companion later):
//
// {"desktops":[{"name":"Main","pages":[
//    {"id":"root","title":"Main","cols":4,"rows":3,"keys":[
//       {"label":"Mute","icon":"mute","id":"mic.mute","color":"#B3261E"},
//       {"label":"Media","icon":"audio","folder":"media"},
//       {}                                   <- empty slot
//    ]},
//    {"id":"media","title":"Media","cols":3,"rows":2,"keys":[ ... ]}
// ]}]}
//
// A desktop can also be {"name":"Playing","kind":"nowplaying"}: the media screen, no pages.
//
// The first page of a desktop is its root. A key with "folder" opens that page (by page id);
// every other key is an action: pressing it reports its "id" to the PC.
#pragma once
#include <Arduino.h>

#include <vector>

constexpr int kMaxDesktops = 8;
constexpr int kMaxPages = 16;  // per desktop

struct DeckKey {
  bool empty = true;
  String label;
  String icon;       // name from the icon table in ui.cpp ("play", "mute", ...), may be empty
  String id;         // action id reported to the PC
  uint32_t color = 0x2A3A4D;
  int folder = -1;   // index of the page to open, or -1 for a plain action key
};

struct DeckPage {
  String id;
  String title;
  int cols = 4;
  int rows = 3;
  std::vector<DeckKey> keys;  // cols * rows entries (row-major)
};

enum class DeskKind : uint8_t {
  Keys,        // pages of keys (default)
  NowPlaying,  // the live media screen (nowplaying.cpp); has no pages. At most one per deck.
};

struct DeckDesktop {
  String name;
  DeskKind kind = DeskKind::Keys;
  std::vector<DeckPage> pages;  // pages[0] is the root (empty for NowPlaying)
};

struct Deck {
  std::vector<DeckDesktop> desktops;
};

// Parses a layout. On failure returns false and fills `err`; `out` is left unchanged.
bool deckParse(const char* json, Deck& out, String& err);

// The layout used until the PC sends one.
extern const char* const kDefaultLayoutJson;
