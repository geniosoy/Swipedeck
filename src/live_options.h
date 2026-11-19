// Options of the two live screens, read from the layout (deck.json "options"); see deck_model.h.
// The player filter of the Now playing screen is not here: it lives on the PC.
#pragma once
#include <Arduino.h>

#include <vector>

struct NpOptions {
  bool tint = true;            // background takes a muted color from the cover
  bool album = true;           // show the album line above the title
  bool basicControls = false;  // only previous / play-pause / next (no shuffle, no repeat)
  bool appIcon = false;        // show the icon of the app that is playing (the PC sends the picture)
};

// What the PC stats screen can show, in this numbering.
enum StatItem : uint8_t { kStatCpu, kStatGpu, kStatRam, kStatVram, kStatDisk, kStatNet, kStatItemCount };

enum class StatsStyle : uint8_t {
  Gauges,  // round gauges for the first three percentage items, bars for the rest, network as a line
  Bars,    // one bar row per item
  Tiles,   // two columns of tiles with a big number each
};

// The clock screen: which parts it has. `weather` is on only when the PC has a place to ask about.
struct ClockOptions {
  bool hour12 = false;    // 2:05 PM instead of 14:05
  bool seconds = false;   // seconds next to the time
  bool timer = true;      // the countdown timer
  bool weather = false;   // the weather block
};

struct StatsOptions {
  StatsStyle style = StatsStyle::Gauges;
  std::vector<uint8_t> items = {kStatCpu, kStatGpu, kStatRam, kStatVram, kStatDisk, kStatNet};  // order = screen order
};
