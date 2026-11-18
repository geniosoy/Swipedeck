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
int volume = 70;

// Sine burst with a fast attack and exponential decay; the pitch glides from f0 to f1.
void synth(Sound s, float f0, float f1, float ms, float amp, float decayMs) {
  Clip& c = clips[static_cast<int>(s)];
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

void buildClips() {
  synth(Sound::Tap, 2400, 2400, 40, 0.55f, 9);    // crisp tick
  synth(Sound::Nav, 1500, 2300, 70, 0.50f, 24);   // rising "open"
  synth(Sound::Back, 2300, 1500, 70, 0.50f, 24);  // falling "back"
  synth(Sound::Swipe, 900, 700, 35, 0.35f, 12);   // soft thud
  synth(Sound::Error, 500, 420, 160, 0.55f, 70);  // low buzz-ish
}

void audioTask(void*) {
  uint8_t id;
  for (;;) {
    if (xQueueReceive(queue, &id, portMAX_DELAY) != pdTRUE) continue;
    const Clip& c = clips[id];
    if (!c.pcm || !enabled) continue;
    size_t written = 0;
    i2s_write(kPort, c.pcm, c.frames * 2 * sizeof(int16_t), &written, portMAX_DELAY);
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
