// "PC stats" desktop: CPU / GPU / RAM / VRAM / disk / network, shown in one of three styles (see live_options.h).
// The PC companion sends a `stats` message about once a second, and only while this desktop is visible.
// -1 in any field means "not available" (e.g. no CPU temperature sensor on Windows without extra software).
#pragma once
#include <Arduino.h>
#include <lvgl.h>

#include "live_options.h"

struct StatsInfo {
  int cpu = -1;       // %
  int cpuTemp = -1;   // deg C
  int gpu = -1;       // %
  int gpuTemp = -1;   // deg C
  int gpuPower = -1;  // W
  int ramPct = -1, ramUsedMB = -1, ramTotalMB = -1;
  int vramUsedMB = -1, vramTotalMB = -1;
  int diskPct = -1, diskUsedGB = -1, diskTotalGB = -1;
  int netDownKBs = -1, netUpKBs = -1;
};

void statsBuild(lv_obj_t* tile, const StatsOptions& options);  // builds the screen inside `tile`
void statsForget();                                            // widgets were deleted (layout reload)
void statsSet(const StatsInfo& info);                          // new numbers from the PC
