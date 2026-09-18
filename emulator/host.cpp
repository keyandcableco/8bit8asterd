// Host for the 8b8 emulator.
//
// Includes the real firmware as a single translation unit and drives it the
// way the Leonardo does: call setup() once, then call loop() repeatedly
// while time advances, and let the three emulated chips turn the register
// writes into audio. The firmware source is unmodified apart from one
// four-line AY_EMULATOR seam in writeReg().
//
// Built two ways from this same file:
//   build-native.sh  g++, renders a WAV, used by the tests
//   build-wasm.sh    emcc, exported to JavaScript for the web page
#include <stdint.h>
#include <string>
#include <vector>
#include <cmath>

#include "arduino_shim.h"
#include "ay8910.h"
#include "MIDIUSB.h"

// ---- storage for everything the shim declares ------------------------------
uint8_t  emuPins[32];
uint64_t emuMicros = 0;
uint8_t  TCCR1A = 0, TCCR1B = 0, TCCR1C = 0, TIMSK0 = 0, TIMSK1 = 0, SREG = 0;
uint16_t OCR1A = 0, TCNT1 = 0;
uint8_t  OCR1AH = 0, OCR1AL = 0;
uint8_t  emuEeprom[1024];
EEPROMClass EEPROM;
EmuSerial Serial;
EmuSerial Serial1;
MidiUSB_  MidiUSB;

// ---- the three chips -------------------------------------------------------
static AY8910 chips[3];

void emuWriteReg(uint8_t chip, unsigned char reg, unsigned char db) {
  if (chip < 3) chips[chip].write(reg, db);
}

// The firmware is one translation unit, exactly as the Arduino IDE builds it.
#define AY_EMULATOR 1
#include "../8b8_firmware.ino"

// ---------------------------------------------------------------------------
// Audio
// ---------------------------------------------------------------------------
// The chip model steps at clock/8 (see ay8910.h for why). The firmware
// programs Timer1 for a 1MHz master clock, so that is 125000 steps a
// second, box-filtered down to whatever rate the host asks for.
static const double CHIP_STEP_HZ = 1000000.0 / 8.0;

// Real boards AC-couple the chip output. Without modelling that, the steady
// DC level a muted channel puts out shows up as offset in the render.
static double  dcPrev = 0.0, dcOut = 0.0;
// ---------------------------------------------------------------------------
// Step sequencer clock
// ---------------------------------------------------------------------------
// Timing lives here rather than in JavaScript because setTimeout is at the
// mercy of the main thread, and on this page the audio callback IS on the
// main thread. Counting samples inside the render makes the beat exact: a
// step lands on the sample it should, no matter what the browser is doing.
static uint16_t seqMask[16];        // one bit per step
static uint8_t  seqNote[16];
static int      seqRows = 0, seqSteps = 16, seqCurStep = 0;
static bool     seqRunning = false;
static double   seqSamplesPerStep = 0.0, seqAcc = 0.0;

static double  sampleRate   = 44100.0;
static double  stepCarry    = 0.0;
static double  loopAccUs    = 0.0;
static bool    started      = false;

