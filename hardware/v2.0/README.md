# 8b8 hardware v2.0

RP2040 board for three YM2149 / AY-3-8910 sound chips, with eight analog
and eight switch inputs on terminal blocks. Everything except the sound chips
and the through-hole connectors is SMD with an LCSC part number, so JLC can
assemble it.

| File | What it is |
|---|---|
| `8b8_v2.kicad_pro`, `.kicad_sch`, `.kicad_pcb` | KiCad 9 project |
| `tools/design.py` | **The netlist, as data.** Every part, footprint, LCSC number and net. |
| `tools/gen_sch.py` | Writes the schematic from `design.py` |
| `tools/gen_pcb.py` | Writes the unrouted board: placement, outline, planes, silkscreen |
| `tools/route.py` | Autoroutes with Freerouting, then adds the copper pours |
| `tools/fixroute.py` | Small A* router for connections the autorouter left open |
| `tools/stitch.py` | Ground vias: beside stranded pads, a 4 mm grid, and inside walled-in pockets |
| `tools/cleanup.py` | Removes dangling track leftovers |
| `tools/check_netlist.py` | Checks that the schematic KiCad reads back matches `design.py` |
| `tools/check_pcb.py` | Checks every pad's net against the schematic, then runs DRC |
| `tools/fab.py` | Writes the JLC package into `fab/` |
| `fab/` | Gerbers (zip), BOM, CPL, and the list of through-hole parts |
| `renders/` | 3D views of the board |

## Status

Checked on the board as committed:

- **Schematic:** KiCad reads back the same 111 nets as `design.py`, with 0 mismatches.
  ERC shows 8 warnings, all one intended thing: the three chips' analog outputs
  are summed, as on v1.0. It's downgraded from error to warning in the project file.
- **Board:** 110 x 90 mm, 4 layers. **DRC: 0 errors, 0 unconnected, 0 schematic
  parity issues.** Every pad carries the right net. 6 cosmetic silkscreen
  warnings remain (a few labels touching vias); JLC clips silk off copper.
- **Stackup:** F.Cu signals, In1 solid GND, In2 signals plus +5V / +3V3 pours,
  B.Cu signals plus GND pour, ~350 stitching vias.
- **Rules:** 0.2 mm tracks, 0.15 mm clearance, 0.25 mm power, 0.6 / 0.3 mm vias.
  All standard JLC 4-layer capability.

### Before ordering

1. **Open it in KiCad and look.** It's autorouted. It's electrically complete
   and DRC-clean, but nobody has eyeballed it yet. Check the RP2040 and crystal
   area in particular, and tidy anything that looks odd.
2. **Check part rotations in JLC's CPL preview.** KiCad and JLC disagree on the
   zero rotation of some footprints (SOT-23, SOIC, the USB-C). JLC shows a
   preview after upload. Rotate any part whose pin 1 sits in the wrong corner.
3. **Through-hole parts** (sockets, terminal blocks) are in `fab/hand_solder.txt`.
   Add them as JLC through-hole assembly, or solder them yourself.
4. **Extended parts:** most ICs are JLC "Extended" library parts, so expect the
   per-part setup fee on top of the parts cost.

## Rebuilding

```bash
cd hardware/v2.0/tools
python3 gen_sch.py && python3 check_netlist.py
/usr/bin/python3 gen_pcb.py                        # needs KiCad's pcbnew module
DISPLAY=:0 /usr/bin/python3 route.py /path/to/freerouting-1.9.0.jar 15
DISPLAY=:0 /usr/bin/python3 route.py /path/to/freerouting-1.9.0.jar 8   # second pass
/usr/bin/python3 fixroute.py && /usr/bin/python3 stitch.py && /usr/bin/python3 stitch.py --islands
/usr/bin/python3 cleanup.py && /usr/bin/python3 check_pcb.py
/usr/bin/python3 fab.py
```

Freerouting 1.9 needs a display; it shows a window while it works. Autorouting
isn't deterministic, so a rebuild won't produce exactly this board. The last
few connections on this one were finished with a rip-up and reroute around
three debounce caps.

