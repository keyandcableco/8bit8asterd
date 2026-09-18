// Arduino / AVR compatibility shim.
//
// Lets the real, unmodified 8b8 firmware compile and run off the Leonardo:
// natively under g++ for testing, and under Emscripten for the browser. The
// point is that the emulator runs THE FIRMWARE, not a second implementation
// of it that would drift away from the hardware the moment either changed.
//
// Only what the firmware actually touches is provided. Everything here is a
// plain host-side stand-in: no timing fidelity is claimed except for
// millis()/micros(), which the host advances explicitly so the synth's
// 100Hz tick and the Warp Zone's microsecond tick behave as they do on
// hardware.
#pragma once

#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <string>

// ---------------------------------------------------------------------------
// PROGMEM. On a host there is one address space, so these are all no-ops.
// ---------------------------------------------------------------------------
#define PROGMEM
#define pgm_read_byte(addr)  (*(const uint8_t  *)(addr))
#define pgm_read_word(addr)  (*(const uint16_t *)(addr))
#define memcpy_P             memcpy
#define F(s)                 (s)

// ---------------------------------------------------------------------------
// Pins. The firmware resolves pins to a port register and a bitmask once at
// startup; here every pin gets its own byte, so a "port" is just that byte
// and the mask is always 1. Register writes are intercepted further up (see
// AY_EMULATOR in the firmware), so nothing needs to decode the bus.
// ---------------------------------------------------------------------------
typedef uint8_t byte;

#define HIGH 1
#define LOW  0
#define OUTPUT 1
#define INPUT  0
#define INPUT_PULLUP 2

extern uint8_t emuPins[32];

inline uint8_t  digitalPinToPort(uint8_t pin)     { return pin; }
inline uint8_t  digitalPinToBitMask(uint8_t)      { return 1; }
inline volatile uint8_t *portOutputRegister(uint8_t port) { return &emuPins[port & 31]; }
inline void     pinMode(uint8_t, uint8_t)         {}
inline void     digitalWrite(uint8_t pin, uint8_t v) { emuPins[pin & 31] = v ? 1 : 0; }
inline int      digitalRead(uint8_t pin)          { return emuPins[pin & 31]; }

// Analog pin names the firmware uses for BDIR/BC2 on chips B and C.
static const uint8_t A0 = 18, A1 = 19, A2 = 20, A3 = 21, A4 = 22, A5 = 23;

template <typename T> T constrain(T v, T lo, T hi) { return v < lo ? lo : (v > hi ? hi : v); }

// ---------------------------------------------------------------------------
// Time. The host owns the clock and advances it a block at a time, so the
// firmware's millis()/micros() scheduling stays exactly as written.
// ---------------------------------------------------------------------------
extern uint64_t emuMicros;
inline unsigned long micros() { return (unsigned long)emuMicros; }
inline unsigned long millis() { return (unsigned long)(emuMicros / 1000ULL); }
inline void delay(unsigned long ms) { emuMicros += (uint64_t)ms * 1000ULL; }

// ---------------------------------------------------------------------------
// AVR registers. The firmware only configures Timer1 (the 1MHz chip clock),
// which the emulator models directly, so these just need somewhere to live.
// ---------------------------------------------------------------------------
extern uint8_t  TCCR1A, TCCR1B, TCCR1C, TIMSK0, TIMSK1, SREG;
extern uint16_t OCR1A, TCNT1;
// The firmware writes the 16-bit compare register as two bytes.
extern uint8_t  OCR1AH, OCR1AL;
#define WGM10 0
#define WGM11 1
#define WGM12 3
#define WGM13 4
#define CS10  0
#define CS11  1
#define CS12  2
#define COM1A0 6
#define COM1A1 7
#define OCIE1A 1
inline void cli() {}
inline void sei() {}

// ---------------------------------------------------------------------------
// EEPROM. Backed by a plain array; the host can persist it if it wants to.
// ---------------------------------------------------------------------------
extern uint8_t emuEeprom[1024];
struct EEPROMClass {
  uint8_t read(int a)              { return emuEeprom[a & 1023]; }
  void    write(int a, uint8_t v)  { emuEeprom[a & 1023] = v; }
  void    update(int a, uint8_t v) { if (emuEeprom[a & 1023] != v) emuEeprom[a & 1023] = v; }
};
extern EEPROMClass EEPROM;

// ---------------------------------------------------------------------------
// Serial. This is the control protocol the web panel speaks, so it is a real
// two-way queue rather than a sink: the host pushes command lines in and
// drains reply lines out.
// ---------------------------------------------------------------------------
#define DEC 10
#define HEX 16

class EmuSerial {
public:
  void begin(unsigned long) {}
  int  available()        { return (int)(rxBuf.size() - rxPos); }
  int  read()             { return rxPos < rxBuf.size() ? (uint8_t)rxBuf[rxPos++] : -1; }

  void print(const char *s)      { txBuf += s; }
  void print(char c)             { txBuf += c; }
  void print(int v, int base = DEC)           { emit((long)v, base); }
  void print(long v, int base = DEC)          { emit(v, base); }
  void print(unsigned v, int base = DEC)      { emit((long)v, base); }
  void print(unsigned long v, int base = DEC) { emit((long)v, base); }
  void print(uint8_t v, int base = DEC)       { emit((long)v, base); }

  void println()                 { txBuf += '\n'; }
  void println(const char *s)    { txBuf += s; txBuf += '\n'; }
  void println(char c)           { txBuf += c; txBuf += '\n'; }
  template <typename T> void println(T v, int base = DEC) { print(v, base); txBuf += '\n'; }

  // host side
  void pushLine(const std::string &line) { rxBuf += line; rxBuf += '\n'; }
  std::string drain() { std::string o; o.swap(txBuf); return o; }
  void compact() { if (rxPos && rxPos == rxBuf.size()) { rxBuf.clear(); rxPos = 0; } }

private:
  void emit(long v, int base) {
    char tmp[24];
    if (base == HEX) snprintf(tmp, sizeof tmp, "%lX", v);
    else             snprintf(tmp, sizeof tmp, "%ld", v);
    txBuf += tmp;
  }
  std::string rxBuf, txBuf;
  size_t rxPos = 0;
};

extern EmuSerial Serial;
extern EmuSerial Serial1;
typedef EmuSerial HardwareSerial;