extern "C" {

void emu_init(double rate) {
  sampleRate = rate;
  stepCarry = 0.0;
  loopAccUs = 0.0;
  dcPrev = dcOut = 0.0;
  emuMicros = 0;
  memset(emuEeprom, 0xFF, sizeof emuEeprom);
  for (int i = 0; i < 3; i++) chips[i].reset();
  seqRunning = false; seqRows = 0; seqCurStep = 0; seqAcc = 0.0;
  memset(seqMask, 0, sizeof seqMask);
  setup();
  started = true;
}

// Renders n samples into out, running the firmware's loop() as time passes.
void emu_render(float *out, int n) {
  if (!started) return;
  const double stepsPerSample = CHIP_STEP_HZ / sampleRate;
  const double usPerSample    = 1000000.0 / sampleRate;

  for (int i = 0; i < n; i++) {
    // Advance the firmware's clock, then service it. loop() used to run once
    // per sample, which is 44100 times a second of audio for a firmware whose
    // fastest timer is 4kHz. Polling at ~8kHz is ample and cuts the work by
    // about five sixths, which matters on a phone where this shares a thread
    // with the audio callback.
    emuMicros += (uint64_t)usPerSample;
    loopAccUs += usPerSample;
    if (loopAccUs >= 125.0) {      // ~8kHz
      loopAccUs = 0.0;
      loop();
    }

    // Fire sequencer steps on their exact sample.
    if (seqRunning && seqSamplesPerStep > 0.0) {
      seqAcc += 1.0;
      if (seqAcc >= seqSamplesPerStep) {
        seqAcc -= seqSamplesPerStep;
        for (int r = 0; r < seqRows; r++) {
          if (seqMask[r] & (1u << seqCurStep)) {
            MidiUSB.push(midiEventPacket_t{0x09, 0x99, seqNote[r], 110});
          }
        }
        seqCurStep = (seqCurStep + 1) % seqSteps;
      }
    }

    stepCarry += stepsPerSample;
    int steps = (int)stepCarry;
    stepCarry -= steps;
    if (steps < 1) steps = 1;

    float acc = 0.0f;
    for (int s = 0; s < steps; s++) {
      acc += chips[0].step() + chips[1].step() + chips[2].step();
    }
    double v = acc / (double)(steps * 3);
    dcOut = v - dcPrev + 0.9995 * dcOut;   // ~3.5Hz single-pole high pass
    dcPrev = v;
    out[i] = (float)dcOut;
  }
}

// ---- sequencer -------------------------------------------------------------
void emu_seq_row(int row, int note, int mask) {
  if (row < 0 || row >= 16) return;
  seqNote[row] = (uint8_t)note;
  seqMask[row] = (uint16_t)mask;
  if (row + 1 > seqRows) seqRows = row + 1;
}

void emu_seq_start(double bpm, int steps) {
  if (bpm < 20.0) bpm = 20.0;
  seqSteps = (steps > 0 && steps <= 16) ? steps : 16;
  seqSamplesPerStep = sampleRate * 60.0 / bpm / 4.0;   // sixteenth notes
  seqCurStep = 0;
  seqAcc = 0.0;
  seqRunning = true;
}

void emu_seq_tempo(double bpm) {
  if (bpm < 20.0) bpm = 20.0;
  seqSamplesPerStep = sampleRate * 60.0 / bpm / 4.0;
}

void emu_seq_stop() { seqRunning = false; }

int emu_seq_step() { return seqRunning ? seqCurStep : -1; }

// ---- MIDI in ---------------------------------------------------------------
void emu_note_on(int channel, int note, int velocity) {
  MidiUSB.push(midiEventPacket_t{0x09, (uint8_t)(0x90 | (channel & 0x0F)),
                                 (uint8_t)note, (uint8_t)velocity});
}
void emu_note_off(int channel, int note) {
  MidiUSB.push(midiEventPacket_t{0x08, (uint8_t)(0x80 | (channel & 0x0F)),
                                 (uint8_t)note, 0});
}
void emu_cc(int channel, int cc, int value) {
  MidiUSB.push(midiEventPacket_t{0x0B, (uint8_t)(0xB0 | (channel & 0x0F)),
                                 (uint8_t)cc, (uint8_t)value});
}

// ---- the control protocol, same lines the web panel already speaks ---------
void emu_send_line(const char *line) {
  Serial.pushLine(std::string(line));
}

static std::string drained;
const char *emu_read_lines() {
  drained = Serial.drain();
  Serial.compact();
  return drained.c_str();
}

// ---- introspection, for the tests ------------------------------------------
int emu_reg(int chip, int reg) {
  if (chip < 0 || chip > 2) return -1;
  return chips[chip].read((uint8_t)reg);
}
int emu_voice_playing(int v) {
  if (v < 0 || v >= MAX_VOICES) return -1;
  return m_playing[v];
}

} // extern "C"
