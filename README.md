# 8b8 — 8-Bit 8asterd firmware

Arduino Leonardo driving three AY-3-8910A chips as a MIDI synth.

## Layout

- `8b8_firmware.ino` — the firmware
- `generate.py` — **single source of truth** for controllable parameters
- `parameters.h` — GENERATED. Do not edit by hand.
- `index.html` — GENERATED. Web Serial control panel (Chrome/Edge only).

## Workflow

Edit `PARAMS` / `PRESETS` in `generate.py`, then:

    python3 generate.py
    arduino-cli compile --fqbn arduino:avr:leonardo .
    arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:leonardo .

`generate.py` hashes the parameter definition into a layout-version byte.
When that byte changes the firmware ignores any saved EEPROM bank and boots
on defaults, so stale saves can never be misread as the new layout.

Open `index.html` directly in Chrome/Edge to control the unit. Close the tab
before flashing or the upload will fail on a busy serial port.

## Constraints

Leonardo (ATmega32u4): 28,672 bytes flash, 2,560 bytes RAM. RAM is the tight
one — keep constant tables in PROGMEM and read them with `pgm_read_*`.

Struct definitions must stay above the first function definition in the
.ino: the Arduino preprocessor inserts generated prototypes there, and a
type declared later breaks the build.
