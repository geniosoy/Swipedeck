// LVGL deck UI: swipe between desktops, open folders (pages) with a back button, key sounds.
#pragma once
#include <Arduino.h>

#include <vector>

// Called for every UI event that the PC should know about (one JSON object per call, no newline):
//   {"type":"press","desktop":0,"page":0,"key":3,"id":"audio.mute"}
//   {"type":"nav","desktop":0,"page":1}       (folder opened / back pressed)
//   {"type":"desktop","index":1}              (swiped to another desktop)
using UiEventFn = void (*)(const char* json);

// Events for keys with "hold" / "repeat" (see deck_model.h): {"type":"press",...,"hold":true} after a long press of a hold
// key, {"type":"press",...,"repeat":true} again and again while a repeat key is held; a short tap is a normal press.

// The id of key `k` on page `p` of desktop `d`, as used in the keystate message ("d/p/k").
constexpr uint32_t uiKeyId(int d, int p, int k) { return (static_cast<uint32_t>(d) << 16) | (static_cast<uint32_t>(p) << 8) | static_cast<uint32_t>(k); }

void uiInit(UiEventFn sink);

// Parses a layout (see deck_model.h) and rebuilds the whole UI. Returns false + err on bad input,
// in which case the previous layout stays on screen.
bool uiLoadLayout(const char* json, String& err);

int uiActiveDesktop();          // index of the desktop currently shown
void uiShowDesktop(int index, bool animate = true);  // programmatic swipe (PC "focus", start desktop)
int uiDesktopCount();
// Endless scrolling: swiping past the last desktop goes to the first one, and the other way round.
void uiSetWrap(bool on);
// Debug: does what a swipe past the first / last desktop does (finger moving right or left). False if nothing wraps.
bool uiSwipe(bool fingerRight);
// Debug: where the tileview is scrolled to and where its active tile is (they must be equal when nothing slides).
void uiScrollInfo(int& scrollX, int& tileX, int& tileCount, bool& sliding);
// Debug: where the keys of the visible page really are, as "WxH keys x,y,wxh ..." (area below the top bar; empty keys
// are not drawn). "not a key desktop" for the live screens.
String uiGridInfo();
// Look of the key grid, in px: corner radius, gap between keys and side padding (between the keys and the screen's
// edges), each from 0. These are the only limits (companion/tests/test_deck.py holds the editor to the same ones).
constexpr int kMaxKeyRadius = 24;
constexpr int kMaxKeyGap = 12;
constexpr int kMaxKeyPad = 12;
// Rebuilds the screen when the look changes (keeping the desktop that is shown) and returns true in that case.
bool uiSetKeyLook(int radius, int gap, int pad);
// Which keys are "on" right now (their `on` look is shown, the others show their normal look): ids from uiKeyId. Only the
// pages that change are redrawn.
void uiSetKeyStates(const std::vector<uint32_t>& onKeys);
void uiShowClock();             // brings the Clock desktop to the front (the timer rings there)
void uiGoBack();                // same as the on-screen back button
