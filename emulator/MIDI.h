// FortySevenEffects MIDI library stub. DIN MIDI has no meaning in a browser,
// so read() always reports "nothing arrived" and the firmware falls through
// to its USB MIDI path.
#pragma once
#include <stdint.h>

#define MIDI_CHANNEL_OMNI 0

namespace midi {
  enum MidiType : uint8_t {
    InvalidType = 0x00, NoteOff = 0x80, NoteOn = 0x90,
    AfterTouchPoly = 0xA0, ControlChange = 0xB0, ProgramChange = 0xC0,
    AfterTouchChannel = 0xD0, PitchBend = 0xE0, SystemExclusive = 0xF0,
  };
}
using namespace midi;

template <typename Transport, Transport &transport>
class MidiInterface {
public:
  void begin(uint8_t) {}
  bool read() { return false; }
  uint8_t getType()     { return InvalidType; }
  uint8_t getChannel()  { return 1; }
  uint8_t getData1()    { return 0; }
  uint8_t getData2()    { return 0; }
};

#define MIDI_CREATE_INSTANCE(Transport, Inst, Name) \
  MidiInterface<Transport, Inst> Name;
