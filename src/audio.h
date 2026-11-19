// Sound feedback: ES8311 codec + class-D amp. Short synthesised UI sounds are played from a
// small task so touch handling never blocks on audio.
#pragma once
#include <Arduino.h>

enum class Sound : uint8_t { Tap, Nav, Back, Swipe, Error, Alarm, kCount };  // Alarm (the timer) also rings with key sounds off

// Starts I2S (MCLK first), initialises the codec over the shared Wire bus and enables the amp.
// Call after displayBegin() (which starts Wire for the touch controller). Returns false on codec failure.
bool audioBegin();
bool audioReady();

void audioPlay(Sound s);       // non-blocking, drops the request if the queue is full
void audioSetVolume(int pct);  // 0..100 (codec DAC volume)
int audioVolume();
void audioSetAmp(bool on);     // debug: force the amplifier pin
void audioSetEnabled(bool on); // user preference "key sounds on/off"
void audioSetNavSounds(bool on);  // folder / back sounds on or off (key presses follow audioSetEnabled)

constexpr int kAudioThemes = 4;   // 0 classic, 1 soft, 2 click, 3 chirp
void audioSetTheme(int theme);    // rebuilds the clips (waits for a playing clip to end)
int audioTheme();