`gen_pcb.py` overwrites the board. **Now that it's routed, edit the layout by
hand in KiCad** and use *Update PCB from Schematic* for any circuit change. The
footprints are linked to the schematic symbols, so that works.

## What changed from v1

| | v1.0 (Pro Micro) | v2.0 |
|---|---|---|
| MCU | Pro Micro module on headers (ATmega32U4) | RP2040 on the board, 4 MB QSPI flash, 12 MHz crystal |
| Programming | Arduino bootloader | Hold BOOT, plug in USB, copy a `.uf2`. No programmer needed. |
| Logic to the chips | 5V straight from the 32U4 | Two 74HCT245 buffers (3.3V in, 5V out) |
| Warp Zone bends | A, B, X, Y (data bits DA1, DA0, DA5, DA4) on J3 | **Removed.** The firmware's Warp Zone does this safely. |
| CLK / RESET on J3 | `/SEL` (the half-clock bend) and `/RESET` | **Removed.** Clock Warp does the clock; reset is internal. |
| Inputs | none | 8 analog (74HC4051 to ADC0), 8 switch (74HC165) |
| Power | USB via Pro Micro | USB-C, 0.75 A polyfuse, AP2112K 3.3 V regulator |
| Audio out | 1k / 47n / 1u / 100k network | Same network, same values |
| MIDI in | 6N137, 220R, 1N4148 | Same circuit; output pulled up to 3.3 V |

## RP2040 pin map (for the firmware port)

| GPIO | Signal | Notes |
|---|---|---|
| 0-7 | DA0-DA7 | Data bus via U5. One masked write: `gpio_put_masked(0xFF, b)` |
| 8, 9 | BDIR_A, BC2_A | Chip A control via U6 |
| 10, 11 | BDIR_B, BC2_B | Chip B |
| 12, 13 | BDIR_C, BC2_C | Chip C |
| 14 | /RESET | All three chips. Pulled low until firmware drives it. |
| 15 | LED MIDI | Active high |
| 16 | spare | Test pad (UART0 TX, so a future MIDI out) |
| 17 | MIDI RX | UART0 RX, 31250 baud |
| 18, 19, 20 | MUX S0, S1, S2 | 74HC4051 channel select |
| 21 | CLOCK | GPOUT0. `clk_sys / 125` at 125 MHz gives exactly 1 MHz. Through U6, then 75R, then pin 22 of each chip. |
| 22, 23, 24 | SR /PL, CP, Q7 | 74HC165 |
| 25 | LED STATUS | Active high |
| 26 | MUX Z | ADC0 |
| 27, 28, 29 | spare | Test pads; ADC1-3 |

BC1 is grounded on every chip, the same reduced scheme as v1, so the
register-write sequence is unchanged.

Inside U5 and U6, channel *k* carries bit 7−*k*. That makes the bus run
straight from the RP2040 to the chips without crossing. It doesn't affect the
firmware: GPIO*n* still drives DA*n*.

### Reading the inputs

- **Analog:** set S2..S0 to the channel, wait about 10 µs, then read ADC0.
  Each input has 1k series and 10nF to ground. Pot ends go to the terminal's
  3V3 and GND pins, so the reading is ratiometric.
- **Switches:** pulse /PL low to latch, then clock out 8 bits on CP and read Q7.
  D0 (switch 1) comes out first. Inputs are active low: closed = 0. Each has
  a 10k pull-up and 1k/100nF debounce.

## Open questions

1. **v1.0 C2 is labelled "47uF" but drawn as a 5 mm disc ceramic.** v2.0 uses
   47 nF (3.4 kHz low-pass with the 1k). If your built v1 boards really have
   47 µF there, change it in `design.py`.
2. **v1.0 MIDI terminal labels.** v1's J3 labels the opto-cathode side "MIDI 4"
   and the 220R side "MIDI 5". The MIDI spec has DIN pin 4 on the resistor side,
   so v2.0 labels it that way. The circuit is identical; only the silkscreen
   differs.
3. **/A9, A8 and /SEL are left open, as on v1.0.** The chips pull them to the
   normal state. Tie them explicitly if you prefer.
