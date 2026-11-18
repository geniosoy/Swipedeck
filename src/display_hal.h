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

// Call regularly from loop() (or the UI task); runs LVGL timers and redraws.
void displayLoop();
