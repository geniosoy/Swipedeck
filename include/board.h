// Board definition: LCDWiki / Hosyond 2.8" ESP32-S3 display (ES3C28P, touch version).
// Pin numbers: see ../../ES3C28P_BOARD_NOTES.md (verified on the board unless marked otherwise).
// Display SPI pins are set in platformio.ini (TFT_eSPI build flags).
#pragma once

constexpr int PANEL_W = 240;  // native portrait size; the app runs landscape (rotation 1 -> 320x240)
constexpr int PANEL_H = 320;
constexpr int PIN_LCD_BL = 45;  // backlight, HIGH = on

// FT6336 capacitive touch; the I2C bus is shared with the ES8311 audio codec (0x18).
constexpr int PIN_TOUCH_SDA = 16;
constexpr int PIN_TOUCH_SCL = 15;
constexpr int PIN_TOUCH_INT = 17;
constexpr int PIN_TOUCH_RST = 18;

// ES8311 codec + speaker amplifier (vendor demos 16/17).
constexpr int PIN_I2S_MCLK = 4;
constexpr int PIN_I2S_BCLK = 5;
constexpr int PIN_I2S_LRCK = 7;
constexpr int PIN_I2S_DOUT = 8;  // ESP -> codec DAC (speaker)
constexpr int PIN_AMP_EN = 1;    // vendor demo drives this LOW while playing audio (AP_ENABLE)
constexpr int AMP_ON_LEVEL = 0;  // [source: demo 16] if the speaker stays silent try 1 (serial: "amp 1")

constexpr int PIN_RGB_LED = 42;  // single WS2812 (not used yet)
