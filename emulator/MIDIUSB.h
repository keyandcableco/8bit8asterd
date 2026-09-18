// MIDIUSB stub. The host injects note events straight into this queue, which
// is exactly what the real library hands the firmware.
#pragma once
#include <stdint.h>
#include <deque>

#define MIDIUSB_h

typedef struct {
  uint8_t header;
  uint8_t byte1;
  uint8_t byte2;
  uint8_t byte3;
} midiEventPacket_t;

class MidiUSB_ {
public:
  midiEventPacket_t read() {
    if (q.empty()) return midiEventPacket_t{0, 0, 0, 0};
    midiEventPacket_t e = q.front();
    q.pop_front();
    return e;
  }
  void flush() {}
  void sendMIDI(midiEventPacket_t) {}
  void push(midiEventPacket_t e) { q.push_back(e); }   // host side
private:
  std::deque<midiEventPacket_t> q;
};
extern MidiUSB_ MidiUSB;
