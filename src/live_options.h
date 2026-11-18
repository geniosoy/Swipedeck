// Options of the two live screens, read from the layout (deck.json "options"); see deck_model.h.
// The player filter of the Now playing screen is not here: it lives on the PC.
#pragma once
#include <Arduino.h>

#include <vector>

struct NpOptions {
  bool tint = true;            // background takes a muted color from the cover
  bool album = true;           // show the album line above the title
  bool basicControls = false;  // only previous / play-pause / next (no shuffle, no repeat)
};

// What the PC stats screen can show, in this numbering.
enum StatItem : uint8_t { kStatCpu, kStatGpu, kStatRam, kStatVram, kStatDisk, kStatNet, kStatItemCount };

enum class StatsStyle : uint8_t {
  Gauges,  // round gauges for the first three percentage items, bars for the rest, network as a line
  Bars,    // one bar row per item
  Tiles,   // two columns of tiles with a big number each
};

struct StatsOptions {
  StatsStyle style = StatsStyle::Gauges;
  std::vector<uint8_t> items = {kStatCpu, kStatGpu, kStatRam, kStatVram, kStatDisk, kStatNet};  // order = screen order
};
