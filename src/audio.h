// Sound feedback: ES8311 codec + class-D amp. Short synthesised UI sounds are played from a
// small task so touch handling never blocks on audio.
#pragma once
#include <Arduino.h>

enum class Sound : uint8_t { Tap, Nav, Back, Swipe, Error, kCount };

// Starts I2S (MCLK first), initialises the codec over the shared Wire bus and enables the amp.
// Call after displayBegin() (which starts Wire for the touch controller). Returns false on codec failure.
bool audioBegin();
bool audioReady();

void audioPlay(Sound s);       // non-blocking, drops the request if the queue is full
void audioSetVolume(int pct);  // 0..100 (codec DAC volume)
int audioVolume();
void audioSetAmp(bool on);     // debug: force the amplifier pin
void audioSetEnabled(bool on); // user preference "key sounds on/off"
