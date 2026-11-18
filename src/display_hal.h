// Display + touch + LVGL glue for the ES3C28P board.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>
#include <lvgl.h>

extern TFT_eSPI tft;

// Initialises the ILI9341V panel, the FT6336 touch controller and LVGL.
// rotation: 0 = portrait, 1 = landscape (default), 2 = portrait flipped, 3 = landscape flipped.
void displayBegin(int rotation = 1);

// Backlight brightness 0..255 (PWM on IO45).
void setBacklight(uint8_t level);

// User brightness 5..255; the screen power state (dimmed / asleep) is applied on top of it.
void displaySetBrightness(int level);
// Seconds of no touch before the backlight dims / switches off (0 = never). Also wakes the screen.
void displaySetPower(int dimAfterSec, int sleepAfterSec);
void displayWake();                 // as if the screen had been touched (without touching anything)
void displaySetRotation(int rotation);  // 1 = normal landscape, 3 = upside down (the case mounted the other way)

// Call regularly from loop() (or the UI task); runs LVGL timers and redraws.
void displayLoop();
