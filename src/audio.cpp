#include "audio.h"

#include <Wire.h>
#include <driver/i2s.h>
#include <math.h>

#include "board.h"
#include "es8311.h"

namespace {
constexpr int kRate = 16000;
constexpr i2s_port_t kPort = I2S_NUM_0;

struct Clip {
  int16_t* pcm = nullptr;  // interleaved stereo
  size_t frames = 0;
};
Clip clips[static_cast<int>(Sound::kCount)];
QueueHandle_t queue = nullptr;
es8311_handle_t codec = nullptr;
bool ready = false;
bool enabled = true;
bool navSounds = true;  // folder / back / error sounds (key presses are always governed by `enabled` alone)
int theme = 0;
int volume = 70;
SemaphoreHandle_t clipLock = nullptr;  // held while a clip plays and while the clips are rebuilt for a new theme

// Sine burst with a fast attack and exponential decay; the pitch glides from f0 to f1.
void synth(Sound s, float f0, float f1, float ms, float amp, float decayMs) {
  Clip& c = clips[static_cast<int>(s)];
  if (c.pcm) { heap_caps_free(c.pcm); c.pcm = nullptr; }
  c.frames = static_cast<size_t>(kRate * ms / 1000.0f);
  c.pcm = static_cast<int16_t*>(heap_caps_malloc(c.frames * 2 * sizeof(int16_t), MALLOC_CAP_8BIT));
  if (!c.pcm) { c.frames = 0; return; }
  float phase = 0;
  for (size_t i = 0; i < c.frames; i++) {
    const float t = i / static_cast<float>(kRate);
    const float k = i / static_cast<float>(c.frames);
    phase += 2.0f * PI * (f0 + (f1 - f0) * k) / kRate;
    const float attack = fminf(1.0f, t / 0.001f);
    const float env = attack * expf(-t * 1000.0f / decayMs);
    const int16_t v = static_cast<int16_t>(sinf(phase) * env * amp * 32767.0f);
    c.pcm[2 * i] = v;
    c.pcm[2 * i + 1] = v;
  }
}

// Each theme is five short synthesised clips (see synth): freq start/end in Hz, length, amplitude, decay.
void buildClips() {
  switch (theme) {
    default:
    case 0:  // classic: crisp tick, rising "open", falling "back"
      synth(Sound::Tap, 2400, 2400, 40, 0.55f, 9);
      synth(Sound::Nav, 1500, 2300, 70, 0.50f, 24);
      synth(Sound::Back, 2300, 1500, 70, 0.50f, 24);
      synth(Sound::Swipe, 900, 700, 35, 0.35f, 12);
      synth(Sound::Error, 500, 420, 160, 0.55f, 70);
      break;
    case 1:  // soft: low and round
      synth(Sound::Tap, 900, 800, 55, 0.55f, 16);
      synth(Sound::Nav, 700, 1000, 90, 0.50f, 34);
      synth(Sound::Back, 1000, 700, 90, 0.50f, 34);
      synth(Sound::Swipe, 500, 450, 40, 0.30f, 14);
      synth(Sound::Error, 300, 240, 200, 0.55f, 90);
      break;
    case 2:  // click: a very short high tick, like a mechanical switch
      synth(Sound::Tap, 3600, 3000, 18, 0.60f, 4);
      synth(Sound::Nav, 3000, 4200, 30, 0.55f, 8);
      synth(Sound::Back, 4200, 3000, 30, 0.55f, 8);
      synth(Sound::Swipe, 1800, 1500, 20, 0.30f, 6);
      synth(Sound::Error, 700, 600, 120, 0.55f, 50);
      break;
    case 3:  // chirp: bubbly, retro game
      synth(Sound::Tap, 1200, 2200, 55, 0.50f, 22);
      synth(Sound::Nav, 800, 2800, 110, 0.50f, 50);
      synth(Sound::Back, 2800, 800, 110, 0.50f, 50);
      synth(Sound::Swipe, 1000, 1300, 40, 0.30f, 16);
      synth(Sound::Error, 400, 150, 220, 0.55f, 100);
      break;
  }
}

void audioTask(void*) {
  uint8_t id;
  for (;;) {
    if (xQueueReceive(queue, &id, portMAX_DELAY) != pdTRUE) continue;
    xSemaphoreTake(clipLock, portMAX_DELAY);
    const Clip& c = clips[id];
    if (c.pcm && enabled) {
      size_t written = 0;
      i2s_write(kPort, c.pcm, c.frames * 2 * sizeof(int16_t), &written, portMAX_DELAY);
    }
    xSemaphoreGive(clipLock);
  }
}
}  // namespace

