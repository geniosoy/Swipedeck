#include "display_hal.h"

#include <FT6336.h>

#include "board.h"

TFT_eSPI tft = TFT_eSPI();

// The touch library is told the native (portrait) panel size and rotates coordinates itself.
static FT6336 touch(PIN_TOUCH_SDA, PIN_TOUCH_SCL, PIN_TOUCH_INT, PIN_TOUCH_RST, PANEL_W, PANEL_H);

static constexpr int BL_CHANNEL = 0;
static constexpr int DRAW_LINES = 40;  // LVGL partial-render buffer height

static lv_disp_draw_buf_t drawBuf;
static lv_disp_drv_t dispDrv;
static lv_indev_drv_t indevDrv;

static void flushCb(lv_disp_drv_t* drv, const lv_area_t* area, lv_color_t* px) {
  const uint32_t w = area->x2 - area->x1 + 1;
  const uint32_t h = area->y2 - area->y1 + 1;
  tft.startWrite();
  tft.setAddrWindow(area->x1, area->y1, w, h);
  tft.pushColors(reinterpret_cast<uint16_t*>(&px->full), w * h, true);  // true = swap bytes
  tft.endWrite();
  lv_disp_flush_ready(drv);
}

static void touchCb(lv_indev_drv_t*, lv_indev_data_t* data) {
  touch.read();
  if (touch.isTouched) {
    data->state = LV_INDEV_STATE_PR;
    data->point.x = constrain(touch.points[0].x, 0, tft.width() - 1);
    data->point.y = constrain(touch.points[0].y, 0, tft.height() - 1);
  } else {
    data->state = LV_INDEV_STATE_REL;
  }
}

void setBacklight(uint8_t level) { ledcWrite(BL_CHANNEL, level); }

void displayBegin(int rotation) {
  rotation = constrain(rotation, 0, 3);
  tft.init();
  tft.setRotation(rotation);
  tft.fillScreen(TFT_BLACK);

  // Backlight PWM (TFT_eSPI switched it on; take over the pin for dimming).
  ledcSetup(BL_CHANNEL, 5000, 8);
  ledcAttachPin(PIN_LCD_BL, BL_CHANNEL);
  setBacklight(255);

  touch.begin();
  touch.setRotation(rotation);  // FT6336 rotation constants use the same numbering as TFT_eSPI (0..3)

  lv_init();
  // Render buffer in internal RAM (fast); LVGL objects/canvases themselves go to PSRAM via malloc.
  const size_t bufPx = static_cast<size_t>(tft.width()) * DRAW_LINES;
  auto* buf = static_cast<lv_color_t*>(heap_caps_malloc(bufPx * sizeof(lv_color_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  lv_disp_draw_buf_init(&drawBuf, buf, nullptr, bufPx);

  lv_disp_drv_init(&dispDrv);
  dispDrv.hor_res = tft.width();
  dispDrv.ver_res = tft.height();
  dispDrv.flush_cb = flushCb;
  dispDrv.draw_buf = &drawBuf;
  lv_disp_drv_register(&dispDrv);

  lv_indev_drv_init(&indevDrv);
  indevDrv.type = LV_INDEV_TYPE_POINTER;
  indevDrv.read_cb = touchCb;
  lv_indev_drv_register(&indevDrv);
}

void displayLoop() { lv_timer_handler(); }
