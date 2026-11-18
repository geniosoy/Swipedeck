// LVGL deck UI: swipe between desktops, open folders (pages) with a back button, key sounds.
#pragma once
#include <Arduino.h>

// Called for every UI event that the PC should know about (one JSON object per call, no newline):
//   {"type":"press","desktop":0,"page":0,"key":3,"id":"audio.mute"}
//   {"type":"nav","desktop":0,"page":1}       (folder opened / back pressed)
//   {"type":"desktop","index":1}              (swiped to another desktop)
using UiEventFn = void (*)(const char* json);

void uiInit(UiEventFn sink);

// Parses a layout (see deck_model.h) and rebuilds the whole UI. Returns false + err on bad input,
// in which case the previous layout stays on screen.
bool uiLoadLayout(const char* json, String& err);

int uiActiveDesktop();          // index of the desktop currently shown
void uiShowDesktop(int index, bool animate = true);  // programmatic swipe (PC "focus", start desktop)
int uiDesktopCount();
// Look of the key grid: corner radius 0..24 and gap 2..12 px. Rebuilds the screen when it changes
// (keeping the desktop that is shown) and returns true in that case.
bool uiSetKeyLook(int radius, int gap);
void uiGoBack();                // same as the on-screen back button
