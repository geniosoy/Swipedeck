// "Clock" desktop: the time of day, the weather, and a countdown timer (see live_options.h for the options).
//
// The board has no clock of its own: the PC companion sends {"type":"clock","epoch":..,"offsetMin":..} when the screen is
// shown and every 30 s, and the board counts the seconds in between. {"type":"weather","ok":true,"temp":9,"unit":"C",
// "text":"Overcast","hi":10,"lo":6,"place":"London"} (or "ok":false) comes from the companion's weather feed.
//
// The timer belongs to the board, not to the screen: it keeps counting when you swipe to another desktop and when the PC
// is away. When it ends the board rings (Sound::Alarm, even when key sounds are off), wakes the screen and brings this
// desktop to the front; any touch of a timer button stops the ringing.
#pragma once
#include <Arduino.h>
#include <lvgl.h>

#include "live_options.h"

struct WeatherInfo {
  bool valid = false;  // a message has arrived
  bool ok = false;     // ...and it has a reading (not "unavailable")
  int temp = 0, hi = 0, lo = 0;
  char unit = 'C';
  String text, place;
};

void clockInit();                                             // starts the timer tick (runs on every screen)
void clockBuild(lv_obj_t* tile, const ClockOptions& options);  // builds the screen inside `tile`
void clockForget();                                           // widgets were deleted (layout reload)
void clockSync(int64_t epoch, int offsetMin);                 // the time of day from the PC
void clockSetWeather(const WeatherInfo& info);
