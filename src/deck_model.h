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
// A desktop can also be {"name":"Playing","kind":"nowplaying","options":{"tint":true,"album":true,"controls":"full"}}
// (media screen) or {"name":"PC","kind":"stats","options":{"style":"gauges","items":["cpu","gpu","ram","vram","disk","net"]}}
// (PC statistics): live screens fed by the companion, no pages. Options are optional (see live_options.h).
//
// The first page of a desktop is its root. A key with "folder" opens that page (by page id);
// every other key is an action: pressing it reports its "id" to the PC.
// A key may also have "hold":true / "repeat":true (long press, see ui.cpp) and "on":{"color","icon","label"}: the look it
// has while the PC says its state is on (a muted speaker, a running app ...).
#pragma once
#include <Arduino.h>

#include <vector>

#include "live_options.h"

constexpr int kMaxDesktops = 8;
constexpr int kMaxPages = 16;  // per desktop

struct DeckKey {
  bool empty = true;
  String label;
  String icon;       // name from the icon table in ui.cpp ("play", "mute", ...), may be empty
  String id;         // action id reported to the PC
  uint32_t color = 0x2A3A4D;
  int folder = -1;   // index of the page to open, or -1 for a plain action key
  bool hold = false;     // has a second action for a long press: the tap is then sent on release, the hold after ~0.4 s
  bool repeat = false;   // holding the key sends the press again and again (volume keys)
  bool hasOn = false;    // has a look for when its PC state is on ("on" in the layout, see keystate in link.h)
  bool on = false;       // ... and the state is on right now
  uint32_t onColor = 0;
  String onIcon, onLabel;
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
  Stats,       // PC statistics dashboard (stats.cpp); has no pages. At most one per deck.
};

struct DeckDesktop {
  String name;
  DeskKind kind = DeskKind::Keys;
  NpOptions np;                 // Now playing: how it looks
  StatsOptions stats;           // PC stats: style and which items, in which order
  std::vector<DeckPage> pages;  // pages[0] is the root (empty for the live screens)
};

struct Deck {
  std::vector<DeckDesktop> desktops;
};

// Parses a layout. On failure returns false and fills `err`; `out` is left unchanged.
bool deckParse(const char* json, Deck& out, String& err);

// The layout used until the PC sends one.
extern const char* const kDefaultLayoutJson;
