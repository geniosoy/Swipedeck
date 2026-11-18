// "Now playing" desktop: cover, title/artist, transport buttons and a draggable progress bar.
// State comes from the PC companion (Windows media session); user input goes back as events:
//   {"type":"media","cmd":"toggle|next|prev"}     {"type":"seek","posMs":123000}
#pragma once
#include <Arduino.h>
#include <lvgl.h>

#include "ui.h"

constexpr int kCoverPx = 112;  // the PC converts covers to this square size (RGB565, little-endian)

enum class NpState : uint8_t { None, Playing, Paused, Stopped };

struct NpInfo {
  NpState state = NpState::None;
  String title, artist, album;
  int32_t posMs = 0;  // position at the moment this message arrived
  int32_t durMs = 0;  // 0 = unknown / live
  float rate = 1.0f;
  bool canSeek = false, canPrev = false, canNext = false, canPlay = false;
};

void npInit(UiEventFn sink);          // once, after lv_init (timer + event sink)
void npBuild(lv_obj_t* tile);         // builds the screen inside `tile`
void npForget();                      // the widgets were deleted (layout reload): drop the pointers
void npSetInfo(const NpInfo& info);   // new state from the PC (the progress bar then runs on its own clock)

// Cover image, sent in pieces: Begin, Write (sequential offsets) ..., End. Shown only when complete.
bool npCoverBegin(int px, size_t total);
bool npCoverWrite(size_t off, const uint8_t* data, size_t len);
bool npCoverEnd();
void npCoverClear();
