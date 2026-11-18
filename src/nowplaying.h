// "Now playing" desktop (Spotify Car Thing style): cover left; album, big title and artist right; thin
// full-width progress bar you can drag; control bar with shuffle / previous / play-pause / next / repeat.
// State comes from the PC companion (Windows media session); user input goes back as events:
//   {"type":"media","cmd":"toggle|next|prev|shuffle|repeat"}     {"type":"seek","posMs":123000}
#pragma once
#include <Arduino.h>
#include <lvgl.h>

#include "ui.h"

constexpr int kCoverPx = 140;  // the PC converts covers to this square size (RGB565, little-endian)

enum class NpState : uint8_t { None, Playing, Paused, Stopped };

struct NpInfo {
  NpState state = NpState::None;
  String title, artist, album;
  int32_t posMs = 0;  // position at the moment this message arrived
  int32_t durMs = 0;  // 0 = unknown / live
  float rate = 1.0f;
  bool canSeek = false, canPrev = false, canNext = false, canPlay = false;
  bool shuffle = false, canShuffle = false, canRepeat = false;
  uint8_t repeat = 0;  // 0 off, 1 track, 2 list
};

void npInit(UiEventFn sink);          // once, after lv_init (timer + event sink)
void npBuild(lv_obj_t* tile);         // builds the screen inside `tile`
void npForget();                      // the widgets were deleted (layout reload): drop the pointers
void npSetInfo(const NpInfo& info);   // new state from the PC (the progress bar then runs on its own clock)

// Cover image, sent in pieces: Begin, Write (sequential offsets) ..., End. Shown only when complete.
// `bg` is the screen background derived from the cover (0xRRGGBB), applied together with the image.
bool npCoverBegin(int px, size_t total, uint32_t bg);
bool npCoverWrite(size_t off, const uint8_t* data, size_t len);
bool npCoverEnd();
void npCoverClear();
