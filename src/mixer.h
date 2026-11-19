// "Volume mixer" desktop: the master volume and up to four programs as vertical faders you drag, and a speaker button under
// each to mute it. The PC companion sends a `mixer` message when something changes (only while this desktop is visible);
// the board answers with events while you touch:
//   {"type":"mixer-set","id":"master"|"<program>","vol":0..100}     (while dragging, at most ~12 per second, and at the end)
//   {"type":"mixer-mute","id":"...","mute":true|false}               (the speaker button)
#pragma once
#include <Arduino.h>
#include <lvgl.h>

#include <vector>

#include "ui.h"

struct MixerFader {
  String id;       // "master" or the program's name as the PC knows it
  String name;     // what is written under the fader
  int vol = 0;     // 0..100
  bool mute = false;
};

struct MixerInfo {
  MixerFader master;
  std::vector<MixerFader> apps;  // at most four are shown
};

void mixerInit(UiEventFn eventSink);
void mixerBuild(lv_obj_t* tile);   // builds the screen inside `tile`
void mixerForget();                // widgets were deleted (layout reload)
void mixerSet(const MixerInfo& info);
