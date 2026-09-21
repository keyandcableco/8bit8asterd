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
extern "C" void emu_wave_tick();
#include "../8b8_firmware.ino"

// ---------------------------------------------------------------------------
// Audio
// ---------------------------------------------------------------------------
// The chip model steps at clock/8 (see ay8910.h for why). The firmware
// programs Timer1 for a 1MHz master clock, so that is 125000 steps a
// second, box-filtered down to whatever rate the host asks for.
// The firmware generates the AY clock on Timer1, so the chips run at
// 16MHz / (2 * (1 + OCR1A)) -- 1MHz at the default divisor of 7. Reading it
// back each buffer is what lets Clock Warp work here: the emulator used a
// fixed 1MHz and would simply have ignored the whole effect.
static double chipClockHz() {
  const double d = (double)OCR1AL;
  return 16000000.0 / (2.0 * (1.0 + (d < 1.0 ? 1.0 : d)));
}

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
#define SEQ_MAX_ROWS 32
#define SEQ_MAX_STEPS 64
// A length per cell rather than a bit per step: 0 is empty, otherwise how
// many steps the note is held for. A bitmask could not express that, and it
// capped the pattern at the 16 bits it happened to have.
static uint8_t  seqCell[SEQ_MAX_ROWS][SEQ_MAX_STEPS];
static uint8_t  seqNote[SEQ_MAX_ROWS];
static uint8_t  seqChan[SEQ_MAX_ROWS];     // 9 = percussion, else melodic
static double   seqGateLeft[SEQ_MAX_ROWS]; // samples until note-off, 0 = idle
static uint8_t  seqGateNote[SEQ_MAX_ROWS];
static int      seqRows = 0, seqSteps = 16, seqCurStep = 0;
static bool     seqRunning = false;
static double   seqSamplesPerStep = 0.0, seqAcc = 0.0;

static double  sampleRate   = 44100.0;
static double  stepCarry    = 0.0;
static double  loopAccUs    = 0.0;
static double  waveAccUs    = 0.0;
static bool    started      = false;

extern "C" {

void emu_init(double rate) {
  sampleRate = rate;
  stepCarry = 0.0;
  loopAccUs = 0.0;
  waveAccUs = 0.0;
  dcPrev = dcOut = 0.0;
  emuMicros = 0;
  memset(emuEeprom, 0xFF, sizeof emuEeprom);
  for (int i = 0; i < 3; i++) chips[i].reset();
  seqRunning = false; seqRows = 0; seqCurStep = 0; seqAcc = 0.0;
  memset(seqCell, 0, sizeof seqCell);
  memset(seqChan, 9, sizeof seqChan);
  memset(seqGateLeft, 0, sizeof seqGateLeft);
  setup();
  started = true;
}

// Renders n samples into out, running the firmware's loop() as time passes.
void emu_render(float *out, int n) {
  if (!started) return;
  const double stepsPerSample = (chipClockHz() / 8.0) / sampleRate;
  const double usPerSample    = 1000000.0 / sampleRate;

  for (int i = 0; i < n; i++) {
    // Advance the firmware's clock, then service it. loop() used to run once
    // per sample, which is 44100 times a second of audio for a firmware whose
    // fastest timer is 4kHz. Polling at ~8kHz is ample and cuts the work by
    // about five sixths, which matters on a phone where this shares a thread
    // with the audio callback.
    // The wavetable voice runs off Timer3 on the hardware. Here it is
    // ticked from the render loop at the same rate, or the emulator would
    // simply not have the feature.
    waveAccUs += usPerSample;
    while (waveAccUs >= (1000000.0 / 16000.0)) {
      waveAccUs -= (1000000.0 / 16000.0);
      emu_wave_tick();
    }

    emuMicros += (uint64_t)usPerSample;
    loopAccUs += usPerSample;
    if (loopAccUs >= 125.0) {      // ~8kHz
      loopAccUs = 0.0;
      loop();
    }

    // Fire sequencer steps on their exact sample.
    if (seqRunning && seqSamplesPerStep > 0.0) {
      // Melodic steps need releasing; percussion is a one-shot and does not.
      for (int r = 0; r < seqRows; r++) {
        if (seqGateLeft[r] > 0.0) {
          seqGateLeft[r] -= 1.0;
          if (seqGateLeft[r] <= 0.0) {
            seqGateLeft[r] = 0.0;
            MidiUSB.push(midiEventPacket_t{0x08, (uint8_t)(0x80 | (seqChan[r] & 0x0F)),
                                           seqGateNote[r], 0});
          }
        }
      }

      seqAcc += 1.0;
      if (seqAcc >= seqSamplesPerStep) {
        seqAcc -= seqSamplesPerStep;
        for (int r = 0; r < seqRows; r++) {
          const uint8_t len = seqCell[r][seqCurStep];
          if (!len) continue;
          const uint8_t ch = seqChan[r] & 0x0F;
          MidiUSB.push(midiEventPacket_t{0x09, (uint8_t)(0x90 | ch), seqNote[r], 110});
          if (ch != 9) {
            // Release anything this row still holds before re-striking.
            if (seqGateLeft[r] > 0.0) {
              MidiUSB.push(midiEventPacket_t{0x08, (uint8_t)(0x80 | ch), seqGateNote[r], 0});
            }
            seqGateNote[r] = seqNote[r];
            // Held for its own length, less a sliver so a note butted up
            // against the next one still articulates instead of slurring.
            seqGateLeft[r] = seqSamplesPerStep * ((double)len - 0.08);
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
void emu_seq_row(int row, int note, int chan) {
  if (row < 0 || row >= SEQ_MAX_ROWS) return;
  seqNote[row] = (uint8_t)note;
  seqChan[row] = (uint8_t)chan;
  if (row + 1 > seqRows) seqRows = row + 1;
}

// Cells are sent one at a time rather than as a block, because a pattern is
// sparse: a busy sixteen-row, sixty-four-step grid still has well under a
// hundred filled cells, and this keeps the boundary a plain number call.
void emu_seq_cell(int row, int step, int len) {
  if (row < 0 || row >= SEQ_MAX_ROWS) return;
  if (step < 0 || step >= SEQ_MAX_STEPS) return;
  seqCell[row][step] = (uint8_t)(len < 0 ? 0 : (len > SEQ_MAX_STEPS ? SEQ_MAX_STEPS : len));
}

void emu_seq_clear() {
  memset(seqCell, 0, sizeof seqCell);
}

void emu_seq_start(double bpm, int steps) {
  if (bpm < 20.0) bpm = 20.0;
  seqSteps = (steps > 0 && steps <= SEQ_MAX_STEPS) ? steps : 16;
  for (int r = 0; r < SEQ_MAX_ROWS; r++) seqGateLeft[r] = 0.0;
  seqSamplesPerStep = sampleRate * 60.0 / bpm / 4.0;   // sixteenth notes
  seqCurStep = 0;
  seqAcc = 0.0;
  seqRunning = true;
}

void emu_seq_tempo(double bpm) {
  if (bpm < 20.0) bpm = 20.0;
  seqSamplesPerStep = sampleRate * 60.0 / bpm / 4.0;
}

void emu_seq_stop() {
  seqRunning = false;
  // Release anything still held, or a melodic row would hang on stop.
  for (int r = 0; r < seqRows; r++) {
    if (seqGateLeft[r] > 0.0) {
      MidiUSB.push(midiEventPacket_t{0x08, (uint8_t)(0x80 | (seqChan[r] & 0x0F)),
                                     seqGateNote[r], 0});
      seqGateLeft[r] = 0.0;
    }
  }
}

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