bool audioBegin() {
  pinMode(PIN_AMP_EN, OUTPUT);
  digitalWrite(PIN_AMP_EN, !AMP_ON_LEVEL);  // amp off while the codec starts (avoids a pop)

  i2s_config_t cfg = {};
  cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  cfg.sample_rate = kRate;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = 64;  // 4 x 64 frames = 16 ms of buffered audio -> low click latency
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = true;  // underrun = silence, so the stream can idle forever
  cfg.fixed_mclk = kRate * 256;
  cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
  if (i2s_driver_install(kPort, &cfg, 0, nullptr) != ESP_OK) return false;

  i2s_pin_config_t pins = {};
  pins.mck_io_num = PIN_I2S_MCLK;
  pins.bck_io_num = PIN_I2S_BCLK;
  pins.ws_io_num = PIN_I2S_LRCK;
  pins.data_out_num = PIN_I2S_DOUT;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  if (i2s_set_pin(kPort, &pins) != ESP_OK) return false;

  // The codec derives its clocks from MCLK, so it must be running before the codec is configured.
  codec = es8311_create(I2C_NUM_0, ES8311_ADDRESS_0);
  if (!codec) return false;
  const es8311_clock_config_t clk = {
      .mclk_inverted = false,
      .sclk_inverted = false,
      .mclk_from_mclk_pin = true,
      .mclk_frequency = kRate * 256,
      .sample_frequency = kRate,
  };
  if (es8311_init(codec, &clk, ES8311_RESOLUTION_16, ES8311_RESOLUTION_16) != ESP_OK) return false;
  es8311_voice_volume_set(codec, volume, nullptr);
  es8311_voice_mute(codec, false);

  clipLock = xSemaphoreCreateMutex();
  buildClips();
  queue = xQueueCreate(8, sizeof(uint8_t));
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, nullptr, 2, nullptr, 0);

  delay(20);
  digitalWrite(PIN_AMP_EN, AMP_ON_LEVEL);
  ready = true;
  return true;
}

bool audioReady() { return ready; }

void audioPlay(Sound s) {
  if (!ready || !enabled) return;
  if (!navSounds && (s == Sound::Nav || s == Sound::Back)) return;
  const uint8_t id = static_cast<uint8_t>(s);
  xQueueSend(queue, &id, 0);
}

void audioSetVolume(int pct) {
  volume = constrain(pct, 0, 100);
  if (codec) es8311_voice_volume_set(codec, volume, nullptr);
}
int audioVolume() { return volume; }
void audioSetAmp(bool on) { digitalWrite(PIN_AMP_EN, on ? HIGH : LOW); }
void audioSetEnabled(bool on) { enabled = on; }
void audioSetNavSounds(bool on) { navSounds = on; }

void audioSetTheme(int t) {
  t = constrain(t, 0, kAudioThemes - 1);
  if (t == theme && ready) return;
  if (!ready) { theme = t; return; }
  xSemaphoreTake(clipLock, portMAX_DELAY);  // waits for a clip that is playing right now
  theme = t;
  buildClips();
  xSemaphoreGive(clipLock);
}
int audioTheme() { return theme; }
