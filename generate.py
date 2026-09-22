#!/usr/bin/env python3
# 8-Bit 8asterd -- parameter bank generator.
#
# Copyright (C) 2026 Greg Miller, The Key & Cable Company
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. This program is distributed WITHOUT ANY WARRANTY; see
# the GNU General Public License for more details:
# <https://www.gnu.org/licenses/>.

"""
8b8 parameter bank generator
============================

Single source of truth for the 8-Bit 8asterd's controllable parameters.
Edit PARAMS / PRESETS below, run this script, and it regenerates:

  parameters.h   -> include from the firmware (.ino). Param indices, ranges,
                    defaults, and a layout-version byte used to invalidate
                    stale EEPROM saves whenever the definition changes.
  index.html     -> the Web Serial control panel (Chrome/Edge, open the file
                    directly). All controls, groupings, display scaling and
                    factory presets are generated from PARAMS/PRESETS.

Protocol (USB-CDC serial, 115200, newline-terminated ASCII):
  P:<idx>:<value>   set one parameter        -> reply  V:<idx>:<clamped>
  DUMP              request all values       -> reply  PRESET:<v0>,<v1>,...
  LOAD:<v0>,<v1>,.. apply a full preset      -> reply  PRESET:...
  SAVE              persist params to EEPROM -> reply  SAVED:1
  DEFAULTS          restore factory defaults -> reply  PRESET:...

A preset is nothing more than the PRESET: CSV — an array of values in
parameter-index order, same idea as the minichord's parameter banks.
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# kind:    "toggle" (0/1 switch), "int" (slider), "enum" (button row; the
#          stored value is the option INDEX — firmware maps it to hardware
#          values where needed, e.g. buzz shape index -> AY shape register)
# scale/unit/offset: display only. shown = (value + offset) * scale
# help:    one line shown under the control
#
# All values must fit in a uint8_t (0..255) — the firmware stores the bank
# as a flat uint8_t array and EEPROM-persists it byte-for-byte.

PARAMS = [
    # --- Buzzy Bass --------------------------------------------------------
    dict(key="buzz_enable", label="Enable", group="Buzzy Bass", kind="toggle",
         default=0,
         help="Points the lowest held note's volume at the chip's envelope generator. Set to a looping shape it is an oscillator, so the note becomes a sawtooth or triangle rather than a square."),
    dict(key="buzz_ratio", label="Ratio", group="Buzzy Bass", kind="int",
         min=1, max=8, default=1,
         help="How far below the note the envelope oscillates: 1 is unison, 2 an octave down, 4 two octaves. At unison it is a waveform; lower it becomes a sub-oscillator."),
    dict(key="buzz_shape", label="Shape", group="Buzzy Bass", kind="enum",
         options=["Saw \u2193", "Tri \u2193\u2191", "Saw \u2191", "Tri \u2191\u2193"],
         default=0,
         help="Which looping envelope shape the hardware runs: 0 and 2 are sawtooths, 1 and 3 triangles. This is where a non-square waveform comes from -- the chip has no other."),
    dict(key="buzz_pure", label="Pure", group="Buzzy Bass", kind="toggle",
         default=0,
         help="Closes the channel's tone gate, so only the envelope reaches "
              "the output. Off, the square still gates the envelope and you "
              "hear the product of the two; on, you get the hardware "
              "sawtooth or triangle by itself."),
    dict(key="buzz_detune", label="Detune", group="Buzzy Bass", kind="int",
         min=0, max=64, default=32, offset=-32,
         help="Offsets the envelope against the square wave. Non-zero gives "
              "the sweeping 'MadMax buzzer' beat. 0 = locked together."),

    # --- Warp Zone (digital circuit bending) -------------------------------
    # Software versions of what a hardware bend does: hit the chip with
    # writes it was never meant to get, fast, while notes are sounding.
    # These write STRAIGHT to the chip, bypassing the register cache, so the
    # normal voice updates keep fighting them -- that fight is the sound.
    dict(key="warp_mode", label="Mode", group="Warp Zone", kind="enum",
         options=["Off", "Sync Buzz", "Stutter", "Scramble", "Zap",
                  "Tape Stop", "Siren", "Crush", "Ring", "Env Crush",
                  "SID Voice"],
         default=0,
         help="Sync Buzz restarts the envelope. Stutter gates the mixer. "
              "Scramble throws junk at registers. Zap sweeps noise. The "
              "last four move on their own: Tape Stop drags pitch down and "
              "snaps back, Siren sweeps it, Crush quantises it coarser and "
              "coarser, Ring is audio-rate amplitude modulation."),
    dict(key="warp_rate", label="Rate", group="Warp Zone", kind="int",
         min=1, max=120, default=30,
         help="Low = slow chopping. High reaches audio rate and becomes a "
              "tone in its own right. In Clock Warp, 1 holds the clock down "
              "instead of sweeping it, the way a clock-halving switch does."),
    dict(key="warp_depth", label="Depth", group="Warp Zone", kind="int",
         min=1, max=63, default=20,
         help="How violent the effect is."),
    dict(key="clock_enable", label="Enable", group="Clock Warp", kind="toggle",
         default=0,
         help="Wobbles the AY master clock, which the Leonardo generates, so "
              "it sits upstream of all three chips: pitch, noise colour, "
              "envelope rates and drum decays move together. It touches no "
              "chip register, so it runs alongside any Warp Zone mode."),
    dict(key="clock_drop", label="Drop", group="Clock Warp", kind="int",
         min=0, max=63, default=30,
         help="How far the clock falls. Around 45 matches the halving a "
              "YM2149 gives with its SEL pin low; full reaches 380kHz, below "
              "anything a hardware switch does."),
    dict(key="clock_sweep", label="Sweep", group="Clock Warp", kind="int",
         min=1, max=120, default=20,
         help="How fast the clock wanders. Only audible while Hold is below "
              "maximum, since a held clock does not sweep."),
    dict(key="clock_hold", label="Hold", group="Clock Warp", kind="int",
         min=0, max=63, default=0,
         help="Mixes between sweeping and sitting at the bottom of the "
              "sweep. Full is a clock-halving switch held down, which is "
              "where the coarse, slow character lives."),

    dict(key="warp_motion", label="Motion", group="Warp Zone", kind="int",
         min=0, max=64, default=0,
         help="Sweeps Rate up and down on its own, hands free. The sweep "
              "itself is usually the interesting part, not where it "
              "settles. 0 = Rate stays put."),

    # --- Vibrato -----------------------------------------------------------
    dict(key="wave_enable", label="Enable", group="Wavetable", kind="toggle",
         default=0,
         help="Takes over one channel and writes its volume register from a "
              "table at audio rate, tone off. The register becomes a 4-bit "
              "DAC, so the voice plays a real waveform instead of a square. "
              "Costs a voice and follows the highest note held."),
    dict(key="wave_shape", label="Shape", group="Wavetable", kind="enum",
         options=["Sine", "Triangle", "Saw", "Ramp", "Pulse 25%", "Vocal"],
         default=0,
         help="Sine and triangle are softer than anything the chip makes on "
              "its own. Pulse gives the narrow-square sound the AY cannot do "
              "in hardware. Vocal is a formant shape, somewhere near an 'ah'."),
    dict(key="wave_level", label="Level", group="Wavetable", kind="int",
         min=1, max=15, default=12,
         help="Peak of the waveform. Low values lose resolution fast, since "
              "there are only sixteen amplitude steps to draw it with."),

    dict(key="draw_enable", label="Enable", group="Drawbars", kind="toggle",
         default=0,
         help="Adds harmonics of each note on further voices, the way an "
              "organ's drawbars do. Summed squares at these ratios reshape "
              "the timbre rather than just thickening it, which is the only "
              "route to a new tone colour on a chip with no filter. Costs a "
              "voice per stop, so polyphony falls accordingly."),
    dict(key="draw_a", label="Stop 1", group="Drawbars", kind="enum",
         options=["Off", "16'", "5 1/3'", "4'", "2 2/3'", "2'", "1 3/5'", "1 1/3'", "1'"],
         default=3,
         help="Pitch of the first added stop, in organ footages against the "
              "note at 8'. 16' is an octave below, 4' an octave above, "
              "2 2/3' a twelfth above."),
    dict(key="draw_a_level", label="Level 1", group="Drawbars", kind="int",
         min=1, max=15, default=8,
         help="How loud the first stop sits under the note."),
    dict(key="draw_b", label="Stop 2", group="Drawbars", kind="enum",
         options=["Off", "16'", "5 1/3'", "4'", "2 2/3'", "2'", "1 3/5'", "1 1/3'", "1'"],
         default=0,
         help="Pitch of the second added stop. Off leaves the voice free for "
              "polyphony."),
    dict(key="draw_b_level", label="Level 2", group="Drawbars", kind="int",
         min=1, max=15, default=6,
         help="How loud the second stop sits under the note."),

    dict(key="uni_enable", label="Enable", group="Unison", kind="toggle",
         default=0,
         help="Doubles each melodic note on a second voice a little out of "
              "tune with the first. Halves how many notes can sound at once, "
              "and the beating between the pair is what makes it wide."),
    dict(key="uni_detune", label="Detune", group="Unison", kind="int",
         min=1, max=32, default=8,
         help="How far apart the pair sits. Low values beat slowly and "
              "thicken; high values drift towards a chorus or an out-of-tune "
              "honky-tonk."),

    dict(key="vib_enable", label="Enable", group="Vibrato", kind="toggle",
         default=0,
         help="Pitch LFO on all sounding melodic voices. Drums untouched."),
    dict(key="vib_rate", label="Rate", group="Vibrato", kind="int",
         min=1, max=200, default=50, scale=0.1, unit="Hz",
         help=""),
    dict(key="vib_depth", label="Depth", group="Vibrato", kind="int",
         min=0, max=31, default=6,
         help=""),
    dict(key="vib_delay", label="Delay", group="Vibrato", kind="int",
         min=0, max=200, default=0, scale=10, unit="ms",
         help="Onset delay after note start, then fades in."),

    # --- Tremolo -----------------------------------------------------------
    dict(key="trem_enable", label="Enable", group="Tremolo", kind="toggle",
         default=0,
         help="Volume LFO on melodic voices."),
    dict(key="trem_rate", label="Rate", group="Tremolo", kind="int",
         min=1, max=200, default=40, scale=0.1, unit="Hz",
         help=""),
    dict(key="trem_depth", label="Depth", group="Tremolo", kind="int",
         min=0, max=15, default=5,
         help="How many volume steps the LFO cuts (AY volume is 0\u201315)."),

    # --- Noise Blend -------------------------------------------------------
    dict(key="noise_enable", label="Enable", group="Noise Blend", kind="toggle",
         default=0,
         help="Mixes the AY noise generator into melodic voices."),
    dict(key="noise_period", label="Period", group="Noise Blend", kind="int",
         min=0, max=15, default=8,
         help="Higher = darker noise. Per-chip register, shared with drums."),

    # --- Drums (global shaping of the CH10 kit) ----------------------------
    # These scale the built-in kit table in the firmware rather than
    # replacing it, so one set of controls reshapes every drum at once and
    # still fits the flat preset array.
    dict(key="drum_tune", label="Tune", group="Drums", kind="int",
         min=50, max=200, default=100, unit="%",
         help="Pitch of every tuned drum. 100 = the kit as built."),
    dict(key="drum_decay", label="Decay", group="Drums", kind="int",
         min=50, max=200, default=100, unit="%",
         help="Length of every drum. Higher = longer tails."),
    dict(key="drum_bend", label="Bend", group="Drums", kind="int",
         min=0, max=200, default=100, unit="%",
         help="Pitch-drop amount on kicks and toms. 0 = flat, no sweep."),
    dict(key="drum_noise", label="Noise", group="Drums", kind="int",
         min=0, max=14, default=7, offset=-7,
         help="Noise colour. Negative = brighter/hissier, positive = darker."),

    # --- Drum FX -----------------------------------------------------------
    # Drums are the only voices driven by the AY envelope generator, so
    # these reach things the tone effects cannot: restarting the envelope
    # mid-hit, running it backwards, and varying it per strike.
    dict(key="drum_roll", label="Roll", group="Drum FX", kind="int",
         min=0, max=50, default=0, unit="Hz",
         help="Re-strikes the envelope while a drum is still ringing. Low "
              "values flam, high values become a buzz roll. The fader is "
              "spaced by ratio rather than by Hz, so it moves evenly by ear "
              "all the way up. 0 = off."),
    dict(key="drum_flam", label="Flam", group="Drum FX", kind="int",
         min=0, max=30, default=0, scale=10, unit="ms",
         help="Every hit fires a second time this long after. 0 = off."),
    dict(key="drum_reverse", label="Reverse", group="Drum FX", kind="toggle",
         default=0,
         help="Runs the envelope upward so drums swell instead of decay, "
              "then cut. Reverse-cymbal territory."),
    dict(key="drum_chaos", label="Chaos", group="Drum FX", kind="int",
         min=0, max=63, default=0,
         help="Randomises pitch, noise colour and length on every single "
              "hit, so no two strikes are identical."),

    # --- Auto FX (NES tracker style, automatic per note) -------------------
    # The 2A03's sweep unit did pitch slides in hardware; its chord and
    # retrigger sounds came from the tracker re-writing registers every
    # frame. Both are software here, applied automatically on every note.
    dict(key="arp_mode", label="Arp", group="Auto FX", kind="enum",
         options=["Off", "Maj", "Min", "Oct", "5th", "Dim", "Wide"],
         default=0,
         help="Cycles one voice through a chord fast enough to hear it as a "
              "chord. The classic chiptune fake-polyphony trick."),
    dict(key="arp_rate", label="Arp Rate", group="Auto FX", kind="int",
         min=1, max=50, default=17, unit="Hz",
         help="Steps per second. Around 15-25 is the classic NES rattle."),
    dict(key="sweep_amount", label="Sweep", group="Auto FX", kind="int",
         min=0, max=64, default=32, offset=-32,
         help="Automatic pitch slide on every note, like the NES sweep "
              "unit. Negative falls, positive rises, 0 is off."),
    dict(key="retrig_rate", label="Retrigger", group="Auto FX", kind="int",
         min=0, max=50, default=0, unit="Hz",
         help="Re-strikes the envelope while a note is held. Spaced by "
              "ratio rather than by Hz, so it moves evenly by ear. 0 = off."),

    # --- Envelope ----------------------------------------------------------
    dict(key="env_mode", label="Mode", group="Envelope", kind="enum",
         options=["MIDI ch presets", "Custom ADSR"], default=0,
         help="Presets = the original per-MIDI-channel tones[] table."),
    dict(key="env_attack", label="Attack", group="Envelope", kind="int",
         min=1, max=32, default=1,
         help="How long the note takes to reach full level, from 10ms at 1 up to 4s at 32. Spaced by ratio, so the fader moves evenly by ear."),
    dict(key="env_decay", label="Decay", group="Envelope", kind="int",
         min=1, max=32, default=26,
         help="How long it takes to fall from full level to the Sustain "
              "level. Spaced by ratio, so the fader moves evenly by ear."),
    dict(key="env_sustain", label="Sustain", group="Envelope", kind="int",
         min=0, max=32, default=32,
         help=""),
    dict(key="env_release", label="Release", group="Envelope", kind="int",
         min=1, max=32, default=19,
         help="How long the note takes to fade once you let go."),

    # --- Pitch & Response --------------------------------------------------
    dict(key="glide", label="Glide", group="Pitch & Response", kind="int",
         min=0, max=100, default=0,
         help="Portamento from the previous note. 0 = off, higher = slower."),
    dict(key="transpose", label="Transpose", group="Pitch & Response", kind="int",
         min=0, max=48, default=24, offset=-24, unit="st",
         help=""),
    # --- Master Mixer ------------------------------------------------------
    dict(key="mix_noise", label="Noise", group="Mixer", kind="int",
         min=0, max=15, default=15,
         help="Master noise level. The chip has no noise fader -- tone and "
              "noise share one amplitude -- so this gates the noise on and "
              "off a few thousand times a second with a random duty. Random "
              "rather than regular keeps the artefact broadband, so it reads "
              "as quieter noise instead of a whine. 15 = untouched."),
    dict(key="mix_tone", label="Tone", group="Mixer", kind="int",
         min=0, max=15, default=15,
         help="Master level for the pitched voices. Drums are unaffected."),
    dict(key="mix_drum", label="Drums", group="Mixer", kind="int",
         min=0, max=15, default=15,
         help="Master drum level. Below 15 this moves drums off the chip's "
              "hardware envelope onto a software one so the level can be "
              "scaled at all, which softens the transient slightly. "
              "15 leaves them on the hardware envelope, untouched."),

    dict(key="temperament", label="Temperament", group="Tuning", kind="enum",
         options=["Equal", "Meantone", "Just", "Pythag", "Werck III",
                  "Kirn III", "Vallotti", "Young", "Kellner", "1/6 Mean"],
         default=0,
         help="Historical tunings. Audible in the lower octaves; above about "
              "MIDI 72 the AY's integer divisor is too coarse to render the "
              "offsets and they round away."),
    dict(key="temper_root", label="Root", group="Tuning", kind="enum",
         options=["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"],
         default=0,
         help="Which key the temperament is centred on. The root itself is "
              "exactly in tune; everything else is tempered around it."),

    dict(key="vel_sense", label="Velocity", group="Pitch & Response", kind="toggle",
         default=1,
         help="Off = every note plays at full volume."),
]

# Factory presets, defined by key so they survive reordering of PARAMS.
# Missing keys fall back to the parameter's default.
PRESETS = {
    "Init": {},
    "Buzz Bass Lead": dict(buzz_enable=1, buzz_ratio=1, buzz_shape=0,
                           vib_enable=1, vib_rate=55, vib_depth=5, vib_delay=60),
    "Haunted Organ": dict(vib_enable=1, vib_rate=30, vib_depth=12,
                          trem_enable=1, trem_rate=35, trem_depth=6,
                          env_mode=1, env_attack=22, env_decay=30,
                          env_sustain=28, env_release=21),
    "Wind Machine": dict(noise_enable=1, noise_period=13,
                         trem_enable=1, trem_rate=25, trem_depth=7,
                         env_mode=1, env_attack=20, env_decay=32,
                         env_sustain=30, env_release=30, vel_sense=0),
    "Chip Whistle": dict(transpose=36, glide=25,
                         vib_enable=1, vib_rate=80, vib_depth=4, vib_delay=40),
    "808 Kit": dict(drum_tune=78, drum_decay=165, drum_bend=130, drum_noise=9),
    "Tight Kit": dict(drum_tune=118, drum_decay=62, drum_bend=70, drum_noise=5),
    "MadMax Buzzer": dict(buzz_enable=1, buzz_ratio=1, buzz_shape=1,
                          buzz_detune=38, env_mode=1, env_attack=1,
                          env_decay=31, env_sustain=30, env_release=24),
    "Arcade Warp": dict(warp_mode=1, warp_rate=95, warp_depth=40,
                        buzz_enable=1, buzz_ratio=2, buzz_detune=36),
    "Broken Cabinet": dict(warp_mode=3, warp_rate=22, warp_depth=55,
                           noise_enable=1, noise_period=6),
    "Tape Eaten": dict(warp_mode=5, warp_rate=70, warp_depth=34,
                       warp_motion=12),
    "Air Raid": dict(warp_mode=6, warp_rate=60, warp_depth=44,
                     warp_motion=20),
    "Dying Console": dict(warp_mode=7, warp_rate=48, warp_depth=40,
                          warp_motion=9, noise_enable=1, noise_period=9),
    "Ring Zone": dict(warp_mode=8, warp_rate=105, warp_depth=30,
                      warp_motion=26, buzz_enable=1, buzz_ratio=1),
    "Buzz Roll": dict(drum_roll=45, drum_decay=70, drum_chaos=10),
    "Drunk Drummer": dict(drum_flam=7, drum_chaos=38, drum_tune=92,
                          drum_decay=120),
    "Reverse Kit": dict(drum_reverse=1, drum_decay=150, drum_bend=40),
    "Broken Machine": dict(drum_roll=50, drum_chaos=58, drum_noise=4,
                           drum_decay=55, drum_bend=170),
    "Bad Ground": dict(warp_mode=3, warp_rate=6, warp_depth=38,
                       warp_motion=35, noise_period=6),
    "1-Up Arp": dict(arp_mode=1, arp_rate=20, env_mode=1, env_attack=1,
                     env_decay=28, env_sustain=26, env_release=26),
    "Laser Jump": dict(sweep_amount=52, env_mode=1, env_attack=1,
                       env_decay=25, env_sustain=6, env_release=30),
    "Machine Gun": dict(retrig_rate=38, noise_enable=1, noise_period=4,
                        env_mode=1, env_attack=1, env_decay=23,
                        env_sustain=10, env_release=28),
}

# ---------------------------------------------------------------------------
# Panel sections
# ---------------------------------------------------------------------------
# Tones and drums are different voices sharing the same three chips, and the
# FX reach across both. The panel says so out loud rather than leaving it to
# be discovered: each band is labelled with what it touches, and the Warp
# Zone sits across the bottom spanning both because it hits them differently.

# Where the published site lives. The social preview needs ABSOLUTE urls --
# Reddit, Discord and the rest will not resolve a relative one.
SITE_URL = "https://keyandcableco.github.io/8bit8asterd"
SITE_HOME = "https://keyandcable.com"
SITE_TITLE = "The 8Bit 8asterd"
SITE_DESC = ("A 9-voice chiptune synth built on three AY-3-8910 chips. "
             "Playable in your browser, running the same firmware as the hardware.")

SECTIONS = [
    dict(key="mix", title="Master Mixer", scope="everything out",
         blurb="Output levels. Tone and noise share one amplitude register "
               "per channel on this chip, so noise is thinned by gating "
               "rather than by a level control that does not exist.",
         groups=["Mixer"]),
    dict(key="tone", title="Tone Voices", scope="pitched voices",
         blurb="The melodic side. Nothing here touches the drum channel.",
         groups=["Envelope", "Tuning", "Pitch & Response", "Buzzy Bass", "Unison", "Drawbars", "Wavetable",
                 "Vibrato", "Tremolo", "Noise Blend", "Auto FX"]),
    dict(key="drum", title="Drum Voices", scope="MIDI channel 10",
         blurb="Percussion only. Drums are the voices driven by each chip's "
               "envelope generator, which is why the FX bite them hardest.",
         groups=["Drums", "Drum FX"]),
    dict(key="warp", title="Warp Zone", scope="tones + drums",
         blurb="The bent stuff, and the only section that reaches both. It "
               "writes straight to the chips while the voices keep writing "
               "their own values, so the two fight \u2014 and because drums "
               "run on the envelope generator and tones do not, the same "
               "setting lands very differently on each.",
         groups=["Warp Zone", "Clock Warp"]),
]

# ---------------------------------------------------------------------------
# Normalise + validate
# ---------------------------------------------------------------------------

def normalise():
    for i, p in enumerate(PARAMS):
        p["index"] = i
        if p["kind"] == "toggle":
            p.setdefault("min", 0); p.setdefault("max", 1)
        elif p["kind"] == "enum":
            p.setdefault("min", 0); p["max"] = len(p["options"]) - 1
        p.setdefault("scale", 1); p.setdefault("offset", 0)
        p.setdefault("unit", ""); p.setdefault("help", "")

        for field in ("min", "max", "default"):
            v = p[field]
            if not (0 <= v <= 255):
                sys.exit(f"ERROR: {p['key']}.{field}={v} does not fit uint8_t")
        if not (p["min"] <= p["default"] <= p["max"]):
            sys.exit(f"ERROR: {p['key']} default outside range")

    placed = {g for sec in SECTIONS for g in sec["groups"]}
    actual = {p["group"] for p in PARAMS}
    missing = actual - placed
    extra = placed - actual
    if missing:
        sys.exit(f"ERROR: parameter groups with no panel section: {sorted(missing)}")
    if extra:
        sys.exit(f"ERROR: SECTIONS lists groups that do not exist: {sorted(extra)}")

    keys = [p["key"] for p in PARAMS]
    if len(set(keys)) != len(keys):
        sys.exit("ERROR: duplicate parameter keys")

    for name, values in PRESETS.items():
        for k, v in values.items():
            match = [p for p in PARAMS if p["key"] == k]
            if not match:
                sys.exit(f"ERROR: preset '{name}' references unknown key '{k}'")
            p = match[0]
            if not (p["min"] <= v <= p["max"]):
                sys.exit(f"ERROR: preset '{name}' value {k}={v} outside range")


def preset_array(values):
    return [values.get(p["key"], p["default"]) for p in PARAMS]


def layout_version():
    """One byte derived from the definition, so firmware EEPROM saves are
    invalidated automatically whenever ranges/order/count change."""
    acc = len(PARAMS)
    for p in PARAMS:
        for ch in p["key"]:
            acc = (acc * 31 + ord(ch)) & 0xFF
        acc = (acc * 31 + p["min"] + p["max"] * 3 + p["default"] * 7) & 0xFF
    return acc or 1


# ---------------------------------------------------------------------------
# parameters.h
# ---------------------------------------------------------------------------

def emit_header(path):
    ver = layout_version()
    lines = []
    a = lines.append
    a("// AUTO-GENERATED by generate.py -- do not edit by hand.")
    a("// Edit PARAMS in generate.py and re-run it instead.")
    a("#ifndef PARAMETERS_H")
    a("#define PARAMETERS_H")
    a("")
    a("#include <avr/pgmspace.h>")
    a("")
    a(f"#define NUM_PARAMS {len(PARAMS)}")
    a(f"#define PARAM_LAYOUT_VERSION 0x{ver:02X}")
    a("")
    a("enum {")
    for p in PARAMS:
        a(f"  P_{p['key'].upper()} = {p['index']},  // {p['group']}: {p['label']}"
          f" [{p['min']}..{p['max']}] default {p['default']}")
    a("};")
    a("")
    for name, field in (("PARAM_MIN", "min"), ("PARAM_MAX", "max"),
                        ("PARAM_DEFAULT", "default")):
        vals = ", ".join(str(p[field]) for p in PARAMS)
        a(f"static const uint8_t {name}[NUM_PARAMS] PROGMEM = {{ {vals} }};")
    a("")
    a("// Ticks between events for the re-strike controls (Drum Roll,")
    a("// Retrigger), indexed by the parameter value. The tick is 100Hz, so")
    a("// ticks = 100 / rate. Rate is spaced EXPONENTIALLY rather than")
    a("// linearly: rhythm is heard in ratios, so a linear Hz control puts a")
    a("// doubling between the first two positions and nothing at all across")
    a("// the top half. This curve runs 1.5Hz to 45Hz at about 6% per step.")
    a("static const uint8_t RATE_EVERY[51] PROGMEM = {")
    import math
    LO, HI, N = 1.5, 45.0, 50
    vals = ["0"]
    for v in range(1, N + 1):
        rate = LO * (HI / LO) ** ((v - 1) / (N - 1))
        vals.append(str(max(1, min(255, round(100.0 / rate)))))
    for i in range(0, len(vals), 17):
        a("  " + ", ".join(vals[i:i + 17]) + ",")
    a("};")
    a("")
    a("// Tone-period multiplier for the Warp pitch modes, x256, indexed by")
    a("// warp depth. Pitch is heard in semitones, so the table is")
    a("// exponential: a linear multiplier put half the total drop in the")
    a("// first quarter of the fader. This spreads 30 semitones evenly.")
    a("static const uint16_t WARP_STRETCH[64] PROGMEM = {")
    import math
    sv = [str(int(round(256 * (2 ** (d * 30.0 / 63.0 / 12.0))))) for d in range(64)]
    for i in range(0, len(sv), 12):
        a("  " + ", ".join(sv[i:i + 12]) + ",")
    a("};")
    a("")
    a("// Envelope segment rates, in 1/16ths of an amplitude unit per 100Hz")
    a("// tick, indexed by the parameter value. The parameter is a TIME and")
    a("// the scale is exponential, 10ms to 4s: as a linear per-tick rate it")
    a("// was reciprocal, so 1->2 halved the time while 31->32 moved it 4ms.")
    a("// A 1/16th accumulator keeps the slow end usable, which an integer")
    a("// rate could not -- 1023/400 rounds to 3 and swallows the top third")
    a("// of the fader.")
    a("static const uint16_t ENV_RATE[33] PROGMEM = {")
    ev = ["0"]
    for v in range(1, 33):
        t = 1.0 * (400.0 ** ((v - 1) / 31.0))
        ev.append(str(max(1, min(65535, int(round(1023.0 * 16.0 / t))))))
    for i in range(0, len(ev), 11):
        a("  " + ", ".join(ev[i:i + 11]) + ",")
    a("};")
    a("")
    a("// Master mixer attenuation, in amplitude-register steps, indexed by")
    a("// the mixer setting. Applied by SUBTRACTION, which is a true dB cut")
    a("// and treats a quiet note the same as a loud one -- multiplying the")
    a("// register instead truncated a decaying note to silence several fader")
    a("// positions before a full one. The curve is an audio taper: the top")
    a("// half covers about 13dB and it accelerates below that, because the")
    a("// AY's own steps grow from roughly 1.5dB at the top to 3.5dB at the")
    a("// bottom and a straight mapping therefore dives.")
    a("static const uint8_t MIX_ATTEN[16] PROGMEM = {")
    a("  15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 2, 1, 1, 0")
    a("};")
    a("")
    a("// Wavetable voice. Sixty-four steps, values 0..15 -- the amplitude")
    a("// register IS the output once tone and noise are switched off, so")
    a("// this is a 4-bit DAC and these are the waveforms it draws. Four bits")
    a("// is coarse, which is the point: it sounds like the hardware it is.")
    a("#define WAVE_LEN 64")
    a("#define WAVE_COUNT 6")
    a("static const uint8_t WAVE_TABLES[WAVE_COUNT][WAVE_LEN] PROGMEM = {")
    import math
    def emit(name, fn):
        vals = []
        for i in range(64):
            v = fn(i / 64.0)
            vals.append(str(max(0, min(15, int(round(v * 15))))))
        a("  { // " + name)
        for j in range(0, 64, 16):
            a("    " + ", ".join(vals[j:j + 16]) + ",")
        a("  },")
    emit("sine",      lambda t: 0.5 + 0.5 * math.sin(2 * math.pi * t))
    emit("triangle",  lambda t: 2 * t if t < 0.5 else 2 * (1 - t))
    emit("saw down",  lambda t: 1 - t)
    emit("ramp up",   lambda t: t)
    emit("pulse 25%", lambda t: 1.0 if t < 0.25 else 0.0)
    # Two stacked formants, roughly an "ah": the chip cannot filter, so the
    # shape has to carry the resonance itself.
    emit("vocal", lambda t: 0.5 + 0.30 * math.sin(2 * math.pi * t)
                                + 0.22 * math.sin(2 * math.pi * 3 * t + 0.4)
                                + 0.12 * math.sin(2 * math.pi * 5 * t + 1.1))
    a("};")
    a("")
    a("#endif // PARAMETERS_H")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {path}  ({len(PARAMS)} params, layout version 0x{ver:02X})")


# ---------------------------------------------------------------------------
# index.html
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>8B8 — AY Panel</title>
<meta name="description" content="__SITE_DESC__">

<!-- Open Graph. Reddit, Discord, iMessage and Slack all read these; without
     them a shared link renders as a bare url with no picture. -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="The Key &amp; Cable Company">
<meta property="og:title" content="__SITE_TITLE__">
<meta property="og:description" content="__SITE_DESC__">
<meta property="og:url" content="__SITE_URL__/">
<meta property="og:image" content="__SITE_URL__/social.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="The 8Bit 8asterd control panel">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__SITE_TITLE__">
<meta name="twitter:description" content="__SITE_DESC__">
<meta name="twitter:image" content="__SITE_URL__/social.png">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  /* ---- Themes --------------------------------------------------------
     Three cabinets, one layout. Every colour is a variable so a theme is
     just a different set of them; --ui-scale multiplies every type size so
     the whole panel can be sized without reflowing anything. ---------- */

  :root{
    --ui-scale: 1;
    --pixel:'Press Start 2P', monospace;
    --mono:'JetBrains Mono', ui-monospace, monospace;
    --glow: none;
    --body-bg: var(--slot);
  }

  /* NES front-loader: two greys over a near-black slot, red as the only colour */
  :root, [data-theme="nes"]{
    --slot:#0b0b0c; --body-dark:#2e2f31; --body:#3c3d40; --body-lit:#5a5c60;
    --bezel:#17181a; --accent:#c8322b; --accent-lit:#e8483f; --accent-dim:#6d1f1b;
    --text:#dedbd2; --text-dim:#8c8d90; --ok:#7bbf5a; --warn:#d8c98a;
    --on-accent:#ffffff;
    --body-bg:
      repeating-linear-gradient(0deg, rgba(255,255,255,.012) 0 2px, transparent 2px 4px),
      #0b0b0c;
  }

  /* Arcade cabinet: black glass, CRT scanlines, neon on the marquee */
  [data-theme="arcade"]{
    --slot:#05060a; --body-dark:#0d1030; --body:#151a44; --body-lit:#2b3480;
    --bezel:#000208; --accent:#ff2e88; --accent-lit:#ff6ab0; --accent-dim:#6b0f38;
    --text:#e8f4ff; --text-dim:#7f93c4; --ok:#3dffc0; --warn:#ffd400;
    --on-accent:#0a0010;
    --glow: 0 0 6px currentColor;
    --body-bg:
      repeating-linear-gradient(0deg, rgba(0,0,0,.55) 0 1px, transparent 1px 3px),
      radial-gradient(ellipse at 50% 0%, #1b2160 0%, #05060a 62%),
      #05060a;
  }

  /* Handheld: four shades of green and nothing else */
  [data-theme="handheld"]{
    --slot:#0b1a06; --body-dark:#1d3312; --body:#2b4a1a; --body-lit:#456b2a;
    --bezel:#050d03; --accent:#9bbc0f; --accent-lit:#c6de8c; --accent-dim:#4a5c11;
    --text:#c6de8c; --text-dim:#6b8a3a; --ok:#9bbc0f; --warn:#c6de8c;
    --on-accent:#0b1a06;
    --body-bg:
      repeating-linear-gradient(0deg, rgba(0,0,0,.10) 0 2px, transparent 2px 4px),
      #0b1a06;
  }

  *{ box-sizing:border-box; }
  html,body{ margin:0; padding:0; }
  body{
    background: var(--body-bg);
    color:var(--text); font-family:var(--mono);
    min-height:100vh; padding:26px 14px 56px;
    display:flex; justify-content:center;
  }
  .rack{ width:100%; max-width:1080px; }

  /* ---- Header ---- */
  header{
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.55);
    padding:0; margin-bottom:14px; overflow:hidden;
  }
  .stripe{ display:flex; height:9px; }
  .stripe i{ flex:1; background:var(--accent); }
  .stripe i:nth-child(even){ background:var(--body-dark); flex:0 0 26px; }
  [data-theme="arcade"] .stripe i{ background:var(--accent); }
  [data-theme="arcade"] .stripe i:nth-child(2){ background:#00e5ff; flex:1; }
  [data-theme="arcade"] .stripe i:nth-child(4){ background:var(--warn); flex:1; }

  .head-inner{
    display:flex; align-items:flex-end; justify-content:space-between;
    gap:16px; flex-wrap:wrap; padding:16px 18px 18px;
  }
  .brand .eyebrow{
    display:flex; align-items:center; gap:9px;
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    letter-spacing:.12em; color:var(--text-dim); margin-bottom:9px;
  }
  .brand .eyebrow .brandlink{
    display:flex; align-items:center; gap:9px;
    color:inherit; text-decoration:none; cursor:pointer;
  }
  .brand .eyebrow .brandlink:hover{ color:var(--accent-lit); }
  .brand .eyebrow .brandlink:hover img{ filter:brightness(1.15); }
  .brand .eyebrow img{
    height:calc(20px * var(--ui-scale)); width:auto; display:block;
    image-rendering:auto;
  }
  .brand h1{
    font-family:var(--pixel); font-size:calc(19px * var(--ui-scale));
    margin:0; line-height:1.35; color:var(--text);
    text-shadow:2px 2px 0 rgba(0,0,0,.65);
  }
  .brand h1 span{ color:var(--accent-lit); text-shadow:var(--glow); }
  .brand p{
    margin:10px 0 0; color:var(--text-dim);
    font-size:calc(12px * var(--ui-scale)); max-width:52ch; line-height:1.55;
  }

  .connection{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }
  .status-pill{
    display:flex; align-items:center; gap:7px; font-family:var(--pixel);
    font-size:calc(7px * var(--ui-scale)); letter-spacing:.06em;
    padding:9px 11px; background:var(--slot);
    border:2px solid var(--bezel); color:var(--text-dim);
  }
  .status-pill .dot{ width:7px; height:7px; background:var(--text-dim); }
  .status-pill.on{ color:var(--ok); }
  .status-pill.on .dot{ background:var(--ok); box-shadow:0 0 7px var(--ok); }
  .status-pill.err{ color:var(--accent-lit); }
  .status-pill.err .dot{ background:var(--accent-lit); box-shadow:0 0 7px var(--accent); }

  button.btn{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    letter-spacing:.04em; color:var(--text); cursor:pointer; padding:10px 12px;
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.5);
  }
  button.btn:hover{ color:var(--accent-lit); text-shadow:var(--glow); }
  button.btn:active{ box-shadow: inset 0 2px 4px rgba(0,0,0,.7); transform:translateY(1px); }
  button.btn:disabled{ opacity:.35; cursor:not-allowed; }
  button.btn:focus-visible, .switch:focus-visible, .enum-btn:focus-visible{
    outline:2px solid var(--accent-lit); outline-offset:2px;
  }

  select{
    font-family:var(--mono); font-size:calc(12px * var(--ui-scale));
    font-weight:500; background:var(--slot); color:var(--text);
    border:2px solid var(--bezel); padding:9px;
  }

  .unsupported, .mismatch{
    background:var(--body-dark); border:2px solid var(--accent);
    padding:14px 16px; color:var(--text);
    font-size:calc(12px * var(--ui-scale)); line-height:1.6; margin-bottom:16px;
  }
  .unsupported b, .mismatch b{
    color:var(--accent-lit); font-family:var(--pixel);
    font-size:calc(8px * var(--ui-scale)); display:block;
    margin-bottom:7px; line-height:1.5;
  }
  .mismatch code{ background:var(--slot); padding:1px 5px; color:var(--text); }

  /* ---- Control bars ---- */
  .bar{
    display:flex; align-items:center; gap:8px; flex-wrap:wrap;
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.55);
    padding:12px 13px; margin-bottom:12px;
  }
  .bar .label{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--text-dim); margin-right:4px;
  }
  .bar .spacer{ flex:1 1 auto; }
  .bar.settings{ border-top-width:0; }
  /* .bar sets display:flex, and a class rule beats the browser's default
     [hidden]{display:none} -- so the panel needs this or it never hides. */
  .bar[hidden]{ display:none; }

  /* Compact: drop the explanatory text once you know what things do. */
  body.compact .ctl .help{ display:none; }
  body.compact .section-blurb{ display:none; }
  body.compact .ctl{ margin-bottom:10px; }


  /* ---- Labelled control cells: label under the control it belongs to,
         because a row of labels beside a row of controls is ambiguous ---- */
  .cc-row{
    display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end;
    padding:12px 13px;
  }
  .cc{ display:flex; flex-direction:column; align-items:center; gap:6px; }
  .cc > .cc-label{
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    color:var(--text-dim); letter-spacing:.04em; text-align:center;
    white-space:nowrap;
  }
  .cc select{ padding:7px 8px; font-size:calc(11px * var(--ui-scale)); }
  .cc .readout{
    font-family:var(--pixel); font-size:calc(10px * var(--ui-scale));
    color:var(--accent-lit); background:var(--slot);
    border:2px solid var(--bezel); padding:9px 14px; min-width:88px;
    text-align:center;
  }

  /* ---- Custom scale degrees ---- */
  .degrees{ display:flex; gap:4px; flex-wrap:wrap; padding:0 13px 12px; }
  .degrees .deg{
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    padding:9px 0; width:38px; text-align:center; cursor:pointer;
    background:var(--slot); border:2px solid var(--bezel); color:var(--text-dim);
    user-select:none;
  }
  .degrees .deg.on{ background:var(--accent); color:var(--on-accent); }
  .degrees .note{
    font-family:var(--mono); font-size:calc(10px * var(--ui-scale));
    color:var(--text-dim); align-self:center; margin-left:6px;
  }

  /* ---- Octave steppers ---- */
  .oct{ display:flex; align-items:center; gap:0; flex:none; }
  .oct button{
    font-family:var(--pixel); font-size:calc(9px * var(--ui-scale));
    width:30px; height:30px; cursor:pointer; color:var(--text);
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
  }
  .oct button:hover{ color:var(--accent-lit); }
  .oct button:active{ background:var(--accent); color:var(--on-accent); }
  .oct span{
    min-width:34px; text-align:center; font-family:var(--pixel);
    font-size:calc(7px * var(--ui-scale)); color:var(--accent-lit);
    background:var(--slot); border:2px solid var(--bezel);
    border-left:0; border-right:0; padding:8px 0;
  }

  /* ---- Play surface: chord matrix, strumpad, keyboard ---- */
  .play-wrap{ display:flex; gap:14px; flex-wrap:wrap; padding:12px 13px; }
  .matrix{ flex:1 1 420px; }
  .matrix .cols{ display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
  .matrix .colhead{
    font-family:var(--pixel); font-size:calc(8px * var(--ui-scale));
    text-align:center; color:var(--text-dim); padding:5px 0 7px;
  }
  .chordbtn{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    padding:14px 2px; text-align:center; cursor:pointer; user-select:none;
    background:linear-gradient(180deg, var(--body-lit) 0%, var(--body) 100%);
    border:2px solid var(--bezel); color:var(--text); touch-action:none;
  }
  .chordbtn.row1{ background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%); }
  .chordbtn.row2{ background:linear-gradient(180deg, var(--body-dark) 0%, var(--slot) 100%); color:var(--text-dim); }
  .chordbtn.down, .chordbtn.latched{ background:var(--accent); color:var(--on-accent); }

  .strum{
    flex:0 0 120px; display:flex; flex-direction:column; gap:1px;
    touch-action:none; min-height:clamp(340px, 46vh, 560px);
  }
  .strum .seg{
    flex:1; border:1px solid var(--bezel); cursor:pointer;
    background:linear-gradient(90deg, var(--body-dark) 0%, var(--slot) 100%);
    display:flex; align-items:center; justify-content:center;
    font-family:var(--pixel); font-size:calc(5px * var(--ui-scale));
    color:var(--text-dim); line-height:1; overflow:hidden;
    pointer-events:auto;
  }
  .strum .seg > span{ pointer-events:none; }
  .strum .seg.lit, .strum .seg.held{ color:var(--on-accent); }
  /* Laid flat, the harp runs the full width under the matrix instead of
     standing beside it, which suits a phone held upright. */
  .strum.horiz{
    flex:1 1 100%; flex-direction:row; min-height:0; height:92px;
  }
  .strum.horiz .seg{
    background:linear-gradient(180deg, var(--body-dark) 0%, var(--slot) 100%);
    font-size:calc(6px * var(--ui-scale));
  }
  .strum.horiz .label{ display:none; }
  /* Grid: three across, like the minichord keymaster, which is far easier to
     hit accurately on a phone than a thin strip. */
  .strum.grid{
    flex:1 1 100%; display:grid; grid-template-columns:repeat(3,1fr);
    gap:3px; min-height:0; height:auto;
  }
  .strum.grid .seg{
    min-height:42px; border:2px solid var(--bezel);
    font-size:calc(7px * var(--ui-scale));
  }
  .strum.grid .label{ display:none; }
  .strum .seg.lit{ background:var(--accent); }
  .strum .seg.held{ background:var(--accent-lit); }
  .strum .label{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--text-dim); text-align:center; padding-bottom:4px;
  }

  /* ---- Piano: whites in a row, blacks floated over the gaps ---- */
  .piano-scroll{ overflow-x:auto; padding:0 13px 10px; }
  .piano{
    position:relative; display:flex; gap:0;
    min-width:min(100%, 520px); height:clamp(90px, 18vw, 130px);
    touch-action:none; user-select:none;
  }
  .wkey{
    flex:1 0 0; min-width:26px; position:relative;
    background:linear-gradient(180deg, #e8e5dc 0%, #bdbab1 100%);
    border:2px solid var(--bezel); border-right-width:1px;
    border-radius:0 0 3px 3px; cursor:pointer;
  }
  .wkey:last-child{ border-right-width:2px; }
  .wkey.down{ background:linear-gradient(180deg, var(--accent-lit) 0%, var(--accent) 100%); }
  .wkey b{
    position:absolute; bottom:5px; left:0; right:0; text-align:center;
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    color:#3a3a3a; pointer-events:none;
  }
  .bkey{
    position:absolute; top:0; height:62%; z-index:2;
    background:linear-gradient(180deg, #3a3a3d 0%, #121214 100%);
    border:2px solid var(--bezel); border-radius:0 0 3px 3px;
    cursor:pointer; transform:translateX(-50%);
  }
  .bkey.down{ background:linear-gradient(180deg, var(--accent) 0%, var(--accent-dim) 100%); }
  .bkey b{
    position:absolute; bottom:4px; left:0; right:0; text-align:center;
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    color:#cfcfcf; pointer-events:none;
  }

  /* ---- Drum pads: big touch targets, nothing fiddly ---- */
  .pads{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(78px,1fr));
    gap:6px; padding:0 13px 13px;
  }
  .pad{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    padding:18px 4px; text-align:center; cursor:pointer; user-select:none;
    background:linear-gradient(180deg, var(--accent-dim) 0%, var(--slot) 100%);
    border:2px solid var(--bezel); color:var(--text); touch-action:none;
  }
  .pad.down{ background:var(--accent); color:var(--on-accent); }
  .pad b{ display:block; font-size:calc(6px * var(--ui-scale)); opacity:.6; margin-bottom:5px; }

  /* ---- Transport: pinned so the sequencer can be stopped from any
         section, not only from the one it lives in ---- */
  .transport{
    position:sticky; top:env(safe-area-inset-top, 0px); z-index:50;
    display:flex; align-items:center; gap:12px;
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), 0 4px 10px rgba(0,0,0,.5);
    padding:10px 13px; margin-bottom:12px;
  }
  .transport .btn.play{
    font-size:calc(11px * var(--ui-scale));
    padding:13px 28px; min-width:110px;
    background:linear-gradient(180deg, var(--accent-lit) 0%, var(--accent) 100%);
    color:var(--on-accent);
  }
  .transport .btn.play.running{
    background:linear-gradient(180deg, var(--warn) 0%, var(--accent-dim) 100%);
  }
  .transport .tinfo{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--text-dim); letter-spacing:.05em;
  }

  /* ---- File picker: a bare input[type=file] renders in OS colours, which
         on this panel is dark text on a dark background and looks like
         nothing is there at all ---- */
  .filepick{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .filepick input[type=file]{ display:none; }
  .filepick label{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    padding:11px 16px; cursor:pointer; color:var(--text);
    background:linear-gradient(180deg, var(--body-lit) 0%, var(--body) 100%);
    border:2px solid var(--bezel);
  }
  .filepick label:hover{ color:var(--accent-lit); }
  .filepick .fname{
    font-family:var(--mono); font-size:calc(11px * var(--ui-scale));
    color:var(--text-dim);
  }

  /* ---- Sequencer ---- */
  /* Outside the pattern: clearly inactive, but a note placed there must
     still be legible -- at a quarter opacity it vanished entirely. */
  .step.past{ opacity:.55; border-style:dotted; }
  .step.past.on, .step.past.tail{ opacity:.7; }
  .seqrow.sharprow .seqname{ opacity:.6; }
  .step.note.on{ background:var(--warn); }

  /* ---- Transport: pinned so the sequencer can be stopped from any
         section, not only from the one it lives in ---- */
  .transport{
    position:sticky; top:env(safe-area-inset-top, 0px); z-index:50;
    display:flex; align-items:center; gap:12px;
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), 0 4px 10px rgba(0,0,0,.5);
    padding:10px 13px; margin-bottom:12px;
  }
  .transport .btn.play{
    font-size:calc(11px * var(--ui-scale));
    padding:13px 28px; min-width:110px;
    background:linear-gradient(180deg, var(--accent-lit) 0%, var(--accent) 100%);
    color:var(--on-accent);
  }
  .transport .btn.play.running{
    background:linear-gradient(180deg, var(--warn) 0%, var(--accent-dim) 100%);
  }
  .transport .tinfo{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--text-dim); letter-spacing:.05em;
  }

  /* ---- File picker: a bare input[type=file] renders in OS colours, which
         on this panel is dark text on a dark background and looks like
         nothing is there at all ---- */
  .filepick{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .filepick input[type=file]{ display:none; }
  .filepick label{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    padding:11px 16px; cursor:pointer; color:var(--text);
    background:linear-gradient(180deg, var(--body-lit) 0%, var(--body) 100%);
    border:2px solid var(--bezel);
  }
  .filepick label:hover{ color:var(--accent-lit); }
  .filepick .fname{
    font-family:var(--mono); font-size:calc(11px * var(--ui-scale));
    color:var(--text-dim);
  }

  /* ---- Sequencer ---- */
  .seq{ padding:0 13px 13px; overflow-x:auto; }
  .seqrow{ display:flex; align-items:center; gap:3px; margin-bottom:3px; }
  .seqname select{
    width:100%; font-size:calc(9px * var(--ui-scale)); padding:2px 1px;
  }
  .seqname{
    flex:0 0 86px; font-family:var(--pixel);
    font-size:calc(6px * var(--ui-scale)); color:var(--text-dim);
    cursor:pointer; padding:4px 0;
  }
  .seqname:hover{ color:var(--accent-lit); }
  .step{
    flex:1 1 0; min-width:18px; height:26px; cursor:pointer;
    background:var(--slot); border:2px solid var(--bezel); touch-action:none;
  }
  .step.beat{ border-color:var(--body-lit); }
  .step.on{ background:var(--accent); }
  .step.tail{ background:var(--accent-dim); }
  .step.note.tail{ background:var(--warn); opacity:.55; }
  .step.playing{ box-shadow:inset 0 0 0 2px var(--warn); }
  .seqhead{ display:flex; gap:3px; margin:0 0 5px 75px; }
  .seqhead div{
    flex:1 1 0; min-width:18px; text-align:center;
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    color:var(--text-dim);
  }
  .seqhead div.beat{ color:var(--accent-lit); }

  /* ---- Gamepad ---- */
  .gp-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; padding:0 13px 13px; }
  .gp-assign{ display:flex; align-items:center; gap:8px; }
  .gp-assign span{
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    color:var(--text-dim); flex:0 0 68px;
  }
  .gp-assign select{ flex:1 1 auto; min-width:0; }
  .gp-live{
    font-family:var(--mono); font-size:calc(11px * var(--ui-scale));
    color:var(--text-dim); padding:0 13px 12px; line-height:1.7;
  }
  .gp-live b{ color:var(--accent-lit); }
  .gp-modes{ display:flex; gap:6px; flex-wrap:wrap; padding:0 13px 10px; }
  .gp-mode{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    padding:10px 12px; cursor:pointer; color:var(--text-dim);
    background:var(--slot); border:2px solid var(--bezel);
  }
  .gp-mode.active{ background:var(--accent); color:var(--on-accent); }

  /* ---- Section nav: one band at a time, nothing to scroll past ---- */
  .nav{ gap:6px; }
  .nav button{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--text-dim); cursor:pointer; padding:10px 12px;
    background:var(--slot); border:2px solid var(--bezel);
  }
  .nav button:hover{ color:var(--text); }
  .nav button.active{ background:var(--accent); color:var(--on-accent); }
  .section.hidden{ display:none; }

  /* ---- MIDI learn ---- */
  body.learning .ctl .name{ cursor:pointer; color:var(--warn); text-decoration:underline dotted; }
  body.learning .ctl .name:hover{ color:var(--accent-lit); }
  .ctl .name.armed{ color:var(--accent-lit) !important; }
  .ctl .cc{
    font-family:var(--pixel); font-size:calc(6px * var(--ui-scale));
    color:var(--text-dim); margin-left:6px; opacity:.75;
  }

  /* ---- Section bands ---- */
  .section{ margin-bottom:22px; }
  .section-head{
    display:flex; align-items:baseline; gap:12px; flex-wrap:wrap;
    border-left:5px solid var(--accent); padding:2px 0 2px 11px; margin-bottom:5px;
  }
  .section-head h2{
    font-family:var(--pixel); font-size:calc(11px * var(--ui-scale));
    margin:0; color:var(--text); text-shadow:var(--glow);
  }
  .section-head .scope{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--on-accent); background:var(--accent); padding:4px 7px;
  }
  .section-blurb{
    color:var(--text-dim); font-size:calc(11.5px * var(--ui-scale));
    line-height:1.6; margin:0 0 11px 16px; max-width:76ch;
  }
  /* Warp spans both voice types, so it spans the grid too. */
  .section.warp .section-head{ border-left-color:var(--warn); }
  .section.warp .section-head .scope{ background:var(--warn); color:var(--on-accent); }
  .section.warp .modules{ grid-template-columns:1fr; }
  .section.warp .module h2{ background:var(--warn); color:var(--on-accent); }

  .modules{ display:grid; grid-template-columns:repeat(auto-fill,minmax(285px,1fr)); gap:14px; }
  .module{
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.55);
    padding:0 0 14px;
  }
  .module h2{
    font-family:var(--pixel); font-size:calc(8px * var(--ui-scale));
    letter-spacing:.04em; margin:0 0 13px; padding:11px 12px; line-height:1.5;
    color:var(--on-accent); background:var(--accent);
    border-bottom:2px solid var(--bezel); text-shadow:1px 1px 0 rgba(0,0,0,.25);
  }
  /* Warp's own controls sit in a row rather than a column, since it is wide */
  .section.warp .module .ctl{ display:inline-block; width:min(260px,100%); vertical-align:top; margin-right:18px; }

  .ctl{ margin:0 13px 14px; }
  .ctl:last-child{ margin-bottom:0; }
  .ctl .row{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:7px; }
  .ctl .name{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    color:var(--text-dim); line-height:1.5;
  }
  .ctl .value{
    font-size:calc(12px * var(--ui-scale)); font-weight:700;
    color:var(--accent-lit); text-shadow:var(--glow);
  }
  .ctl .help{
    font-size:calc(10.5px * var(--ui-scale)); color:var(--text-dim);
    margin-top:6px; line-height:1.5;
  }

  .switch{
    width:34px; height:34px; border-radius:50%; flex:none; cursor:pointer;
    background:radial-gradient(circle at 38% 32%, var(--body-lit) 0%, var(--body-dark) 70%);
    border:2px solid var(--bezel); box-shadow: inset 0 -2px 3px rgba(0,0,0,.6);
    display:flex; align-items:center; justify-content:center;
  }
  .switch .led{ width:9px; height:9px; border-radius:50%; background:var(--bezel); transition:background .12s, box-shadow .12s; }
  .switch.active{ background:radial-gradient(circle at 38% 32%, var(--accent-lit) 0%, var(--accent) 72%); }
  .switch.active .led{ background:#fff; box-shadow:0 0 8px var(--accent-lit); }

  input[type=range]{ -webkit-appearance:none; appearance:none; width:100%; height:20px; background:transparent; cursor:pointer; margin:0; }
  input[type=range]::-webkit-slider-runnable-track{ height:8px; background:var(--slot); border:2px solid var(--bezel); }
  input[type=range]::-webkit-slider-thumb{
    -webkit-appearance:none; width:14px; height:18px; margin-top:-7px;
    background:linear-gradient(180deg, var(--body-lit) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
  }
  input[type=range]:hover::-webkit-slider-thumb{ background:linear-gradient(180deg, var(--accent-lit) 0%, var(--accent) 100%); }
  input[type=range]::-moz-range-track{ height:8px; background:var(--slot); border:2px solid var(--bezel); }
  input[type=range]::-moz-range-thumb{ width:14px; height:18px; border-radius:0; background:var(--body-lit); border:2px solid var(--bezel); }

  .enum-row{ display:flex; gap:5px; flex-wrap:wrap; }
  .enum-btn{
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale)); line-height:1.5;
    background:var(--slot); color:var(--text-dim);
    border:2px solid var(--bezel); padding:8px; cursor:pointer;
  }
  .enum-btn:hover{ color:var(--text); }
  .enum-btn.active{ background:var(--accent); color:var(--on-accent); text-shadow:none; }

  .console{ margin-top:16px; background:var(--slot); border:2px solid var(--bezel); padding:12px 13px; }
  .console-head{ display:flex; justify-content:space-between; align-items:center; margin-bottom:9px; }
  .console-head .label{ font-family:var(--pixel); font-size:calc(7px * var(--ui-scale)); color:var(--text-dim); }
  .console .clear{ font-family:var(--pixel); font-size:calc(7px * var(--ui-scale)); color:var(--text-dim); background:none; border:none; cursor:pointer; }
  .console .clear:hover{ color:var(--accent-lit); }
  .log{ height:122px; overflow-y:auto; font-size:calc(11.5px * var(--ui-scale)); line-height:1.65; color:var(--text-dim); }
  .log .tx{ color:var(--ok); }
  .log .rx{ color:var(--warn); }
  .log .sys{ color:var(--text-dim); font-style:italic; }
  .log .err{ color:var(--accent-lit); }

  footer{
    text-align:center; color:var(--text-dim); font-family:var(--pixel);
    font-size:calc(7px * var(--ui-scale)); line-height:1.9; margin-top:20px;
  }

  @media (prefers-reduced-motion: reduce){ *{ transition:none !important; } }
  @media (max-width:560px){
    .brand h1{ font-size:calc(15px * var(--ui-scale)); }
    .head-inner{ padding:14px; }
  }
</style>
__EXTRA_HEAD__
</head>
<body>
<div class="rack">

  <header>
    <div class="stripe"><i></i><i></i><i></i><i></i><i></i></div>
    <div class="head-inner">
      <div class="brand">
        <div class="eyebrow">__BRAND__</div>
        <h1>8-BIT<br>8ASTERD <span>&#9632;</span></h1>
        <p>__INTRO__</p>
      </div>
      <div class="connection">
        <div class="status-pill" id="statusPill"><span class="dot"></span><span id="statusText">Disconnected</span></div>
__TRANSPORT_UI__
      </div>
    </div>
  </header>

  <div class="unsupported" id="unsupported" style="display:none;">
    <b>Web Serial isn't available here.</b> This panel needs Chrome or Edge (desktop),
    opened as a local file — Safari and Firefox don't implement the Web Serial API yet.
  </div>

  <div class="mismatch" id="mismatch" style="display:none;">
    <b>Firmware doesn't match this panel.</b>
    <span id="mismatchDetail"></span>
    Re-run <code>generate.py</code>, then recompile and re-flash. Until you do,
    every control here writes to the wrong parameter on the unit.
  </div>

  <div class="transport" id="transport">
    <button class="btn" id="imgSave">Save Game</button>
    <input type="file" id="imgLoadInput" accept=".8b8,.json" style="display:none">
    <button class="btn" id="imgLoad">Load Game</button>
    <button class="btn play" id="seqPlay">Play</button>
    <span class="tinfo" id="transportInfo">&mdash;</span>
  </div>

  <div class="bar">
    <button class="btn" id="settingsBtn">Settings</button>
    <span class="spacer"></span>
    <button class="btn" id="learnBtn">MIDI Learn</button>
    <button class="btn" id="diagBtn">Diag</button>
  </div>

  <div class="bar settings" id="settingsPanel" hidden>
    <span class="label">Theme</span>
    <select id="themeSelect">
      <option value="nes">NES</option>
      <option value="arcade">Arcade</option>
      <option value="handheld">Handheld</option>
    </select>
    <span class="label">Text</span>
    <select id="sizeSelect">
      <option value="0.85">Small</option>
      <option value="1" selected>Normal</option>
      <option value="1.2">Large</option>
      <option value="1.45">Larger</option>
    </select>
    <span class="label">Detail</span>
    <select id="detailSelect">
      <option value="full">Full</option>
      <option value="compact">Compact</option>
    </select>
  </div>

  <nav class="bar nav" id="nav"></nav>

  <div class="bar presets">
    <span class="label">Preset</span>
    <select id="presetSelect"></select>
    <button class="btn" id="applyPreset">Apply</button>
    <button class="btn" id="readUnit">Read Unit</button>
    <button class="btn" id="saveUnit">Save to Unit</button>
    <button class="btn" id="defaultsBtn">Defaults</button>
    <button class="btn" id="exportBtn">Export</button>
    <button class="btn" id="importBtn">Import</button>
  </div>

  <div id="sections"></div>


  <section class="section" id="playSection">
    <div class="section-head">
      <h2>Play</h2><span class="scope">chords + harp</span>
    </div>
    <p class="section-blurb">Seven roots around the circle of fifths, three chord types, and a strumpad whose twelve notes follow whatever chord is held. Works with a mouse, a finger, or the letter keys.</p>
    <div class="module">
      <h2>Chord Matrix</h2>
      <div class="cc-row">
        <div class="cc">
          <div class="switch" id="sharpSw"><div class="led"></div></div>
          <span class="cc-label">Sharp</span>
        </div>
        <div class="cc">
          <div class="switch" id="latchSw"><div class="led"></div></div>
          <span class="cc-label">Hold</span>
        </div>
        <div class="cc">
          <div class="switch" id="barrySw"><div class="led"></div></div>
          <span class="cc-label">Barry</span>
        </div>
        <div class="cc">
          <select id="chordLayoutSel">
            <option value="0">Standard</option>
            <option value="1">Alternate</option>
          </select>
          <span class="cc-label">Layout</span>
        </div>
        <div class="cc">
          <div class="switch" id="stackSw"><div class="led"></div></div>
          <span class="cc-label">Stack</span>
        </div>
        <div class="cc">
          <select id="harpModeSel"></select>
          <span class="cc-label">Harp</span>
        </div>
        <div class="cc">
          <select id="keySigSel"></select>
          <span class="cc-label">Key</span>
        </div>
        <div class="cc">
          <select id="accModeSel">
            <option value="0">Follow key</option>
            <option value="1">Sharp</option>
            <option value="2">Flat</option>
          </select>
          <span class="cc-label">Accidental</span>
        </div>
        <div class="cc">
          <select id="strumDirSel">
            <option value="v">Vertical</option>
            <option value="h">Horizontal</option>
            <option value="grid">Grid</option>
          </select>
          <span class="cc-label">Layout</span>
        </div>
        <div class="cc">
          <div class="oct" id="chordOct"></div>
          <span class="cc-label">Chord 8ve</span>
        </div>
        <div class="cc">
          <div class="oct" id="strumOct"></div>
          <span class="cc-label">Strum 8ve</span>
        </div>
        <div class="cc">
          <select id="inversionSel">
            <option value="0">Root</option>
            <option value="1">1st</option>
            <option value="2">2nd</option>
            <option value="3">3rd</option>
          </select>
          <span class="cc-label">Inversion</span>
        </div>
        <div class="cc">
          <select id="spacingSel">
            <option value="0">Close</option>
            <option value="1">Drop 2</option>
            <option value="2">Drop 3</option>
            <option value="3">Drop 2+4</option>
            <option value="4">Spread</option>
          </select>
          <span class="cc-label">Spacing</span>
        </div>
        <div class="cc">
          <span class="readout" id="chordName">&mdash;</span>
          <span class="cc-label">Playing</span>
        </div>
      </div>

      <div class="play-wrap">
        <div class="matrix">
          <div class="cols" id="colHeads"></div>
          <div class="cols" id="chordGrid"></div>
        </div>
        <div class="strum" id="strum"></div>
      </div>
    </div>

    <div class="module">
      <h2>Keyboard</h2>
      <div class="cc-row">
        <div class="cc">
          <div class="oct" id="kbOct"></div>
          <span class="cc-label">Octave</span>
        </div>
        <div class="cc">
          <span class="readout" id="kbRange">C4 &ndash; B5</span>
          <span class="cc-label">Range</span>
        </div>
        <div class="cc">
          <span class="readout" id="midiStatus">none</span>
          <span class="cc-label">MIDI In</span>
        </div>
      </div>
      <div class="piano-scroll"><div class="piano" id="piano"></div></div>
      <div class="pads" id="pads"></div>
    </div>
  </section>

  <section class="section" id="seqSection">
    <div class="section-head">
      <h2>Sequencer</h2><span class="scope">16 steps</span>
    </div>
    <p class="section-blurb">Sixteen steps of drums. Tap the row name to audition a sound. Everything the Drums and Drum FX sections do applies while it runs, so this is the quickest way to hear what Roll, Chaos or a Warp mode actually do to a pattern.</p>
    <div class="module">
      <h2>Drum Sequencer</h2>
      <div class="cc-row">
        <div class="cc">
          <select id="seqPreset"></select>
          <span class="cc-label">Pattern</span>
        </div>
        <div class="cc">
          <select id="seqPageSel">
            <option value="0">1&ndash;16</option>
            <option value="1">17&ndash;32</option>
            <option value="2">33&ndash;48</option>
            <option value="3">49&ndash;64</option>
          </select>
          <span class="cc-label">Page</span>
        </div>
        <div class="cc">
          <select id="seqLen"></select>
          <span class="cc-label">Length</span>
        </div>
        <div class="cc">
          <select id="seqSig">
            <option value="4">4/4</option>
            <option value="3">3/4</option>
            <option value="6">6/8</option>
            <option value="5">5/4</option>
            <option value="7">7/8</option>
          </select>
          <span class="cc-label">Signature</span>
        </div>
        <div class="cc">
          <input type="range" id="seqTempo" min="50" max="200" value="110"
                 style="width:150px">
          <span class="cc-label" id="seqBpm">110 BPM</span>
        </div>
        <div class="cc">
          <input type="range" id="seqSwing" min="0" max="66" value="0"
                 style="width:110px">
          <span class="cc-label" id="seqSwingVal">Swing 0%</span>
        </div>
        <div class="cc">
          <button class="btn" id="seqTap">Tap</button>
          <span class="cc-label">Tempo</span>
        </div>
        <div class="cc">
          <button class="btn" id="seqClear">Clear</button>
          <span class="cc-label">This page</span>
        </div>
        <div class="cc">
          <select id="seqCopyTo">
            <option value="0">1&ndash;16</option>
            <option value="1">17&ndash;32</option>
            <option value="2">33&ndash;48</option>
            <option value="3">49&ndash;64</option>
          </select>
          <span class="cc-label">Copy to</span>
        </div>
        <div class="cc">
          <button class="btn" id="seqCopyBtn">Copy</button>
          <span class="cc-label">Page</span>
        </div>
      </div>

      <div class="seq">
        <div class="seqhead" id="seqHead"></div>
        <div id="seqRows"></div>
      </div>
    </div>

    <div class="module">
      <h2>MIDI File</h2>
      <div class="bar filepick cc-row" style="margin:0 13px 10px;align-items:flex-end;">
        <input type="file" id="midiFileInput" accept=".mid,.midi,audio/midi">
        <label for="midiFileInput">Choose .mid</label>
        <span class="fname" id="midiFileName">no file</span>
        <span class="spacer"></span>
        <div class="cc">
          <input type="range" id="midiSpeed" min="25" max="200" value="100"
                 style="width:150px">
          <span class="cc-label" id="midiSpeedVal">Speed 1.00x</span>
        </div>
        <div class="cc">
          <div class="switch" id="midiLoopSw"><div class="led"></div></div>
          <span class="cc-label">Loop</span>
        </div>
        <div class="cc">
          <div class="switch" id="midiSyncSw"><div class="led"></div></div>
          <span class="cc-label">Sync tempo</span>
        </div>
        <div class="cc">
          <button class="btn" id="midiPlayBtn">Play File</button>
          <span class="cc-label">Playback</span>
        </div>
      </div>
      <p class="section-blurb" id="midiFileInfo">No file loaded. Channel 10 plays the drum kit; every other channel goes to the tone voices, so every setting on this panel shapes the playback &mdash; temperament, envelope, unison, the Warp Zone, all of it.</p>
    </div>

    <div class="module">
      <h2>Note Sequencer</h2>
      <div class="cc-row">
        <div class="cc">
          <div class="oct" id="noteOct"></div>
          <span class="cc-label">Octave</span>
        </div>
        <div class="cc">
          <span class="readout" id="noteRange">C3 &ndash; B3</span>
          <span class="cc-label">Range</span>
        </div>
      </div>
      <div class="seq">
        <div id="noteRows"></div>
      </div>
    </div>
  </section>


  <section class="section" id="gamepadSection">
    <div class="section-head">
      <h2>Gamepad</h2><span class="scope">bluetooth pad</span>
    </div>
    <p class="section-blurb">Connect a Bluetooth controller and press any button to wake it up. Three modes, cycled with Select or the buttons below. The right stick always strums whatever chord is held.</p>
    <div class="module">
      <h2>Controller</h2>
      <div class="gp-modes" id="gpModes"></div>
      <div class="gp-live" id="gpLive">No controller detected. Pair it, then press a button.</div>
      <div class="gp-grid" id="gpAssign"></div>
    </div>
  </section>

__EXTRA_BODY__

  <div class="console">
    <div class="console-head">
      <span class="label">Serial Log</span>
      <button class="clear" id="clearLog">Clear</button>
    </div>
    <div class="log" id="log"></div>
  </div>

  <footer>NO FILTERS &#9632; NO REGRETS</footer>
</div>

<script>
/* ==== Generated data ==================================================== */
const PARAMS  = __PARAMS_JSON__;
const PRESETS = __PRESETS_JSON__;
const NUM_PARAMS = __NUM_PARAMS__;
const LAYOUT_VERSION = __LAYOUT_VERSION__;
const SECTIONS = __SECTIONS_JSON__;  // must match the flashed firmware

/* ==== State ============================================================= */
const values = PARAMS.map(p => p.default);

/* ==== Logging =========================================================== */
const logEl = document.getElementById('log');
function log(cls, text){
  const row = document.createElement('div');
  row.className = cls; row.textContent = text;
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
  while (logEl.children.length > 300) logEl.removeChild(logEl.firstChild);
}
document.getElementById('clearLog').onclick = () => { logEl.innerHTML = ''; };

__TRANSPORT_JS__

/* ==== Protocol (shared by both transports) ============================== */
let sawLayout = false;

function handleLine(line){
  if (line.startsWith('PRESET:')){
    log('rx','\u00ab ' + line);
    const vals = line.slice(7).split(',').map(v => parseInt(v, 10));
    for (let i = 0; i < Math.min(vals.length, NUM_PARAMS); i++){
      if (!isNaN(vals[i])) values[i] = vals[i];
    }
    renderAll();
  }
  else if (line.startsWith('V:')){
    log('rx','\u00ab ' + line);
    const [, i, v] = line.split(':');
    const idx = parseInt(i, 10), val = parseInt(v, 10);
    if (idx >= 0 && idx < NUM_PARAMS && !isNaN(val)){
      values[idx] = val;
      renderOne(idx);
    }
  }
  else if (line.startsWith('LAYOUT:')){
    log('rx','\u00ab ' + line);
    const [vStr, cStr] = line.slice(7).split(',');
    const fwVer   = parseInt(vStr, 16);
    const fwCount = parseInt(cStr, 10);
    sawLayout = true;
    const ok = (fwVer === LAYOUT_VERSION && fwCount === NUM_PARAMS);
    const box = document.getElementById('mismatch');
    if (ok){
      box.style.display = 'none';
      log('sys', 'Firmware layout 0x' + fwVer.toString(16).toUpperCase() +
                 ' with ' + fwCount + ' parameters \u2014 matches this panel.');
    } else {
      document.getElementById('mismatchDetail').textContent =
        'The unit reports layout 0x' + (isNaN(fwVer) ? '??' : fwVer.toString(16).toUpperCase()) +
        ' with ' + fwCount + ' parameters; this panel was generated for layout 0x' +
        LAYOUT_VERSION.toString(16).toUpperCase() + ' with ' + NUM_PARAMS + '.';
      box.style.display = 'block';
      log('err', 'LAYOUT MISMATCH \u2014 the flashed firmware is not this version.');
    }
  }
  else if (line.startsWith('RAM:')){
    // free-now : closest-the-stack-has-ever-come, both in bytes
    const [, now, low] = line.split(':');
    const n = parseInt(now, 10), l = parseInt(low, 10);
    if (isNaN(n) || n < 0){
      log('sys', 'RAM: not measured (emulator uses the host stack)');
    } else {
      const verdict = l < 128 ? '  <-- TIGHT, stack has come close'
                    : l < 256 ? '  (getting close)' : '  (comfortable)';
      log(l < 128 ? 'err' : 'sys',
          'RAM free now ' + n + ' bytes, closest ever ' + l + verdict);
    }
  }
  else if (line.startsWith('DIAG')){
    log('rx','\u00ab ' + line);
  }
  else if (line.startsWith('CCMAP:')){
    log('rx','\u00ab ' + line);
    line.slice(6).split(',').forEach((v, i) => { ccAssign[i] = parseInt(v, 10); });
    renderCc();
  }
  else if (line.startsWith('CC:')){
    log('rx','\u00ab ' + line);
    const parts = line.split(':');
    ccAssign[parseInt(parts[1], 10)] = parseInt(parts[2], 10);
    document.querySelectorAll('.ctl .name.armed').forEach(e => e.classList.remove('armed'));
    renderCc();
  }
  else if (line.startsWith('LEARNING:')){
    log('sys','Waiting for a CC \u2014 move a control on your MIDI controller.');
  }
  else if (line.startsWith('ERR:')){
    // The unit rejected a command rather than half-applying it.
    log('err','\u00ab ' + line);
    if (line.startsWith('ERR:LONG')){
      log('err','A command was too long for the unit and was ignored. ' +
                'Firmware and panel are out of step \u2014 reflash.');
    } else if (line.startsWith('ERR:SHORT')){
      log('err','The preset had fewer values than the unit expects. ' +
                'Re-run generate.py and reflash.');
    }
  }
  else if (line.startsWith('SAVED:')){
    log('rx','\u00ab ' + line);
    log('sys','Parameters saved to the unit\u2019s EEPROM.');
  }
  // Anything else (e.g. DEBUG output sharing the port) is ignored.
}

/* ==== Sending param changes ============================================ */
function throttle(fn, ms){
  let last = 0, pending = null;
  return (...args) => {
    const now = Date.now();
    if (now - last >= ms){ last = now; fn(...args); }
    else{
      clearTimeout(pending);
      pending = setTimeout(() => { last = Date.now(); fn(...args); }, ms - (now - last));
    }
  };
}
const throttledSend = PARAMS.map(p => throttle(v => send('P:' + p.index + ':' + v), 70));

function setValue(idx, v, flush){
  values[idx] = v;
  renderOne(idx);
  if (flush) send('P:' + idx + ':' + v);
  else throttledSend[idx](v);
}

/* ==== UI construction =================================================== */
function displayText(p, raw){
  const shown = (raw + p.offset) * p.scale;
  const txt = (p.scale < 1) ? shown.toFixed(1) : String(shown);
  return p.unit ? txt + ' ' + p.unit : txt;
}

const renderers = [];  // index -> function updating that control's visuals

function buildUI(){
  const host = document.getElementById('sections');
  const seen = new Set();

  for (const sec of SECTIONS){
    const groups = sec.groups.filter(g => PARAMS.some(p => p.group === g));
    if (!groups.length) continue;

    const band = document.createElement('section');
    band.className = 'section ' + sec.key;

    const head = document.createElement('div');
    head.className = 'section-head';
    const h = document.createElement('h2'); h.textContent = sec.title;
    const sc = document.createElement('span'); sc.className = 'scope'; sc.textContent = sec.scope;
    head.appendChild(h); head.appendChild(sc);
    band.appendChild(head);

    if (sec.blurb){
      const bl = document.createElement('p');
      bl.className = 'section-blurb'; bl.textContent = sec.blurb;
      band.appendChild(bl);
    }

    const mods = document.createElement('div');
    mods.className = 'modules';
    for (const g of groups){
      seen.add(g);
      const mod = document.createElement('section');
      mod.className = 'module';
      const mh = document.createElement('h2'); mh.textContent = g;
      mod.appendChild(mh);
      for (const p of PARAMS.filter(x => x.group === g)) mod.appendChild(buildControl(p));
      mods.appendChild(mod);
    }
    band.appendChild(mods);
    host.appendChild(band);
  }

  // Anything a new parameter group introduced without a section still shows,
  // rather than silently vanishing from the panel.
  const orphans = [...new Set(PARAMS.map(p => p.group))].filter(g => !seen.has(g));
  if (orphans.length){
    const mods = document.createElement('div');
    mods.className = 'modules';
    for (const g of orphans){
      const mod = document.createElement('section');
      mod.className = 'module';
      const mh = document.createElement('h2'); mh.textContent = g;
      mod.appendChild(mh);
      for (const p of PARAMS.filter(x => x.group === g)) mod.appendChild(buildControl(p));
      mods.appendChild(mod);
    }
    host.appendChild(mods);
  }

  const sel = document.getElementById('presetSelect');
  for (const name of Object.keys(PRESETS)){
    const o = document.createElement('option');
    o.value = name; o.textContent = name;
    sel.appendChild(o);
  }
}

function buildControl(p){
  const wrap = document.createElement('div');
  wrap.className = 'ctl';

  const row = document.createElement('div');
  row.className = 'row';
  const name = document.createElement('span');
  name.className = 'name'; name.textContent = p.label;
  const ccTag = document.createElement('span');
  ccTag.className = 'cc'; ccTag.dataset.idx = p.index;
  name.appendChild(ccTag);
  // In learn mode the label is the target: click it, then move something on
  // the controller and the firmware binds whatever CC that sent.
  name.onclick = () => {
    if (!document.body.classList.contains('learning')) return;
    document.querySelectorAll('.ctl .name.armed').forEach(e => e.classList.remove('armed'));
    name.classList.add('armed');
    send('LEARN:' + p.index);
  };
  row.appendChild(name);
  wrap.appendChild(row);

  if (p.kind === 'toggle'){
    const sw = document.createElement('div');
    sw.className = 'switch';
    sw.innerHTML = '<div class="led"></div>';
    sw.onclick = () => setValue(p.index, values[p.index] ? 0 : 1, true);
    row.appendChild(sw);
    renderers[p.index] = () => sw.classList.toggle('active', !!values[p.index]);
  }
  else if (p.kind === 'enum'){
    const er = document.createElement('div');
    er.className = 'enum-row';
    p.options.forEach((label, oi) => {
      const b = document.createElement('button');
      b.className = 'enum-btn'; b.textContent = label;
      b.onclick = () => setValue(p.index, oi, true);
      er.appendChild(b);
    });
    wrap.appendChild(er);
    renderers[p.index] = () => {
      [...er.children].forEach((b, oi) =>
        b.classList.toggle('active', oi === values[p.index]));
    };
  }
  else{ // int slider
    const val = document.createElement('span');
    val.className = 'value';
    row.appendChild(val);
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = p.min; slider.max = p.max; slider.step = 1;
    slider.oninput = () => setValue(p.index, parseInt(slider.value, 10), false);
    slider.onchange = () => setValue(p.index, parseInt(slider.value, 10), true);
    wrap.appendChild(slider);
    renderers[p.index] = () => {
      slider.value = values[p.index];
      val.textContent = displayText(p, values[p.index]);
    };
  }

  if (p.help){
    const help = document.createElement('div');
    help.className = 'help'; help.textContent = p.help;
    wrap.appendChild(help);
  }
  return wrap;
}

function renderOne(i){ renderers[i] && renderers[i](); }
function renderAll(){ for (let i = 0; i < NUM_PARAMS; i++) renderOne(i); }

/* ==== Preset actions ==================================================== */
document.getElementById('applyPreset').onclick = () => {
  const name = document.getElementById('presetSelect').value;
  const arr = PRESETS[name];
  if (!arr) return;
  for (let i = 0; i < NUM_PARAMS; i++) values[i] = arr[i];
  renderAll();
  send('LOAD:' + arr.join(','));
};
document.getElementById('readUnit').onclick = () => send('DUMP');
document.getElementById('saveUnit').onclick = () => send('SAVE');
document.getElementById('defaultsBtn').onclick = () => send('DEFAULTS');

document.getElementById('exportBtn').onclick = async () => {
  const json = JSON.stringify(values);
  try{
    await navigator.clipboard.writeText(json);
    log('sys','Current values copied to clipboard: ' + json);
  }catch(e){
    log('sys','Copy this preset: ' + json);
  }
};
document.getElementById('importBtn').onclick = () => {
  const text = prompt('Paste a preset array (e.g. [0,1,0,...]):');
  if (!text) return;
  try{
    const arr = JSON.parse(text);
    if (!Array.isArray(arr)) throw new Error('not an array');
    for (let i = 0; i < Math.min(arr.length, NUM_PARAMS); i++){
      values[i] = arr[i] | 0;
    }
    renderAll();
    send('LOAD:' + values.join(','));
  }catch(e){
    log('err','Import failed: ' + e.message);
  }
};

/* ==== Diagnostics ======================================================= */
// Diag asks the unit for its live voice map: what each of the 9 voices is
// doing, which chip it sits on, its ADSR stage and its age in ticks. That is
// the thing to read when notes choke.
document.getElementById('diagBtn').onclick = () => send('DIAG');


/* ==== Play surface ====================================================== */
// Layout follows the minichord: seven columns of roots around the circle of
// fifths, three rows for major / minor / 7th, and a sharp modifier that
// lifts every note a semitone. The strumpad is the harp -- twelve stacked
// sections whose notes come from the chord currently held.

const ROOTS  = [['F',53],['C',48],['G',55],['D',50],['A',57],['E',52],['B',59]];

// The columns are F C G D A E B, which is exactly the order sharps are added
// to a signature and the reverse of the order flats are. So a key signature
// falls onto the matrix in order: N sharps alter the first N columns, N flats
// the last N. Choosing a key therefore respells the whole matrix at once --
// in D you press the F button and get F# major.
const KEY_SIGS = [
  ['Cb', 11, -7], ['Gb',  6, -6], ['Db',  1, -5], ['Ab',  8, -4],
  ['Eb',  3, -3], ['Bb', 10, -2], ['F',   5, -1], ['C',   0,  0],
  ['G',   7,  1], ['D',   2,  2], ['A',   9,  3], ['E',   4,  4],
  ['B',  11,  5], ['F#',  6,  6], ['C#',  1,  7]
];
let keySig = 7;                      // index into KEY_SIGS; 7 is C major

// 0 = follow the signature (sharpen in sharp keys, flatten in flat keys),
// 1 = always sharpen, 2 = always flatten. Players do not all think of that
// button the same way, so it is not tied to the key unless asked.
let accidentalMode = 0;

function keyAlterations(){
  const n = KEY_SIGS[keySig][2];
  const alt = [0, 0, 0, 0, 0, 0, 0];
  if (n > 0) for (let i = 0; i < n; i++) alt[i] = 1;        // F C G D A E B
  else for (let i = 0; i < -n; i++) alt[6 - i] = -1;        // B E A D G C F
  return alt;
}

// What the sharp/flat button does right now: +1, -1, or nothing.
function modifierStep(){
  if (!sharpOn) return 0;
  if (accidentalMode === 1) return 1;
  if (accidentalMode === 2) return -1;
  return KEY_SIGS[keySig][2] < 0 ? -1 : 1;                  // follow the key
}

function accidentalText(a){
  return a === 0 ? '' : a === 1 ? '\u266f' : a === 2 ? '\u00d7'
       : a === -1 ? '\u266d' : a === -2 ? '\u266d\u266d' : '?';
}

// Column i's root: its letter plus whatever the key and the button say.
function rootAlter(i){ return keyAlterations()[i] + modifierStep(); }
function rootPc(i){ return ((ROOTS[i][1] + rootAlter(i)) % 12 + 12) % 12; }
function rootName(i){ return ROOTS[i][0] + accidentalText(rootAlter(i)); }
function rootMidi(i){ return ROOTS[i][1] + rootAlter(i); }
const QUALITY = [
  { suffix: '',  name: 'Maj' },
  { suffix: 'm', name: 'Min' },
  { suffix: '7', name: '7th' }
];

// Button combinations, following the minichord: a row can be pressed on its
// own or stacked with others in the same column. The key is the sorted set
// of held rows, so "0" is major alone and "02" is major plus 7th.
//
// barry: Barry Harris treats the major sixth and the diminished seventh as
// the two halves of one scale, so in that mode a major becomes a sixth, a
// minor becomes a minor sixth, and a diminished becomes a diminished
// seventh. The sevenths are already four-note chords and are left alone.
const CHORDS = {
  '0':   { tones: [0,4,7],    suffix: '',     barry: [0,4,7,9], bSuffix: '6'    },
  '1':   { tones: [0,3,7],    suffix: 'm',    barry: [0,3,7,9], bSuffix: 'm6'   },
  '2':   { tones: [0,4,7,10], suffix: '7'     },
  '01':  { tones: [0,3,6],    suffix: 'dim',  barry: [0,3,6,9], bSuffix: 'dim7' },
  '02':  { tones: [0,4,7,11], suffix: 'maj7'  },
  '12':  { tones: [0,3,7,10], suffix: 'm7'    },
  '012': { tones: [0,4,8],    suffix: 'aug'   }
};
// ---- Harp modes -----------------------------------------------------------
// Follows the scale modes I wrote for the minichord (upstream PR #125). The
// chord mode is the original behaviour: the strings ARE the chord, which is
// ideal for arpeggios but leaves nothing to play against it. The scale modes
// change what the strings are, so you can run up and down them freely while
// the chords move underneath and never land on a wrong note.
const SCALES = {
  ionian:      [0,2,4,5,7,9,11],
  lydian:      [0,2,4,6,7,9,11],
  mixolydian:  [0,2,4,5,7,9,10],
  dorian:      [0,2,3,5,7,9,10],
  aeolian:     [0,2,3,5,7,8,10],
  harmMinor:   [0,2,3,5,7,8,11],
  octatonic:   [0,2,3,5,6,8,9,11],   // whole-half, fits a diminished chord
  wholeTone:   [0,2,4,6,8,10],
  majSixthDim: [0,2,4,5,7,8,9,11],   // Barry Harris major sixth diminished
  minSixthDim: [0,2,3,5,7,8,9,11],   // and its minor counterpart
  majPent:     [0,2,4,7,9],
  minPent:     [0,3,5,7,10],
  domPent:     [0,2,4,7,10],
  dimPent:     [0,3,6,9,10]
};

// Mode 8: the mapping a jazz player would use, chosen from the chord type and
// rooted on the chord's own root rather than the key.
const CHORD_SCALE = {
  '0':   'ionian',     '02':  'lydian',   '2':   'mixolydian',
  '12':  'dorian',     '1':   'aeolian',  '01':  'octatonic',
  '012': 'wholeTone'
};
// The alternate layout: a second reading of the same three buttons, from the
// minichord's assignable chord layout (upstream PR #130). Three buttons give
// seven combinations and the standard set already uses all of them, so sus
// and extended chords need a second reading rather than new buttons. These
// are that feature's defaults. The ninth chords drop their fifth -- four
// voices, and that is how a player would voice them anyway.
const ALT_CHORDS = {
  '0':   { tones: [0,5,7],     suffix: 'sus4'  },
  '1':   { tones: [0,2,7],     suffix: 'sus2'  },
  '2':   { tones: [0,5,7,10],  suffix: '7sus4' },
  '02':  { tones: [0,4,11,14], suffix: 'maj9'  },
  '12':  { tones: [0,3,10,14], suffix: 'm9'    },
  '01':  { tones: [0,4,7,14],  suffix: 'add9'  },
  '012': { tones: [0,4,9,14],  suffix: '6/9'   }
};
const ALT_ROW_SUFFIX = ['sus4', 'sus2', '7sus4'];
const CHORD_SCALE_ALT = {
  '0': 'mixolydian', '1': 'ionian',  '2': 'mixolydian',
  '02': 'ionian',    '12': 'dorian', '01': 'ionian', '012': 'ionian'
};
let altLayout = false;

const CHORD_SCALE_BARRY = { '0': 'majSixthDim', '1': 'minSixthDim', '01': 'octatonic' };
// Mode 9: the same idea with pentatonics, which removes the semitones.
const CHORD_PENT = {
  '0':   'majPent',  '02': 'majPent', '2':   'domPent',
  '12':  'minPent',  '1':  'minPent', '01':  'dimPent',
  '012': 'wholeTone'
};

// Modes 1-7 ignore the chord and sit on the key. The "relative" ones root on
// the relative minor, nine semitones up.
const HARP_MODES = [
  { name: 'Chord tones',      kind: 'chord'                                  },
  { name: 'Major',            kind: 'key',  scale: 'ionian'                  },
  { name: 'Major pentatonic', kind: 'key',  scale: 'majPent'                 },
  { name: 'Minor pentatonic', kind: 'key',  scale: 'minPent'                 },
  { name: 'Diminished 6th',   kind: 'key',  scale: 'majSixthDim'             },
  { name: 'Rel. nat. minor',  kind: 'key',  scale: 'aeolian',   offset: 9    },
  { name: 'Rel. harm. minor', kind: 'key',  scale: 'harmMinor', offset: 9    },
  { name: 'Rel. min. pent.',  kind: 'key',  scale: 'minPent',   offset: 9    },
  { name: 'Scale per chord',  kind: 'perChord'                               },
  { name: 'Pentatonic/chord', kind: 'perChordPent'                           },
  { name: 'Custom on key',    kind: 'customKey'                              },
  { name: 'Custom on chord',  kind: 'customChord'                            }
];

const DEGREE_NAMES = ['1','b2','2','b3','3','4','b5','5','b6','6','b7','7'];

let harpMode = 0;
let customMask = 0b101010110101;       // a major scale to start from
let strumHorizontal = false;
let strumLayout = 'v';                 // v | h | grid

// Chord tones give four notes an octave, so twelve strings cover three
// octaves. A seven-note scale would only reach a octave and a half in twelve,
// so the scale modes get twenty-four and land in much the same range.
function strumCount(){ return HARP_MODES[harpMode].kind === 'chord' ? 12 : 24; }

// Returns { tones, root } -- the interval list and the pitch class it starts
// from, which is the chord root in some modes and the key in others.
function harpKeyFor(){ return currentKey() || lastHarpKey; }

function harpScaleFor(){
  const m = HARP_MODES[harpMode];
  const spec = chordSpec();
  const chordRoot = rootPc(activeRoot);

  if (m.kind === 'chord'){
    // Fall back to the last chord so the harp keeps its strings once the
    // button is released. No padding: this mode means the chord's own notes,
    // and a triad padded with a sixth put an E in a G major harp.
    const sp = spec || specForKey(lastHarpKey);
    if (!sp) return null;
    return { tones: sp.tones, root: chordRoot };
  }
  if (m.kind === 'key'){
    return { tones: SCALES[m.scale], root: (KEY_SIGS[keySig][1] + (m.offset || 0)) % 12 };
  }
  if (m.kind === 'perChord' || m.kind === 'perChordPent'){
    const key = harpKeyFor();
    if (!key) return null;
    const table = altLayout ? CHORD_SCALE_ALT
                : m.kind === 'perChordPent' ? CHORD_PENT
                : (barryOn && CHORD_SCALE_BARRY[key] ? CHORD_SCALE_BARRY : CHORD_SCALE);
    const name = table[key] || CHORD_SCALE[key] || 'ionian';
    return { tones: SCALES[name], root: chordRoot };
  }
  // Custom: a twelve bit mask, one bit per chromatic degree.
  const tones = [];
  for (let i = 0; i < 12; i++) if (customMask & (1 << i)) tones.push(i);
  if (!tones.length) return null;
  return { tones, root: m.kind === 'customKey' ? KEY_SIGS[keySig][1] : chordRoot };
}

let sharpOn = false, latchOn = false, barryOn = false, stackOn = false;
let gestureRows = null;   // rows added by the current press-and-drag
let chordOct = 0, strumOct = 0, kbOct = 0;
let inversion = 0, spacing = 0;

// A small -/+ stepper. Limits differ per control because each sits at a
// different height in the firmware's MIDI 24..96 note table, and a setting
// that just gets folded back in looks like a broken button.
function makeOctave(id, get, set, lo, hi){
  const host = document.getElementById(id);
  if (!host) return () => {};
  const minus = document.createElement('button'); minus.textContent = '\u2212';
  const read  = document.createElement('span');
  const plus  = document.createElement('button'); plus.textContent = '+';
  host.appendChild(minus); host.appendChild(read); host.appendChild(plus);

  function show(){
    const v = get();
    read.textContent = v > 0 ? ('+' + v) : String(v);
  }
  function bump(d){
    const v = Math.max(lo, Math.min(hi, get() + d));
    set(v);
    try { localStorage.setItem('8b8.' + id, v); } catch(e){}
    show();
  }
  minus.onclick = () => bump(-1);
  plus.onclick  = () => bump(1);
  try {
    const saved = localStorage.getItem('8b8.' + id);
    if (saved !== null) set(Math.max(lo, Math.min(hi, parseInt(saved, 10) || 0)));
  } catch(e){}
  show();
  return show;
}
let activeRoot = 1;               // column currently selected
let heldRows = new Set();         // rows physically held right now
// What is actually sounding, which is not the same thing: with Hold on the
// buttons are released while the chord keeps playing. Re-voicing controls
// key off this, otherwise changing octave or spacing under Hold does
// nothing -- which is exactly when you would reach for them.
let soundingKey = null;
// The last chord the harp was built from. Kept after the button is released
// so the strumpad stays playable without needing Hold on -- pressing a chord
// primes the harp, and it stays primed until the next chord.
let lastHarpKey = null;
let chordNotes = [];
let strumNotes = [];
let lastStrumSeg = -1, strumming = false;

function currentKey(){
  return heldRows.size ? [...heldRows].sort().join('') : soundingKey;
}

function specForKey(key){
  // Barry Harris rewrites the standard triads; in the alternate layout those
  // combinations are different chords entirely, so it does not apply there.
  if (altLayout){
    const a = ALT_CHORDS[key];
    return a ? { tones: a.tones, suffix: a.suffix } : null;
  }
  const c = CHORDS[key];
  if (!c) return null;
  const useBarry = barryOn && c.barry;
  return { tones: useBarry ? c.barry : c.tones,
           suffix: useBarry ? c.bSuffix : c.suffix };
}

function chordSpec(){ return specForKey(currentKey()); }

// Inversion decides which chord tone sits at the bottom; spacing decides how
// far apart the voices sit. Drop voicings take the close position and drop
// the Nth voice counted from the TOP by an octave -- drop 2 the second from
// the top, drop 3 the third, drop 2+4 both. Spread moves the outer voices
// apart instead, which is wider but leaves the middle clustered.
function voiceChord(tones, base){
  let notes = tones.map(t => base + t).sort((a, b) => a - b);

  for (let i = 0; i < inversion; i++){
    notes[0] += 12;                 // lowest voice up an octave
    notes.sort((a, b) => a - b);
  }

  const n = notes.length;
  const DROPS = { 1: [2], 2: [3], 3: [2, 4] };
  if (spacing >= 1 && spacing <= 3){
    for (const d of DROPS[spacing]){
      const i = n - d;              // dth voice counted from the top
      if (i >= 0) notes[i] -= 12;   // a triad has no fourth voice to drop
    }
  } else if (spacing === 4){
    notes[0] -= 12;
    notes[n - 1] += 12;
  }
  notes.sort((a, b) => a - b);

  // Shift the whole voicing into the firmware's note table rather than
  // clamping individual notes, which would collapse voices onto each other.
  while (notes[notes.length - 1] > 96) notes = notes.map(x => x - 12);
  while (notes[0] < 24) notes = notes.map(x => x + 12);
  return notes;
}

function chordFor(){
  const spec = chordSpec();
  if (!spec) return [];
  return voiceChord(spec.tones, rootMidi(activeRoot) + chordOct * 12);
}

// The harp: chord tones stacked upward until twelve sections are filled, so
// a strum walks the chord rather than a scale.
function ladderFor(){
  const sc = harpScaleFor();
  if (!sc) return [];
  const n = strumCount();
  // Fold the tones into one octave and sort them. A chord can reach past an
  // octave -- the alternate layout's ninths hold a 14 -- and stacking those
  // by the octave would put the ninth above the next repeat's root, so the
  // strings stopped ascending. The harp plays the chord's pitch classes
  // across octaves, which is what a harp does anyway.
  const tones = [...new Set(sc.tones.map(t => ((t % 12) + 12) % 12))].sort((a, b) => a - b);

  // Start an octave below the chord so there is room to climb, aligned to the
  // scale's own root rather than the chord's.
  const anchor = rootMidi(activeRoot) - 12 + strumOct * 12;
  let base = Math.floor(anchor / 12) * 12 + sc.root;
  if (base > anchor) base -= 12;

  let out = [];
  for (let i = 0; i < n; i++){
    out.push(base + tones[i % tones.length] + 12 * Math.floor(i / tones.length));
  }
  // A very short custom scale would otherwise climb an octave per string and
  // run off the top of the note table, so the octave is capped and the upper
  // strings repeat instead.
  while (Math.max.apply(null, out) > 96) out = out.map(x => x - 12);
  while (Math.min.apply(null, out) < 24) out = out.map(x => x + 12);
  out = out.map(x => Math.max(24, Math.min(96, x)));
  return out;
}

function releaseChord(){
  chordNotes.forEach(n => stopNote(n, 0));
  chordNotes = [];
}

// A chord change happens on a PRESS, never on a release. That is the
// minichord's rule and it matters: timing a release across three buttons is
// hopeless, and without it every complex chord would collapse through
// unwanted intermediate chords on the way out.
function pressRow(rootIdx, rowIdx){
  if (rootIdx !== activeRoot){ heldRows.clear(); activeRoot = rootIdx; }
  heldRows.add(rowIdx);
  soundingKey = [...heldRows].sort().join('');
  lastHarpKey = soundingKey;
  refreshChord();
}

function releaseRow(rowIdx){
  heldRows.delete(rowIdx);
  if (heldRows.size === 0 && !latchOn){
    releaseChord();
    soundingKey = null;
    updateStrum();                 // stays primed from lastHarpKey
  }
  paintChords();
}

// Re-form whatever is currently sounding. Chord notes cannot glide to a new
// voicing, so this restarts them; that retrigger IS the update.
function revoice(){
  if (currentKey()) refreshChord();
}

function refreshChord(){
  const spec = chordSpec();
  if (!spec) return;
  releaseChord();
  chordNotes = chordFor();
  chordNotes.forEach(n => playNote(n, 100, 0));
  updateStrum();
  const el = document.getElementById('chordName');
  if (el) el.textContent = rootName(activeRoot) + spec.suffix;
  paintChords();
}

// The buttons say what they will play, so switching layout relabels them
// rather than leaving the old names on a different set of chords.
function relabelChords(){
  document.querySelectorAll('.chordbtn').forEach(b => {
    const ri = b.dataset.root | 0, qi = b.dataset.q | 0;
    b.textContent = rootName(ri) + (altLayout ? ALT_ROW_SUFFIX[qi] : QUALITY[qi].suffix);
  });
  const sw = document.getElementById('barrySw');
  if (sw) sw.style.opacity = altLayout ? '0.35' : '';   // no effect here
}

// What Rate, Depth and Motion actually do, per mode. Three controls cover
// eleven effects, so without this the panel names them for none of them.
const WARP_LABELS = [
  ['Rate', 'Depth', 'Motion'],                  // Off
  ['Retrigger', 'Interval', 'Drift'],           // Sync Buzz
  ['Chop rate', 'Gap', 'Drift'],                // Stutter
  ['Jump rate', 'Spread', 'Drift'],             // Scramble
  ['Zap rate', 'Sweep', 'Drift'],               // Zap
  ['Fall time', 'Distance', 'Drift'],           // Tape Stop
  ['Siren speed', 'Range', 'Drift'],            // Siren
  ['Crush rate', 'Bits', 'Drift'],              // Crush
  ['Ring freq', 'Amount', 'Drift'],             // Ring
  ['Crush rate', 'Bits', 'Drift'],              // Env Crush
  ['Buzz freq', 'Bite', 'Drift']                // SID Voice
];

// Parameter controls carry no id, so the three are found once by their
// starting labels inside the Warp Zone module and kept. The label is a text
// node followed by the CC tag, so only the text node is rewritten: replacing
// the whole label would drop the CC binding readout with it.
let warpNameEls = null;
let warpModeRow = null;

function findWarpControls(){
  const mods = [...document.querySelectorAll('.module')];
  const warp = mods.find(m => {
    const h = m.querySelector('h2');
    return h && h.textContent.trim() === 'Warp Zone';
  });
  if (!warp) return;
  const want = { Rate: 0, Depth: 1, Motion: 2 };
  warpNameEls = [null, null, null];
  warp.querySelectorAll('.ctl .name').forEach(n => {
    const txt = (n.childNodes[0] && n.childNodes[0].nodeValue || '').trim();
    if (txt in want) warpNameEls[want[txt]] = n;
    // An enum renders as a row of buttons, not a select, so the mode is read
    // from which button is active rather than from a value.
    if (txt === 'Mode') warpModeRow = n.closest('.ctl').querySelector('.enum-row');
  });
}

function paintWarpLabels(){
  if (!warpNameEls) findWarpControls();
  if (!warpNameEls || !warpModeRow) return;
  const btns = [...warpModeRow.querySelectorAll('.enum-btn')];
  const mode = Math.max(0, btns.findIndex(b => b.classList.contains('active')));
  const names = WARP_LABELS[mode % WARP_LABELS.length];
  warpNameEls.forEach((n, i) => {
    if (n && n.childNodes[0]) n.childNodes[0].nodeValue = names[i];
  });
}

function paintChords(){
  document.querySelectorAll('.chordbtn').forEach(b => {
    const key = currentKey() || '';
    const on = (b.dataset.root | 0) === activeRoot && key.indexOf(String(b.dataset.q)) >= 0;
    b.classList.toggle('latched', on && latchOn);
    b.classList.toggle('down', on && !latchOn);
  });
}

// Vertical runs high at the top, like a harp stood on end; horizontal runs
// low at the left, like a keyboard. Rebuilt on a change rather than reversed
// with CSS, so the segment order always matches what is under the finger.
// Every string says what it plays. Rebuilt whenever the ladder changes, so
// the labels follow the chord, the harp mode and the octave.
function labelStrum(){
  document.querySelectorAll('.strum .seg').forEach(el => {
    const i = el.dataset.seg | 0;
    const span = el.firstChild;
    if (!span) return;
    span.textContent = (i < strumNotes.length) ? noteName(strumNotes[i]) : '';
  });
}

// One place to rebuild the ladder, so the labels can never drift from it.
function updateStrum(){
  strumNotes = ladderFor();
  labelStrum();
}

function buildStrumSegments(){
  const strum = document.getElementById('strum');
  if (!strum) return;
  const n = strumCount();
  strum.innerHTML = '';
  strum.classList.toggle('horiz', strumLayout === 'h');
  strum.classList.toggle('grid', strumLayout === 'grid');
  if (strumLayout === 'v'){
    const lbl = document.createElement('div');
    lbl.className = 'label'; lbl.textContent = 'STRUM';
    strum.appendChild(lbl);
  }

  // Vertical runs high at the top like a harp stood on end. Horizontal runs
  // low at the left like a keyboard. The grid fills bottom-left upward, so a
  // sweep left to right rises and then carries on in the row above.
  const order = [];
  if (strumLayout === 'v'){
    for (let i = n - 1; i >= 0; i--) order.push(i);
  } else if (strumLayout === 'h'){
    for (let i = 0; i < n; i++) order.push(i);
  } else {
    const rows = Math.ceil(n / 3);
    for (let r = rows - 1; r >= 0; r--)
      for (let c = 0; c < 3; c++){
        const i = r * 3 + c;
        if (i < n) order.push(i);
      }
  }
  order.forEach(i => {
    const seg = document.createElement('div');
    seg.className = 'seg'; seg.dataset.seg = i;
    seg.appendChild(document.createElement('span'));
    strum.appendChild(seg);
  });
  labelStrum();
}

function buildPlaySurface(){
  const heads = document.getElementById('colHeads');
  const grid  = document.getElementById('chordGrid');
  if (!heads || !grid) return;

  ROOTS.forEach(r => {
    const h = document.createElement('div');
    h.className = 'colhead'; h.textContent = r[0];
    heads.appendChild(h);
  });

  QUALITY.forEach((q, qi) => {
    ROOTS.forEach((r, ri) => {
      const b = document.createElement('div');
      b.className = 'chordbtn row' + qi;
      b.dataset.root = ri; b.dataset.q = qi;
      b.textContent = r[0] + q.suffix;
      // A mouse has one pointer, so holding two buttons at once to build a
      // maj7 is impossible, and on a touchscreen it means two fingers on
      // small targets. Two ways round it, both single-pointer:
      //   drag  -- press one row and slide onto others in the same column
      //   Stack -- each tap adds or removes a row instead of replacing
      b.addEventListener('pointerdown', e => {
        e.preventDefault();
        b.setPointerCapture(e.pointerId);
        if (stackOn){
          if (heldRows.has(qi) && activeRoot === ri){
            heldRows.delete(qi);
            if (heldRows.size === 0){ soundingKey = null; releaseChord(); strumNotes = []; paintChords(); }
            else { soundingKey = [...heldRows].sort().join(''); refreshChord(); }
          } else {
            pressRow(ri, qi);
          }
          return;
        }
        gestureRows = new Set([qi]);
        pressRow(ri, qi);
      });

      b.addEventListener('pointermove', e => {
        if (stackOn || !gestureRows) return;
        // Pointer capture keeps the events here, so look up what is under it.
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const hit = el && el.closest ? el.closest('.chordbtn') : null;
        if (!hit) return;
        const r2 = hit.dataset.root | 0, q2 = hit.dataset.q | 0;
        if (r2 !== activeRoot || gestureRows.has(q2)) return;
        gestureRows.add(q2);
        pressRow(r2, q2);
      });

      const endGesture = () => {
        if (stackOn) return;              // taps persist until tapped again
        if (gestureRows){
          gestureRows.forEach(q => heldRows.delete(q));
          gestureRows = null;
        } else {
          heldRows.delete(qi);
        }
        if (heldRows.size === 0 && !latchOn){
          releaseChord();
          soundingKey = null;
          updateStrum();
        }
        paintChords();
      };
      b.addEventListener('pointerup', endGesture);
      b.addEventListener('pointercancel', endGesture);
      grid.appendChild(b);
    });
  });

  const strum = document.getElementById('strum');
  buildStrumSegments();

  // Strings behave like the on-screen keys: a note sounds for as long as the
  // finger is on it. Leaving a string -- by sliding onto the next one or by
  // lifting -- lets it ring on briefly rather than cutting it dead, so a
  // strum still overlaps the way a harp does.
  const HARP_RING_MS = 400;
  let heldStrum = null;          // { note, el } currently under the pointer

  // The firmware ignores a note-on for a note already sounding, so a string
  // the held chord is already playing would make no sound, and releasing it
  // would end the chord's note for good. Those restrike instead and are
  // never released here: the chord still owns them.
  function strumOn(n, el){
    if (chordNotes.indexOf(n) >= 0) stopNote(n, 0);
    playNote(n, 100, 0);
    el.classList.add('held');
  }

  function strumOff(entry, ring){
    if (!entry) return;
    entry.el.classList.remove('held');
    if (chordNotes.indexOf(entry.note) >= 0) return;   // the chord keeps it
    if (ring) setTimeout(() => stopNote(entry.note, 0), HARP_RING_MS);
    else stopNote(entry.note, 0);
  }

  function hit(seg, el){
    if (heldStrum && heldStrum.seg === seg) return;    // already on this one
    strumOff(heldStrum, true);                          // let the last one ring
    if (!strumNotes.length || seg >= strumNotes.length){ heldStrum = null; return; }
    const n = strumNotes[seg];
    strumOn(n, el);
    heldStrum = { note: n, el, seg };
    el.classList.add('lit');
    setTimeout(() => el.classList.remove('lit'), 120);
  }

  strum.addEventListener('pointerdown', e => {
    const el = e.target.closest('.seg');
    if (!el) return;
    e.preventDefault();
    strumming = true; lastStrumSeg = -1;
    strum.setPointerCapture(e.pointerId);
    hit(el.dataset.seg | 0, el);
  });
  strum.addEventListener('pointermove', e => {
    if (!strumming) return;
    // pointer capture keeps events here, so find the segment under the finger
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const seg = el && el.closest ? el.closest('.seg') : null;
    if (seg) hit(seg.dataset.seg | 0, seg);
  });
  const endStrum = () => {
    strumming = false;
    lastStrumSeg = -1;
    strumOff(heldStrum, true);     // ring on after the finger lifts
    heldStrum = null;
  };
  strum.addEventListener('pointerup', endStrum);
  strum.addEventListener('pointercancel', endStrum);

  // Modifier switches
  const sharpSw = document.getElementById('sharpSw');
  const latchSw = document.getElementById('latchSw');
  sharpSw.onclick = () => {
    sharpOn = !sharpOn;
    sharpSw.classList.toggle('active', sharpOn);
    relabelChords();
    revoice();
  };
  latchSw.onclick = () => {
    latchOn = !latchOn;
    latchSw.classList.toggle('active', latchOn);
    if (!latchOn){ heldRows.clear(); releaseChord(); strumNotes = []; soundingKey = null; }
    paintChords();
  };
  const dirSel = document.getElementById('strumDirSel');
  if (dirSel){
    dirSel.onchange = () => {
      strumLayout = dirSel.value;
      strumHorizontal = strumLayout === 'h';
      buildStrumSegments();
      try { localStorage.setItem('8b8.strumDir', strumLayout); } catch(e){}
    };
    try {
      const saved = localStorage.getItem('8b8.strumDir');
      if (saved){ dirSel.value = saved; strumLayout = saved; strumHorizontal = saved === 'h'; }
    } catch(e){}
  }

  const modeSel = document.getElementById('harpModeSel');
  const keySel  = document.getElementById('keySigSel');
  const degHost = document.getElementById('customDegrees');

  if (keySel){
    KEY_SIGS.forEach((k, i) => {
      const n = k[2];
      const o = document.createElement('option');
      o.value = i;
      o.textContent = k[0] + (n === 0 ? '' : '  ' + Math.abs(n) + (n > 0 ? '\u266f' : '\u266d'));
      keySel.appendChild(o);
    });
    keySel.value = keySig;
  }

  function refreshHarp(){
    const kind = HARP_MODES[harpMode].kind;
    if (degHost) degHost.hidden = (kind !== 'customKey' && kind !== 'customChord');
    // The key is not only the harp's now -- it spells the whole matrix -- so
    // it stays at full strength whatever the harp mode is.
    buildStrumSegments();
    if (currentKey() || kind === 'key' || kind === 'customKey') updateStrum();
  }

  if (modeSel){
    HARP_MODES.forEach((m, i) => {
      const o = document.createElement('option');
      o.value = i; o.textContent = m.name;
      modeSel.appendChild(o);
    });
    modeSel.onchange = () => {
      harpMode = modeSel.value | 0;
      try { localStorage.setItem('8b8.harpMode', harpMode); } catch(e){}
      refreshHarp();
    };
  }
  if (keySel){
    keySel.onchange = () => {
      keySig = keySel.value | 0;
      try { localStorage.setItem('8b8.keySig', keySig); } catch(e){}
      relabelChords();
      refreshHarp();
      if (currentKey()) refreshChord();
      updateSharpLabel();
    };
  }

  const accSel = document.getElementById('accModeSel');
  if (accSel){
    accSel.onchange = () => {
      accidentalMode = accSel.value | 0;
      try { localStorage.setItem('8b8.accMode', accidentalMode); } catch(e){}
      relabelChords();
      updateSharpLabel();
      if (currentKey()) refreshChord();
    };
  }

  // The button's own label follows what it will do, so it never says Sharp
  // while flattening.
  function updateSharpLabel(){
    const lab = document.querySelector('#sharpSw');
    if (!lab || !lab.parentElement) return;
    const txt = lab.parentElement.querySelector('.cc-label');
    if (!txt) return;
    const flat = accidentalMode === 2 ||
                 (accidentalMode === 0 && KEY_SIGS[keySig][2] < 0);
    txt.textContent = flat ? 'Flat' : 'Sharp';
  }
  updateSharpLabel();

  if (degHost){
    DEGREE_NAMES.forEach((nm, i) => {
      const d = document.createElement('div');
      d.className = 'deg' + ((customMask >> i) & 1 ? ' on' : '');
      d.textContent = nm;
      d.onclick = () => {
        customMask ^= (1 << i);
        d.classList.toggle('on', !!((customMask >> i) & 1));
        cnt.textContent = countDegrees() + ' notes';
        try { localStorage.setItem('8b8.customMask', customMask); } catch(e){}
        refreshHarp();
      };
      degHost.appendChild(d);
    });
    var cnt = document.createElement('span');
    cnt.className = 'note';
    degHost.appendChild(cnt);
  }
  function countDegrees(){
    let c = 0;
    for (let i = 0; i < 12; i++) if ((customMask >> i) & 1) c++;
    return c;
  }

  try {
    const hm = localStorage.getItem('8b8.harpMode');
    const hk = localStorage.getItem('8b8.keySig');
    const am = localStorage.getItem('8b8.accMode');
    if (am !== null){ accidentalMode = am | 0; const a = document.getElementById('accModeSel'); if (a) a.value = am; }
    const cm = localStorage.getItem('8b8.customMask');
    if (cm !== null) customMask = parseInt(cm, 10) || customMask;
    if (hm !== null && modeSel){ harpMode = hm | 0; modeSel.value = hm; }
    if (hk !== null && keySel){ keySig = hk | 0; keySel.value = hk; }
  } catch(e){}
  if (degHost){
    degHost.querySelectorAll('.deg').forEach((d, i) => d.classList.toggle('on', !!((customMask >> i) & 1)));
    cnt.textContent = countDegrees() + ' notes';
  }
  refreshHarp();

  const layoutSel = document.getElementById('chordLayoutSel');
  if (layoutSel){
    layoutSel.onchange = () => {
      altLayout = layoutSel.value === '1';
      relabelChords();
      if (currentKey()) refreshChord(); else updateStrum();
      try { localStorage.setItem('8b8.altLayout', altLayout ? '1' : '0'); } catch(e){}
    };
    try {
      if (localStorage.getItem('8b8.altLayout') === '1'){
        layoutSel.value = '1'; altLayout = true;
      }
    } catch(e){}
  }
  relabelChords();
  findWarpControls();
  paintWarpLabels();
  // Every mode button repaints, since the value arrives via setValue rather
  // than a change event.
  if (warpModeRow) warpModeRow.addEventListener('click', () => setTimeout(paintWarpLabels, 0));

  const stackSw = document.getElementById('stackSw');
  if (stackSw) stackSw.onclick = () => {
    stackOn = !stackOn;
    stackSw.classList.toggle('active', stackOn);
    if (!stackOn && !latchOn){
      heldRows.clear(); soundingKey = null; releaseChord(); strumNotes = [];
    }
    gestureRows = null;
    paintChords();
    try { localStorage.setItem('8b8.stack', stackOn ? '1' : '0'); } catch(e){}
  };
  try {
    if (localStorage.getItem('8b8.stack') === '1' && stackSw){
      stackOn = true; stackSw.classList.add('active');
    }
  } catch(e){}

  const barrySw = document.getElementById('barrySw');
  if (barrySw) barrySw.onclick = () => {
    barryOn = !barryOn;
    barrySw.classList.toggle('active', barryOn);
    revoice();
  };

  buildKeyboard();

  makeOctave('chordOct', () => chordOct, v => { chordOct = v; revoice(); }, -2, 2);
  makeOctave('strumOct', () => strumOct, v => {
    strumOct = v;
    if (currentKey()) updateStrum();   // harp only; no need to retrigger
  }, -1, 2);
  // The piano sits at C4 and is two octaves wide, so it can drop further
  // than it can climb before the top of the table folds keys together.
  const invSel = document.getElementById('inversionSel');
  const spcSel = document.getElementById('spacingSel');
  if (invSel) invSel.onchange = () => {
    inversion = invSel.value | 0;
    try { localStorage.setItem('8b8.inversion', inversion); } catch(e){}
    revoice();
  };
  if (spcSel) spcSel.onchange = () => {
    spacing = spcSel.value | 0;
    try { localStorage.setItem('8b8.spacing', spacing); } catch(e){}
    revoice();
  };
  try {
    const i = localStorage.getItem('8b8.inversion');
    const sp = localStorage.getItem('8b8.spacing');
    if (i !== null && invSel){ invSel.value = i; inversion = i | 0; }
    if (sp !== null && spcSel){ spcSel.value = sp; spacing = sp | 0; }
  } catch(e){}

  const showKb = makeOctave('kbOct', () => kbOct, v => { kbOct = v; showKbRange(); }, -3, 1);
  showKb();
  showKbRange();

  buildSequencer();
  buildGamepad();
}

/* ---- Keyboard: two octaves of piano, plus drum pads ---- */
// White keys carry the letter row; black keys sit over the gaps between
// them, which is what makes it readable at a glance and on a phone.
const WHITE = [0,2,4,5,7,9,11];                 // semitones of the naturals
const BLACK_AFTER = { 0:1, 1:3, 3:6, 4:8, 5:10 };  // white index -> black semitone
const PIANO_OCTAVES = 2, PIANO_BASE = 60;       // C4 up
const WHITE_KEYS = 'asdfghjkl;\'';
const BLACK_KEYS = 'wetyuop';

const PADS = [
  ['z', 36, 'Kick'],  ['x', 38, 'Snare'], ['c', 42, 'Hat'],   ['v', 46, 'OpnHat'],
  ['b', 41, 'LoTom'], ['n', 50, 'HiTom'], ['m', 49, 'Crash'], [',', 56, 'Cowbell']
];

const keyToNote = {};    // letter -> {note, chan}
const heldKeys = new Set();

function buildKeyboard(){
  const piano = document.getElementById('piano');
  const pads  = document.getElementById('pads');
  if (!piano || !pads) return;

  const nWhite = WHITE.length * PIANO_OCTAVES;
  let wi = 0, bi = 0;

  for (let oct = 0; oct < PIANO_OCTAVES; oct++){
    WHITE.forEach((semi, idx) => {
      const note = PIANO_BASE + oct * 12 + semi;
      const letter = WHITE_KEYS[wi] || '';
      if (letter) keyToNote[letter] = { note, chan: 0 };
      const el = document.createElement('div');
      el.className = 'wkey';
      el.dataset.note = note;
      if (letter) el.dataset.key = letter;
      el.innerHTML = letter ? '<b>' + letter.toUpperCase() + '</b>' : '';
      piano.appendChild(el);
      wi++;
    });
  }

  // Blacks are positioned on the boundary between two whites, as a
  // percentage of the keyboard width, so they track any container size.
  for (let oct = 0; oct < PIANO_OCTAVES; oct++){
    Object.keys(BLACK_AFTER).forEach(k => {
      const idx = parseInt(k, 10);
      const note = PIANO_BASE + oct * 12 + BLACK_AFTER[idx];
      const pos = oct * WHITE.length + idx + 1;      // boundary after this white
      const letter = BLACK_KEYS[bi] || '';
      if (letter) keyToNote[letter] = { note, chan: 0 };
      const el = document.createElement('div');
      el.className = 'bkey';
      el.dataset.note = note;
      if (letter) el.dataset.key = letter;
      el.style.left  = (pos * 100 / nWhite) + '%';
      el.style.width = (100 / nWhite * 0.62) + '%';
      el.innerHTML = letter ? '<b>' + letter.toUpperCase() + '</b>' : '';
      piano.appendChild(el);
      bi++;
    });
  }

  PADS.forEach(([letter, note, label]) => {
    keyToNote[letter] = { note, chan: 9 };
    const el = document.createElement('div');
    el.className = 'pad';
    el.dataset.note = note; el.dataset.key = letter; el.dataset.drum = '1';
    el.innerHTML = '<b>' + letter.toUpperCase() + '</b>' + label;
    pads.appendChild(el);
  });
}

const NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const NOTE_NAMES_FLAT = ['C','D\u266d','D','E\u266d','E','F','G\u266d','G','A\u266d','A','B\u266d','B'];

// Labels follow the key's accidental, so a strumpad in E flat reads B flat
// rather than A sharp. This is a spelling of pitch CLASSES, not true note
// spelling -- it will never give you an E sharp or an F flat, which needs
// the note's letter to be tracked rather than deduced.
function noteName(n){
  const flat = KEY_SIGS[keySig][2] < 0;
  return (flat ? NOTE_NAMES_FLAT : NOTE_NAMES)[n % 12] + (Math.floor(n / 12) - 1);
}

// Applied when a key is struck rather than baked into the elements, so the
// octave can change with notes held without stranding them.
function kbNote(n){ return Math.max(24, Math.min(96, n + kbOct * 12)); }

function showKbRange(){
  const el = document.getElementById('kbRange');
  if (!el) return;
  el.textContent = noteName(kbNote(PIANO_BASE)) + ' \u2013 ' +
                   noteName(kbNote(PIANO_BASE + PIANO_OCTAVES * 12 - 1));
}

function paintKey(letter, on){
  document.querySelectorAll('[data-key="' + letter + '"]').forEach(e => e.classList.toggle('down', on));
}

window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const k = e.key.toLowerCase();
  const m = keyToNote[k];
  if (!m || heldKeys.has(k)) return;
  heldKeys.add(k);
  playNote(m.chan === 9 ? m.note : kbNote(m.note), 100, m.chan);
  paintKey(k, true);
});
window.addEventListener('keyup', e => {
  const k = e.key.toLowerCase();
  if (!heldKeys.has(k)) return;
  heldKeys.delete(k);
  const m = keyToNote[k];
  if (m && m.chan !== 9) stopNote(kbNote(m.note), 0);
  paintKey(k, false);
});

// Pointer play. Drums are one-shots, so they are never held.
document.addEventListener('pointerdown', e => {
  const el = e.target.closest('.wkey, .bkey, .pad');
  if (!el) return;
  e.preventDefault();
  const raw = el.dataset.note | 0;
  const drum = el.dataset.drum === '1';
  const note = drum ? raw : kbNote(raw);
  playNote(note, 100, drum ? 9 : 0);
  el.classList.add('down');
  const up = () => {
    if (!drum) stopNote(note, 0);
    el.classList.remove('down');
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
  };
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
});


/* ==== Standard MIDI File playback ======================================= */
// A .mid is a header chunk then one track chunk per part, each a stream of
// delta-time + event. The awkward parts are variable-length quantities, the
// running status byte that lets an event omit its status if it matches the
// last one, and the fact that tempo lives in meta events which may appear
// anywhere and change mid-piece -- so ticks cannot be turned into seconds
// until every track has been merged into one timeline.

function midiReader(buf){
  const d = new DataView(buf), u = new Uint8Array(buf);
  let p = 0;
  return {
    pos: () => p,
    seek: n => { p = n; },
    left: () => u.length - p,
    u8:  () => u[p++],
    u16: () => { const v = d.getUint16(p); p += 2; return v; },
    u32: () => { const v = d.getUint32(p); p += 4; return v; },
    str: n => { let s = ''; for (let i = 0; i < n; i++) s += String.fromCharCode(u[p++]); return s; },
    skip: n => { p += n; },
    // Variable-length quantity: seven bits per byte, top bit means "more".
    vlq: () => {
      let v = 0, b;
      do { b = u[p++]; v = (v << 7) | (b & 0x7F); } while (b & 0x80);
      return v;
    }
  };
}

function parseMidiFile(buf){
  const r = midiReader(buf);
  if (r.str(4) !== 'MThd') throw new Error('not a MIDI file (no MThd header)');
  const hdrLen = r.u32();
  const format = r.u16();
  const ntrks  = r.u16();
  const division = r.u16();
  r.skip(hdrLen - 6);                       // headers may be longer than six
  if (format === 2) throw new Error('format 2 files are independent sequences, not a song');

  // Every event from every track, on one timeline measured in ticks.
  const all = [];
  let seenTempo = false;
  // ntrks counts MTrk chunks only, so an unknown chunk must not use up an
  // iteration. Skipping one rather than stopping matters because abandoning
  // the rest of the file loses any tempo in a later track, and the 120bpm
  // default then plays the piece at the wrong speed with nothing else wrong.
  let parsed = 0;
  while (parsed < ntrks && r.left() > 8){
    const kind = r.str(4);
    const len = r.u32();
    if (kind !== 'MTrk'){ r.skip(len); continue; }
    parsed++;
    const end = r.pos() + len;
    let tick = 0, running = 0;
    while (r.pos() < end){
      tick += r.vlq();
      let st = r.u8();
      if (st < 0x80){ r.seek(r.pos() - 1); st = running; }   // running status
      else if (st < 0xF0) running = st;
      else running = 0;      // a system or meta event cancels running status

      if (st === 0xFF){                       // meta
        const type = r.u8(), n = r.vlq();
        if (type === 0x51 && n === 3){
          const a = r.u8(), b = r.u8(), c = r.u8();
          all.push({ tick, meta: 'tempo', usPerBeat: (a << 16) | (b << 8) | c });
          seenTempo = true;
        } else r.skip(n);
      } else if (st === 0xF0 || st === 0xF7){
        r.skip(r.vlq());                      // sysex, nothing here wants it
      } else {
        const cmd = st & 0xF0, chan = st & 0x0F;
        const d1 = r.u8();
        const d2 = (cmd === 0xC0 || cmd === 0xD0) ? 0 : r.u8();
        if (cmd === 0x90 && d2 > 0)      all.push({ tick, on: true,  chan, note: d1, vel: d2 });
        else if (cmd === 0x80 || cmd === 0x90) all.push({ tick, on: false, chan, note: d1 });
      }
    }
    r.seek(end);
  }

  all.sort((a, b) => a.tick - b.tick);

  // Ticks to seconds. SMPTE division is a fixed tick rate; otherwise ticks
  // are per quarter note and the rate moves with every tempo change.
  const smpte = (division & 0x8000) !== 0;
  const smpteRate = smpte ? ((256 - (division >> 8)) * (division & 0xFF)) : 0;
  let usPerBeat = 500000;                     // 120bpm until told otherwise
  let lastTick = 0, seconds = 0;
  const out = [];
  for (const e of all){
    const dt = e.tick - lastTick;
    seconds += smpte ? (dt / smpteRate)
                     : (dt * usPerBeat / (division * 1000000));
    lastTick = e.tick;
    if (e.meta === 'tempo'){ usPerBeat = e.usPerBeat; continue; }
    out.push({ t: seconds, on: e.on, chan: e.chan, note: e.note, vel: e.vel || 100 });
  }

  // How many notes want to sound at once: nine is all there is.
  let live = 0, peak = 0;
  for (const e of out){ live += e.on ? 1 : -1; if (live > peak) peak = live; }

  return { events: out, duration: seconds, tracks: ntrks, format, peak,
           bpm: Math.round(60000000 / usPerBeat),
           // Unrounded, for the sequencer. A file at 103.4bpm synced to an
           // integer 103 drifts 0.4 per cent, which is a tenth of a second
           // every thirty and audible against a pattern.
           bpmExact: 60000000 / usPerBeat,
           seenTempo };
}

/* ---- playback ---- */
// One timer, firing everything whose time has passed. Deliberately NOT
// looking ahead: notes are played the instant they are dispatched, so
// dispatching early would play them early -- a lookahead only helps when the
// events can carry a timestamp, which these cannot. A short interval keeps
// the error small and always on the late side, which is the forgiving one.
// The file's own bytes, kept so a saved game can carry the piece as well
// as the settings. Base64 rather than the ArrayBuffer, since that is what
// has to go into JSON anyway.
let midiRaw = null, midiName = '';
let midiFile = null, midiPlaying = false, midiTimer = null;
let midiNext = 0, midiHeldNotes = [];
let midiSpeed = 1.0, midiLoop = false, midiSync = false;

// Points the sequencer at the file's tempo, scaled by the playback speed, so
// a pattern played alongside a file stays with it. The tempo control is
// capped at 50 to 200, and a file outside that is clamped rather than
// refused: half or double a very fast or slow piece still lines up on the
// beat, which a mismatched tempo does not.
function midiApplyTempo(){
  if (!midiSync || !midiFile) return;
  const t = document.getElementById('seqTempo');
  if (!t) return;
  const want = Math.round(midiFile.bpm * midiSpeed);
  const bpm = Math.max(50, Math.min(200, want));
  const exact = Math.max(50, Math.min(200, midiFile.bpmExact * midiSpeed));
  t.value = bpm;
  document.getElementById('seqBpm').textContent = bpm + ' BPM'
    + (bpm !== want ? ' (file ' + want + ')' : '');
  seqEngineTempo(exact);
  updateTransport();
}
// The play position is accumulated rather than derived from a start time, so
// the speed can change mid-file without the position jumping, and a loop can
// reset it without disturbing anything else.
let midiClock = 0, midiLastReal = 0;

function bytesToBase64(bytes){
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

function loadMidiFromBase64(b64, name){
  const raw = atob(b64);
  const buf = new ArrayBuffer(raw.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) view[i] = raw.charCodeAt(i);
  midiFile = parseMidiFile(buf);
  midiRaw = b64;
  midiName = name;
  const nameEl = document.getElementById('midiFileName');
  if (nameEl) nameEl.textContent = name;
  midiStatus(name + ': ' + midiFile.events.length + ' notes, ' +
             midiFile.duration.toFixed(1) + 's, ' + midiFile.bpm + ' BPM');
  midiApplyTempo();
}

function midiStatus(msg){
  const el = document.getElementById('midiFileInfo');
  if (el) el.textContent = msg;
}

function midiAllOff(){
  midiHeldNotes.forEach(e => stopNote(e.note, e.chan === 9 ? 9 : 0));
  midiHeldNotes = [];
}

function midiStop(){
  if (midiSync && seqPlaying) toggleSeq();
  if (midiInEngine) midiEngineStop();
  midiInEngine = false;
  midiPlaying = false;
  if (midiTimer) clearInterval(midiTimer);
  midiTimer = null;
  midiAllOff();
  const b = document.getElementById('midiPlayBtn');
  if (b) b.textContent = 'Play File';
}

// True when the engine is timing playback, so the JS timer only draws the
// readout instead of dispatching notes.
let midiInEngine = false;

function midiPlay(){
  if (!midiFile || !midiFile.events.length) return;
  midiStop();
  midiPlaying = true;
  midiNext = 0;
  midiClock = 0;
  midiLastReal = performance.now();
  midiInEngine = midiEngineLoad(midiFile.events);
  if (midiInEngine) midiEngineStart(midiSpeed, midiLoop);

  // With Sync on the two run as one transport. Started in the same turn they
  // begin on the same render, so the pattern and the file share a downbeat
  // rather than being however far apart the two presses were.
  if (midiSync && !seqPlaying) toggleSeq();
  const b = document.getElementById('midiPlayBtn');
  if (b) b.textContent = 'Stop File';

  midiTimer = setInterval(() => {
    if (!midiPlaying) return;
    if (midiInEngine){
      const at = midiEnginePos();
      midiStatus(at.toFixed(1) + ' / ' + midiFile.duration.toFixed(1) + ' s' +
                 (midiSpeed !== 1 ? '   x' + midiSpeed.toFixed(2) : '') +
                 (midiLoop ? '   loop' : ''));
      if (!midiLoop && at === 0 && midiClock > 0.5) midiStop();
      midiClock += 0.005;
      return;
    }
    const real = performance.now();
    midiClock += ((real - midiLastReal) / 1000) * midiSpeed;
    midiLastReal = real;
    const now = midiClock;
    while (midiNext < midiFile.events.length && midiFile.events[midiNext].t <= now){
      const e = midiFile.events[midiNext++];
      // Channel 10 is percussion by convention; everything else is melodic.
      const ch = (e.chan === 9) ? 9 : 0;
      if (e.on){
        playNote(e.note, e.vel, ch);
        if (ch !== 9) midiHeldNotes.push(e);
      } else {
        // Route the release to the channel it was played on. Sending a drum's
        // note-off to channel 0 would stop whichever melodic voice happened to
        // be holding that same note number.
        stopNote(e.note, ch);
        const i = midiHeldNotes.findIndex(h => h.note === e.note && h.chan === e.chan);
        if (i >= 0) midiHeldNotes.splice(i, 1);
      }
    }
    midiStatus(now.toFixed(1) + ' / ' + midiFile.duration.toFixed(1) + ' s' +
               (midiSpeed !== 1 ? '   x' + midiSpeed.toFixed(2) : '') +
               (midiLoop ? '   loop' : ''));

    if (midiNext >= midiFile.events.length && now > midiFile.duration + 0.25){
      if (midiLoop){
        // Release everything before restarting, or a note held across the
        // join would never get its note-off and would hang for good.
        midiAllOff();
        midiNext = 0;
        midiClock = 0;
      } else {
        midiStop();
      }
    }
  }, 5);
}

function buildMidiFile(){
  const input = document.getElementById('midiFileInput');
  const btn   = document.getElementById('midiPlayBtn');
  if (!input || !btn) return;

  input.onchange = () => {
    const f = input.files && input.files[0];
    const nameEl = document.getElementById('midiFileName');
    if (!f) return;
    if (nameEl) nameEl.textContent = f.name;
    const fr = new FileReader();
    fr.onload = () => {
      try {
        midiFile = parseMidiFile(fr.result);
        midiRaw = bytesToBase64(new Uint8Array(fr.result));
        midiName = f.name;
        const warn = midiFile.peak > 9
          ? '  \u2014 wants ' + midiFile.peak + ' notes at once, only 9 voices: some will be stolen'
          : '';
        // The tempo is worth showing: a file with no tempo event plays at the
        // 120bpm default, which is the usual reason a piece runs at the wrong
        // speed with nothing obviously broken.
        const tempo = midiFile.seenTempo
          ? midiFile.bpm + ' BPM'
          : '120 BPM (none in file)';
        midiStatus(f.name + ': ' + midiFile.events.length + ' notes, ' +
                   midiFile.duration.toFixed(1) + 's, ' + midiFile.tracks +
                   ' track(s), ' + tempo + warn);
        log(midiFile.peak > 9 ? 'err' : 'sys',
            'Loaded ' + f.name + ' (format ' + midiFile.format + ', peak polyphony ' +
            midiFile.peak + ')');
        midiApplyTempo();
      } catch (err){
        midiFile = null;
        midiStatus('Could not read that file: ' + err.message);
        log('err', 'MIDI file: ' + err.message);
      }
    };
    fr.readAsArrayBuffer(f);
  };

  btn.onclick = () => { midiPlaying ? midiStop() : midiPlay(); };

  // Start with the bundled piece loaded, so the player can be pressed without
  // finding a file first. Any file the user picks replaces it.
  const demo = '__DEMO_MIDI__';
  if (demo){
    try {
      const raw = atob(demo);
      const buf = new ArrayBuffer(raw.length);
      const view = new Uint8Array(buf);
      for (let i = 0; i < raw.length; i++) view[i] = raw.charCodeAt(i);
      midiFile = parseMidiFile(buf);
      midiRaw = demo;
      midiName = 'invention.mid';
      const nameEl = document.getElementById('midiFileName');
      if (nameEl) nameEl.textContent = 'invention.mid';
      midiStatus('invention.mid (included): ' + midiFile.events.length + ' notes, ' +
                 midiFile.duration.toFixed(1) + 's, ' + midiFile.bpm + ' BPM. ' +
                 'Press Play File, or choose your own.');
      midiApplyTempo();
    } catch (err){
      midiFile = null;
    }
  }

  const saveBtn = document.getElementById('imgSave');
  if (saveBtn) saveBtn.onclick = saveImage;

  const loadBtn = document.getElementById('imgLoad');
  const loadIn  = document.getElementById('imgLoadInput');
  if (loadBtn && loadIn){
    loadBtn.onclick = () => loadIn.click();
    loadIn.onchange = () => {
      const f = loadIn.files && loadIn.files[0];
      if (!f) return;
      const fr = new FileReader();
      fr.onload = () => {
        try {
          const img = JSON.parse(fr.result);
          const missing = applyImage(img);
          let msg = 'Loaded ' + f.name;
          if (img.layout !== LAYOUT_VERSION){
            msg += '  \u2014 made on layout ' + img.layout + ', this is ' +
                   LAYOUT_VERSION;
          }
          if (missing.length){
            msg += '. ' + missing.length + ' setting(s) not in the save, left as they were: '
                 + missing.join(', ');
          }
          log(missing.length || img.layout !== LAYOUT_VERSION ? 'err' : 'sys', msg);
        } catch (err){
          log('err', 'Could not read that save: ' + err.message);
        }
      };
      fr.readAsText(f);
    };
  }

  const spd = document.getElementById('midiSpeed');
  const spdVal = document.getElementById('midiSpeedVal');
  if (spd) spd.oninput = () => {
    midiSpeed = (parseInt(spd.value, 10) || 100) / 100;
    spdVal.textContent = 'Speed ' + midiSpeed.toFixed(2) + 'x';
    midiEngineSpeed(midiSpeed);
    midiApplyTempo();
  };

  const loopSw = document.getElementById('midiLoopSw');
  if (loopSw) loopSw.onclick = () => {
    midiLoop = !midiLoop;
    loopSw.classList.toggle('active', midiLoop);
    midiEngineLoop(midiLoop);
  };

  const syncSw = document.getElementById('midiSyncSw');
  if (syncSw) syncSw.onclick = () => {
    midiSync = !midiSync;
    syncSw.classList.toggle('active', midiSync);
    midiApplyTempo();
  };
}

/* ==== Instrument image ================================================== */
// One file holding everything that decides what the instrument sounds like:
// the parameter bank, the sequencer's grids and transport, the chord matrix
// and harp, and the loaded MIDI file. Enough to hand someone else and have
// them hear what you are hearing.
//
// The layout version travels with it. Parameters are stored by NAME rather
// than index, so an image made before a parameter was added still loads: the
// names that still exist are applied and the rest reported, instead of a
// silent misalignment where every value lands one slot out.

const IMAGE_FORMAT = 1;

function captureImage(){
  const img = {
    format: IMAGE_FORMAT,
    layout: LAYOUT_VERSION,
    saved: new Date().toISOString(),
    params: {},
    seq: {
      drums: seqGrid.map(r => r.slice()),
      notes: noteGrid.map(r => r.slice()),
      assign: drumAssign.slice(),
      length: seqLength,
      page: seqPage,
      tempo: parseInt(document.getElementById('seqTempo').value, 10) || 110,
      swing: parseInt(document.getElementById('seqSwing').value, 10) || 0,
      sig: document.getElementById('seqSig').value,
      playing: seqPlaying
    },
    chords: {
      keySig, accidentalMode, altLayout, barryOn, stackOn, sharpOn, latchOn,
      inversion, spacing, chordOct, strumOct, kbOct,
      harpMode, customMask, strumLayout
    }
  };

  PARAMS.forEach(p => { img.params[p.key] = values[p.index]; });

  // The MIDI file travels with the image, or the sequence would arrive
  // without the piece it was playing against.
  if (midiFile && midiRaw){
    img.midi = { name: midiName, data: midiRaw, speed: midiSpeed,
                 loop: midiLoop, sync: midiSync, playing: midiPlaying };
  }
  return img;
}

function applyImage(img){
  if (!img || img.format !== IMAGE_FORMAT) throw new Error('not an 8b8 save');

  const missing = [];
  if (img.params){
    PARAMS.forEach(p => {
      if (img.params[p.key] === undefined){ missing.push(p.key); return; }
      setValue(p.index, img.params[p.key], true);
    });
  }

  if (img.seq){
    const q = img.seq;
    if (q.drums) q.drums.forEach((r, i) => { if (seqGrid[i]) seqGrid[i] = r.slice(); });
    if (q.notes) q.notes.forEach((r, i) => { if (noteGrid[i]) noteGrid[i] = r.slice(); });
    if (q.assign){
      drumAssign = q.assign.slice();
      document.querySelectorAll('#seqRows .seqname select').forEach((sel, i) => {
        sel.value = String(drumAssign[i]);
      });
    }
    if (q.length) setSeqLength(q.length);
    if (q.page !== undefined){
      seqPage = q.page;
      const ps = document.getElementById('seqPageSel');
      if (ps) ps.value = String(seqPage);
      if (seqPage > maxPageOpened) maxPageOpened = seqPage;
    }
    const t = document.getElementById('seqTempo');
    if (t && q.tempo){ t.value = q.tempo; t.oninput(); }
    const sw = document.getElementById('seqSwing');
    if (sw && q.swing !== undefined){ sw.value = q.swing; sw.oninput(); }
    const sg = document.getElementById('seqSig');
    if (sg && q.sig){ sg.value = q.sig; sg.onchange(); }
    paintSeq(); paintLength();
    seqEnginePattern(seqRowsForEngine());
  }

  if (img.chords){
    const c = img.chords;
    if (c.keySig !== undefined){
      keySig = c.keySig;
      const k = document.getElementById('keySigSel');
      if (k){ k.value = String(keySig); }
    }
    if (c.accidentalMode !== undefined) accidentalMode = c.accidentalMode;
    if (c.altLayout !== undefined) altLayout = c.altLayout;
    if (c.barryOn !== undefined) barryOn = c.barryOn;
    if (c.stackOn !== undefined) stackOn = c.stackOn;
    if (c.sharpOn !== undefined) sharpOn = c.sharpOn;
    if (c.latchOn !== undefined) latchOn = c.latchOn;
    [['sharpSw', sharpOn], ['barrySw', barryOn], ['stackSw', stackOn],
     ['latchSw', latchOn]].forEach(([id, on]) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle('active', !!on);
    });
    if (c.inversion !== undefined) inversion = c.inversion;
    if (c.spacing !== undefined) spacing = c.spacing;
    if (c.chordOct !== undefined) chordOct = c.chordOct;
    if (c.strumOct !== undefined) strumOct = c.strumOct;
    if (c.kbOct !== undefined) kbOct = c.kbOct;
    if (c.harpMode !== undefined) harpMode = c.harpMode;
    if (c.customMask !== undefined) customMask = c.customMask;
    if (c.strumLayout !== undefined) strumLayout = c.strumLayout;
    relabelChords();
    // Redraws the ladder from the restored key, harp mode and octaves.
    updateStrum();
  }

  if (img.midi && img.midi.data){
    loadMidiFromBase64(img.midi.data, img.midi.name || 'shared.mid');
    const sp = document.getElementById('midiSpeed');
    if (sp && img.midi.speed){ sp.value = Math.round(img.midi.speed * 100); sp.oninput(); }
    if (img.midi.loop && !midiLoop) document.getElementById('midiLoopSw').onclick();
    if (img.midi.sync && !midiSync) document.getElementById('midiSyncSw').onclick();
  }

  // Start whatever was running, so the image plays rather than merely loads.
  if (img.seq && img.seq.playing && !seqPlaying) toggleSeq();
  if (img.midi && img.midi.playing && !midiPlaying) midiPlay();

  return missing;
}

function saveImage(){
  const blob = new Blob([JSON.stringify(captureImage(), null, 1)],
                        { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '8b8-save-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.8b8';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  log('sys', 'Game saved. It carries the whole instrument, including whatever is playing.');
}

/* ==== Drum sequencer ==================================================== */
// Sixteen steps against a drift-corrected clock: each tick schedules the
// next from when it SHOULD have fired, so the pattern does not wander the
// way a plain setInterval does.
const SEQ_STEPS = 64;          // four pages of sixteen
const SEQ_PAGE = 16;
// Every drum the firmware has. A row can be pointed at any of them, so the
// eight rows are a working set rather than a fixed kit: the alternative, a
// second page of rows, would hide half a pattern behind a switch.
const DRUM_KIT = [
  [35, 'Acoustic kick'], [36, 'Kick'],        [37, 'Side stick'],
  [38, 'Snare'],         [39, 'Hand clap'],   [40, 'Elec snare'],
  [41, 'Low floor tom'], [42, 'Closed hat'],  [43, 'Hi floor tom'],
  [44, 'Pedal hat'],     [45, 'Low tom'],     [46, 'Open hat'],
  [47, 'Low-mid tom'],   [48, 'Hi-mid tom'],  [49, 'Crash 1'],
  [50, 'High tom'],      [51, 'Ride 1'],      [52, 'Chinese'],
  [53, 'Ride bell'],     [54, 'Tambourine'],  [55, 'Splash'],
  [56, 'Cowbell'],       [57, 'Crash 2'],     [58, 'Vibraslap'],
  [59, 'Ride 2']
];

const SEQ_ROWS = [
  { name: 'Kick',   note: 36 },
  { name: 'Snare',  note: 38 },
  { name: 'Hat',    note: 42 },
  { name: 'OpnHat', note: 46 },
  { name: 'LoTom',  note: 41 },
  { name: 'HiTom',  note: 50 },
  { name: 'Rim',    note: 37 },
  { name: 'Crash',  note: 49 }
];
const SEQ_PATTERNS = {
  'Empty':    {},
  'Four/Four':{ Kick:[0,4,8,12], Snare:[4,12], Hat:[0,2,4,6,8,10,12,14] },
  'Breakbeat':{ Kick:[0,3,9,10], Snare:[4,12], Hat:[0,2,4,6,8,10,12,14], OpnHat:[14] },
  'Arcade':   { Kick:[0,6,8,14], Snare:[4,12], Rim:[2,10], Crash:[0] },
  'Half Time':{ Kick:[0,10], Snare:[8], Hat:[0,4,8,12] },
  'Tom Roll': { LoTom:[0,2,4,6], HiTom:[8,10,12,14], Crash:[0] },
  // Son clave spans two bars, so these run to step 31 and set the length to
  // 32 on load. 3:2 puts the three-side first.
  // The son clave in cut time: the same figure as the two-bar version, at
  // half the note values, so it fits a page.
  'Clave 3:2': {
    assign: { Rim: 39, OpnHat: 56 },
    Rim:   [0, 3, 6, 10, 12],
    Kick:  [0, 8],
    OpnHat:[4, 12]
  },
  'Clave 2:3': {
    assign: { Rim: 39, OpnHat: 56 },
    Rim:   [2, 4, 8, 11, 14],
    Kick:  [0, 8],
    OpnHat:[4, 12]
  },
  // Jazz ride: quarter, quarter plus the swung upbeat, repeating. Needs the
  // Swing control up around 33% to sit as a triplet; flat it reads as
  // sixteenths. Hat on two and four is the foot.
  'Jazz Ride': {
    assign: { Crash: 51, OpnHat: 44 },
    Crash: [0, 4, 7, 8, 12, 15],
    OpnHat:[4, 12],
    Kick:  [0]
  }
};

// One row per semitone, highest at the top so it reads like a piano roll.
const NOTE_ROW_NAMES = ['B','A#','A','G#','G','F#','F','E','D#','D','C#','C'];
const NOTE_SEMIS     = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0];
const NOTE_BASE = 48;                       // C3 at octave 0
let noteOct = 0;
let seqLength = 16;            // how much of it actually plays
let seqPage = 0;               // which sixteen are on screen
let maxPageOpened = 0;         // the loop covers up to here, and no further
let seqBeatsPerBar = 4;        // where the accents fall
let stretch = null;            // the drag currently resizing a note

let seqGrid = SEQ_ROWS.map(() => new Array(SEQ_STEPS).fill(0));
let noteGrid = NOTE_SEMIS.map(() => new Array(SEQ_STEPS).fill(0));
let noteCells = [];
let seqPlaying = false;
let seqCells = [];             // cached elements: querying 128 cells per step was wasteful
let seqLastPainted = -1;

// Rows are handed to the engine as a 16-bit mask each, which is all the
// timing side needs to know about the pattern.
// Which drum each row currently plays. Patterns stay with the row, so
// repointing a row auditions the same rhythm on a different drum.
let drumAssign = SEQ_ROWS.map(r => r.note);
try {
  const saved = JSON.parse(localStorage.getItem('8b8.drumAssign') || 'null');
  if (Array.isArray(saved) && saved.length === SEQ_ROWS.length) drumAssign = saved;
} catch(e){}

function cellsOf(grid, ri){
  const cells = [];
  for (let s = 0; s < SEQ_STEPS; s++) if (grid[ri][s]) cells.push([s, grid[ri][s]]);
  return cells;
}

function seqRowsForEngine(){
  const rows = SEQ_ROWS.map((r, ri) => ({ note: drumAssign[ri], chan: 9, cells: cellsOf(seqGrid, ri) }));
  // Pitch rows follow, on channel 0, so they run through the melodic voices
  // and are released rather than being one-shots like the drums.
  NOTE_SEMIS.forEach((semi, ri) =>
    rows.push({ note: noteNoteFor(ri), chan: 0, cells: cellsOf(noteGrid, ri) }));
  return rows;
}

// Auditioning a pitch has to release it again. Without this the preview
// used the same bare playNote() the sequencer uses, so a clicked note
// sustained until that row's next scheduled note-off -- which never comes at
// all when the sequencer is stopped.
const previewTimers = {};
function previewNote(n, chan){
  chan = (chan === undefined) ? 0 : chan;
  if (chan === 9){ playNote(n, 110, 9); return; }   // drums are one-shots
  clearTimeout(previewTimers[n]);
  playNote(n, 100, 0);
  previewTimers[n] = setTimeout(() => {
    stopNote(n, 0);
    delete previewTimers[n];
  }, 180);
}

function noteNoteFor(ri){
  return Math.max(24, Math.min(96, NOTE_BASE + NOTE_SEMIS[ri] + noteOct * 12));
}

function showNoteRange(){
  const el = document.getElementById('noteRange');
  if (!el) return;
  const lo = NOTE_BASE + noteOct * 12;
  el.textContent = noteName(Math.max(24, Math.min(96, lo))) + ' \u2013 ' +
                   noteName(Math.max(24, Math.min(96, lo + 11)));
}

// Steps beyond the pattern length are dimmed rather than removed, so
// shortening a pattern does not throw away what was drawn past the end.
// Beat markers follow the time signature, and steps past the pattern length
// are dimmed rather than removed so shortening never discards work.
// One place to change the loop length, so the select, the engine and the
// dimming can never disagree.
function setSeqLength(n){
  seqLength = Math.max(1, Math.min(SEQ_STEPS, n));
  const ls = document.getElementById('seqLen');
  if (ls) ls.value = seqLength;
  if (seqPlaying){
    seqEngineStop();
    seqEngineStart(parseInt(document.getElementById('seqTempo').value, 10) || 110, seqLength);
  }
  paintLength();
  updateTransport();
}

function updateTransport(){
  const el = document.getElementById('transportInfo');
  if (!el) return;
  const bpm = document.getElementById('seqTempo');
  el.textContent = (seqPlaying ? 'RUNNING' : 'STOPPED') +
                   '  \u00b7  ' + seqLength + ' steps' +
                   '  \u00b7  ' + (bpm ? bpm.value : '?') + ' BPM';
}

function paintLength(){
  document.querySelectorAll('.step').forEach(c => {
    const abs = absStep(c.dataset.s | 0);
    c.classList.toggle('past', abs >= seqLength);
    c.classList.toggle('beat', abs % seqBeatsPerBar === 0);
  });
  document.querySelectorAll('.seqhead div').forEach((d, i) => {
    const abs = absStep(i);
    d.textContent = (abs % seqBeatsPerBar === 0) ? String(abs / seqBeatsPerBar + 1) : '';
    d.className = (abs % seqBeatsPerBar === 0) ? 'beat' : '';
    d.style.opacity = abs >= seqLength ? '0.28' : '';
  });
}

function buildNoteSequencer(){
  const host = document.getElementById('noteRows');
  if (!host) return;
  NOTE_ROW_NAMES.forEach((name, ri) => {
    const row = document.createElement('div');
    row.className = 'seqrow' + (name.indexOf('#') >= 0 ? ' sharprow' : '');
    const nm = document.createElement('div');
    nm.className = 'seqname'; nm.textContent = name;
    nm.onclick = () => previewNote(noteNoteFor(ri));
    row.appendChild(nm);
    noteCells[ri] = [];
    for (let si = 0; si < SEQ_PAGE; si++){
      const c = document.createElement('div');
      c.className = 'step note';
      c.dataset.r = ri; c.dataset.s = si;
      attachCell(c, noteGrid, noteCells, ri, si, () => previewNote(noteNoteFor(ri)));
      noteCells[ri][si] = c;
      row.appendChild(c);
    }
    host.appendChild(row);
  });

  makeOctave('noteOct', () => noteOct, v => {
    noteOct = v;
    showNoteRange();
    seqEnginePattern(seqRowsForEngine());
  }, -2, 2);
  showNoteRange();
}

function buildSequencer(){
  const head = document.getElementById('seqHead');
  const rows = document.getElementById('seqRows');
  if (!head || !rows) return;

  for (let i = 0; i < SEQ_PAGE; i++){
    const d = document.createElement('div');
    d.textContent = '';
    if (i % 4 === 0) d.className = 'beat';
    head.appendChild(d);
  }

  SEQ_ROWS.forEach((r, ri) => {
    const row = document.createElement('div');
    row.className = 'seqrow';
    const nm = document.createElement('div');
    nm.className = 'seqname';
    const pick = document.createElement('select');
    DRUM_KIT.forEach(([note, label]) => {
      const o = document.createElement('option');
      o.value = note; o.textContent = label;
      pick.appendChild(o);
    });
    pick.value = String(drumAssign[ri]);
    pick.onchange = () => {
      drumAssign[ri] = pick.value | 0;
      try { localStorage.setItem('8b8.drumAssign', JSON.stringify(drumAssign)); } catch(e){}
      playNote(drumAssign[ri], 110, 9);                // audition the new one
      seqEnginePattern(seqRowsForEngine());
    };
    nm.appendChild(pick);
    row.appendChild(nm);
    seqCells[ri] = [];
    for (let si = 0; si < SEQ_PAGE; si++){
      const c = document.createElement('div');
      c.className = 'step';
      c.dataset.r = ri; c.dataset.s = si;
      attachCell(c, seqGrid, seqCells, ri, si, () => playNote(drumAssign[ri], 110, 9));
      seqCells[ri][si] = c;
      row.appendChild(c);
    }
    rows.appendChild(row);
  });

  const sel = document.getElementById('seqPreset');
  Object.keys(SEQ_PATTERNS).forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  });
  sel.onchange = () => loadPattern(sel.value);

  const lenSel = document.getElementById('seqLen');
  if (lenSel){
    for (let n = 1; n <= SEQ_STEPS; n++){
      const o = document.createElement('option');
      o.value = n; o.textContent = n + ' step' + (n > 1 ? 's' : '');
      lenSel.appendChild(o);
    }
    lenSel.value = 16;
    lenSel.value = seqLength;
    lenSel.onchange = () => {
      setSeqLength(parseInt(lenSel.value, 10) || 16);
    };
    // Deliberately not remembered between visits: a length restored from a
    // previous session looped sixty-four steps while page one was on screen,
    // with no visible reason for it.
  }

  const tempo = document.getElementById('seqTempo');
  tempo.oninput = () => {
    document.getElementById('seqBpm').textContent = tempo.value + ' BPM';
    seqEngineTempo(parseInt(tempo.value, 10) || 110);
    updateTransport();
  };

  const pageSel = document.getElementById('seqPageSel');
  if (pageSel) pageSel.onchange = () => {
    seqPage = pageSel.value | 0;
    // Turning to a page outside the pattern used to show sixteen dead cells:
    // they accepted notes but nothing played them, and at the dimmed opacity
    // the notes were barely visible either. Reaching for a page is a clear
    // enough signal that you want those steps, so the pattern grows to cover
    // it. Shortening it again afterwards still works and keeps the notes.
    // The loop covers the pages that have actually been opened: stay on
    // 1-16 and it loops sixteen steps, reach page three and it loops
    // forty-eight. It never shrinks on its own, since going back to look at
    // an earlier page is not a request to throw the later ones out of the
    // loop -- the Length control is there for that.
    if (seqPage > maxPageOpened){
      maxPageOpened = seqPage;
      setSeqLength((maxPageOpened + 1) * SEQ_PAGE);
    }
    paintSeq();
    paintLength();
  };

  const swing = document.getElementById('seqSwing');
  const swingVal = document.getElementById('seqSwingVal');
  if (swing) swing.oninput = () => {
    const pct = parseInt(swing.value, 10) || 0;
    swingVal.textContent = 'Swing ' + pct + '%';
    seqEngineSwing(pct / 100);
  };

  const sigSel = document.getElementById('seqSig');
  if (sigSel) sigSel.onchange = () => {
    seqBeatsPerBar = parseInt(sigSel.value, 10) || 4;
    paintLength();
    try { localStorage.setItem('8b8.seqSig', seqBeatsPerBar); } catch(e){}
  };
  try {
    const sv = localStorage.getItem('8b8.seqSig');
    if (sv && sigSel){ sigSel.value = sv; seqBeatsPerBar = parseInt(sv, 10) || 4; }
  } catch(e){}

  // Tap tempo: average the gaps between the last few taps. Gaps beyond two
  // seconds start a fresh count, since that is someone coming back to it
  // rather than keeping time.
  let taps = [];
  const tapBtn = document.getElementById('seqTap');
  if (tapBtn) tapBtn.onclick = () => {
    const now = performance.now();
    if (taps.length && now - taps[taps.length - 1] > 2000) taps = [];
    taps.push(now);
    if (taps.length > 5) taps.shift();
    if (taps.length < 2){ tapBtn.textContent = 'Tap\u2026'; return; }
    let sum = 0;
    for (let i = 1; i < taps.length; i++) sum += taps[i] - taps[i - 1];
    const bpm = Math.max(50, Math.min(200, Math.round(60000 / (sum / (taps.length - 1)))));
    const t = document.getElementById('seqTempo');
    t.value = bpm;
    document.getElementById('seqBpm').textContent = bpm + ' BPM';
    seqEngineTempo(bpm);
    tapBtn.textContent = String(bpm);
  };

  const copySel = document.getElementById('seqCopyTo');
  const copyBtn = document.getElementById('seqCopyBtn');
  if (copyBtn) copyBtn.onclick = () => {
    copyPageTo(copySel.value | 0);
    copyBtn.textContent = 'Copied';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 900);
  };

  document.getElementById('seqPlay').onclick = toggleSeq;
  document.getElementById('seqClear').onclick = () => loadPattern('Empty');   // this page

  buildNoteSequencer();
  buildMidiFile();
  loadPattern('Four/Four');
  document.getElementById('seqPreset').value = 'Four/Four';
  paintLength();
  updateTransport();
  requestAnimationFrame(paintPlayhead);
}

// A note is drawn as a head plus a tail across the steps it covers, so its
// length is visible at a glance rather than implied.
function paintGrid(cells, grid){
  for (let r = 0; r < cells.length; r++){
    const covered = new Array(SEQ_PAGE).fill(0);   // 1 = head, 2 = tail
    for (let s = 0; s < SEQ_STEPS; s++){
      const len = grid[r][s];
      if (!len) continue;
      for (let k = 0; k < len; k++){
        const abs = s + k;
        if (abs >= SEQ_STEPS) break;
        const col = abs - seqPage * SEQ_PAGE;
        if (col >= 0 && col < SEQ_PAGE) covered[col] = k === 0 ? 1 : 2;
      }
    }
    for (let c = 0; c < SEQ_PAGE; c++){
      const el = cells[r][c];
      if (!el) continue;
      el.classList.toggle('on',   covered[c] === 1);
      el.classList.toggle('tail', covered[c] === 2);
    }
  }
}

function paintSeq(){
  paintGrid(seqCells, seqGrid);
  paintGrid(noteCells, noteGrid);
}

function absStep(col){ return seqPage * SEQ_PAGE + col; }

// Press an empty cell to place a note and start dragging its length; press a
// note's head to remove it. Dragging right past the next cells stretches it.
function attachCell(el, grid, cells, ri, col, onPreview){
  el.addEventListener('pointerdown', e => {
    e.preventDefault();
    const s = absStep(col);
    if (grid[ri][s]){
      grid[ri][s] = 0;
      stretch = null;
    } else {
      // Clicking inside an existing note's tail trims that note instead.
      let owner = -1;
      for (let k = 1; k < 16 && s - k >= 0; k++){
        if (grid[ri][s - k] > k){ owner = s - k; break; }
      }
      if (owner >= 0){
        grid[ri][owner] = s - owner;      // shorten it to end here
      } else {
        grid[ri][s] = 1;
        stretch = { grid, ri, start: s };
        if (onPreview) onPreview(ri);
        try { el.setPointerCapture(e.pointerId); } catch(err){}
      }
    }
    paintSeq();
    seqEnginePattern(seqRowsForEngine());
  });

  el.addEventListener('pointermove', e => {
    if (!stretch || stretch.grid !== grid || stretch.ri !== ri) return;
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const hit = under && under.closest ? under.closest('.step') : null;
    if (!hit || hit.dataset.r === undefined) return;
    const s = absStep(hit.dataset.s | 0);
    const len = Math.max(1, Math.min(SEQ_STEPS - stretch.start, s - stretch.start + 1));
    if (grid[stretch.ri][stretch.start] === len) return;
    grid[stretch.ri][stretch.start] = len;
    paintSeq();
    seqEnginePattern(seqRowsForEngine());
  });

  const end = () => { stretch = null; };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}

// The playhead is read from the engine rather than set by it, so the
// display can lag a frame without the timing itself being affected.
function paintPlayhead(){
  const step = seqPlaying ? seqEngineStep() : -1;
  if (step !== seqLastPainted){
    const mark = (abs, on) => {
      const col = abs - seqPage * SEQ_PAGE;
      if (col < 0 || col >= SEQ_PAGE) return;
      for (let r = 0; r < seqCells.length; r++) seqCells[r][col].classList.toggle('playing', on);
      for (let r = 0; r < noteCells.length; r++) noteCells[r][col].classList.toggle('playing', on);
    };
    if (seqLastPainted >= 0) mark(seqLastPainted, false);
    if (step >= 0) mark(step, true);
    seqLastPainted = step;
  }
  requestAnimationFrame(paintPlayhead);
}

// Fills the CURRENT PAGE only, so each page can carry a different kit
// pattern. Mutates the existing arrays rather than replacing them: the cell
// handlers captured these references when they were built, and swapping the
// array out left every drum click writing to an orphaned copy while the
// display read the new one.
function loadPattern(name){
  const p = SEQ_PATTERNS[name] || {};

  // A pattern may repoint rows onto the drums it needs, since the eight rows
  // are a working set rather than a fixed kit.
  if (p.assign){
    SEQ_ROWS.forEach((r, ri) => {
      if (p.assign[r.name] !== undefined) drumAssign[ri] = p.assign[r.name];
    });
    document.querySelectorAll('#seqRows .seqname select').forEach((sel, ri) => {
      sel.value = String(drumAssign[ri]);
    });
    try { localStorage.setItem('8b8.drumAssign', JSON.stringify(drumAssign)); } catch(e){}
  }

  // Patterns reaching past step 15 are written from the start rather than
  // into the page on screen, and the loop grows to hold them: a clave landing
  // half on one page and half on another would be neither.
  let last = 0;
  SEQ_ROWS.forEach(r => (p[r.name] || []).forEach(i => { if (i > last) last = i; }));
  const spans = last >= SEQ_PAGE;
  const base = spans ? 0 : seqPage * SEQ_PAGE;
  const width = spans ? SEQ_STEPS : SEQ_PAGE;

  SEQ_ROWS.forEach((r, ri) => {
    // Patterns name the default row, not the drum, so a preset still lands
    // in the right place after a row has been repointed.
    const on = p[r.name] || [];
    for (let s = 0; s < width && base + s < SEQ_STEPS; s++) seqGrid[ri][base + s] = 0;
    on.forEach(i => { if (base + i < SEQ_STEPS) seqGrid[ri][base + i] = 1; });
  });

  if (spans){
    const pages = Math.floor(last / SEQ_PAGE) + 1;
    if (maxPageOpened < pages - 1) maxPageOpened = pages - 1;
    if (seqLength < pages * SEQ_PAGE) setSeqLength(pages * SEQ_PAGE);
  }
  paintSeq();
  paintLength();
  seqEnginePattern(seqRowsForEngine());
}

// Copies everything on the page currently shown -- drums and notes -- onto
// another page, and grows the pattern to include it.
function copyPageTo(target){
  if (target === seqPage) return;
  const from = seqPage * SEQ_PAGE, to = target * SEQ_PAGE;
  [[seqGrid, SEQ_ROWS.length], [noteGrid, NOTE_SEMIS.length]].forEach(([grid, rows]) => {
    for (let r = 0; r < rows; r++)
      for (let s = 0; s < SEQ_PAGE; s++) grid[r][to + s] = grid[r][from + s];
  });
  if (target > maxPageOpened) maxPageOpened = target;
  if (seqLength < (target + 1) * SEQ_PAGE) setSeqLength((target + 1) * SEQ_PAGE);
  paintSeq();
  paintLength();
  seqEnginePattern(seqRowsForEngine());
}

function toggleSeq(){
  const btn = document.getElementById('seqPlay');
  const bpm = parseInt(document.getElementById('seqTempo').value, 10) || 110;
  if (seqPlaying){
    seqEngineStop();
    seqPlaying = false; btn.textContent = 'Play';
    btn.classList.remove('running');
  } else {
    seqEnginePattern(seqRowsForEngine());
    seqEngineStart(bpm, seqLength);
    seqPlaying = true; btn.textContent = 'Stop';
    btn.classList.add('running');
  }
  updateTransport();
}

/* ==== Gamepad =========================================================== */
// Standard-mapping layout, which is what an 8BitDo reports over Bluetooth:
//   0 A  1 B  2 X  3 Y   4 L1  5 R1  6 L2  7 R2
//   8 Select  9 Start   10 L3  11 R3
//   12 Up  13 Down  14 Left  15 Right   16 Home
// A pad that reports something else still works -- the live readout below
// names whichever index you press, so any controller can be figured out.
const GP = { A:0, B:1, X:2, Y:3, L1:4, R1:5, L2:6, R2:7,
             SELECT:8, START:9, UP:12, DOWN:13, LEFT:14, RIGHT:15 };

const GP_MODES = ['Chords', 'Drums', 'Params'];
let gpMode = 0;
let gpPrev = [];              // previous button states, for edge detection
let gpRootIdx = 1;            // start on C
let gpLastStrumSeg = -1;
let gpParamSlots = [null, null, null, null];   // LX, LY, RX, RY
let gpLastSent = [null, null, null, null];
let gpConnected = false;

const GP_DRUMS = [
  [GP.A, 36, 'Kick'], [GP.B, 38, 'Snare'], [GP.X, 42, 'Hat'], [GP.Y, 46, 'OpnHat'],
  [GP.UP, 50, 'HiTom'], [GP.DOWN, 41, 'LoTom'], [GP.LEFT, 37, 'Rim'], [GP.RIGHT, 49, 'Crash'],
  [GP.L1, 56, 'Cowbell'], [GP.R1, 39, 'Clap']
];

function buildGamepad(){
  const modes = document.getElementById('gpModes');
  const assign = document.getElementById('gpAssign');
  if (!modes || !assign) return;

  GP_MODES.forEach((m, i) => {
    const b = document.createElement('div');
    b.className = 'gp-mode' + (i === gpMode ? ' active' : '');
    b.textContent = m;
    b.onclick = () => setGpMode(i);
    modes.appendChild(b);
  });

  // Four stick axes, each assignable to any parameter.
  [['Left X', 0], ['Left Y', 1], ['Right X', 2], ['Right Y', 3]].forEach(([label, slot]) => {
    const row = document.createElement('div');
    row.className = 'gp-assign';
    const sp = document.createElement('span'); sp.textContent = label;
    const sel = document.createElement('select');
    const none = document.createElement('option');
    none.value = ''; none.textContent = '(none)';
    sel.appendChild(none);
    PARAMS.forEach(p => {
      const o = document.createElement('option');
      o.value = p.index; o.textContent = p.group + ' - ' + p.label;
      sel.appendChild(o);
    });
    sel.onchange = () => {
      gpParamSlots[slot] = sel.value === '' ? null : (sel.value | 0);
      try { localStorage.setItem('8b8.gp' + slot, sel.value); } catch(e){}
    };
    try {
      const saved = localStorage.getItem('8b8.gp' + slot);
      if (saved !== null){ sel.value = saved; sel.onchange(); }
    } catch(e){}
    row.appendChild(sp); row.appendChild(sel);
    assign.appendChild(row);
  });

  requestAnimationFrame(gpPoll);
}

function setGpMode(i){
  gpMode = (i + GP_MODES.length) % GP_MODES.length;
  document.querySelectorAll('.gp-mode').forEach((b, n) => b.classList.toggle('active', n === gpMode));
}

function gpPoll(){
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  let pad = null;
  for (const p of pads) if (p && p.connected) { pad = p; break; }

  if (!pad){
    if (gpConnected){
      gpConnected = false;
      document.getElementById('gpLive').textContent = 'Controller disconnected.';
    }
    gpPrev = [];
    requestAnimationFrame(gpPoll);
    return;
  }
  if (!gpConnected){
    gpConnected = true;
    log('sys', 'Gamepad: ' + pad.id + (pad.mapping === 'standard' ? '' : ' (non-standard mapping)'));
  }

  const btn = pad.buttons.map(b => b.pressed);
  const hit = i => btn[i] && !gpPrev[i];        // rising edge only

  // Select cycles the mode wherever you are.
  if (hit(GP.SELECT)) setGpMode(gpMode + 1);

  if (gpMode === 0) gpChords(btn, hit);
  else if (gpMode === 1) gpDrums(hit);
  else gpParams(pad);

  gpStrum(pad);                                  // right stick strums in every mode
  if (gpMode !== 2) gpParamsFromSticks(pad, true);

  const down = btn.map((v, i) => v ? i : -1).filter(i => i >= 0);
  document.getElementById('gpLive').innerHTML =
    '<b>' + GP_MODES[gpMode] + '</b> &nbsp; ' + pad.id.slice(0, 40) +
    '<br>buttons down: ' + (down.length ? down.join(', ') : 'none') +
    '<br>sticks: ' + pad.axes.slice(0, 4).map(a => a.toFixed(2)).join('  ');

  gpPrev = btn;
  requestAnimationFrame(gpPoll);
}

function gpChords(btn, hit){
  // Shoulders and left/right walk the seven roots; A, B and X are the three
  // rows and STACK exactly as they do on the matrix, so A+X is a major
  // seventh, B+X a minor seventh, A+B diminished and all three augmented.
  if (hit(GP.LEFT)  || hit(GP.L1)) gpRootIdx = (gpRootIdx + ROOTS.length - 1) % ROOTS.length;
  if (hit(GP.RIGHT) || hit(GP.R1)) gpRootIdx = (gpRootIdx + 1) % ROOTS.length;
  if (hit(GP.Y)){
    sharpOn = !sharpOn;
    const sw = document.getElementById('sharpSw');
    if (sw) sw.classList.toggle('active', sharpOn);
    revoice();
  }
  if (hit(GP.R2)){
    barryOn = !barryOn;
    const sw = document.getElementById('barrySw');
    if (sw) sw.classList.toggle('active', barryOn);
    revoice();
  }

  // Press adds a row and re-forms the chord; release only drops the row, so
  // letting go of one button of a stack never sounds the intermediate chord.
  const rows = [[GP.A, 0], [GP.B, 1], [GP.X, 2]];
  rows.forEach(([b, row]) => { if (hit(b)) pressRow(gpRootIdx, row); });
  rows.forEach(([b, row]) => { if (!btn[b] && gpPrev[b]) releaseRow(row); });

  if (hit(GP.START)){ heldRows.clear(); soundingKey = null; releaseChord(); strumNotes = []; paintChords(); }
}

function gpDrums(hit){
  GP_DRUMS.forEach(([b, note]) => { if (hit(b)) playNote(note, 110, 9); });
}

// Right stick vertical walks the strum ladder, so a flick sweeps the chord.
function gpStrum(pad){
  // Follow the on-screen orientation: push up on a vertical harp, right on a
  // horizontal one.
  const y = (strumHorizontal ? (pad.axes[2] || 0) : (pad.axes[3] || 0));
  if (Math.abs(y) < 0.12){ gpLastStrumSeg = -1; return; }
  if (!strumNotes.length) return;
  const t = strumHorizontal ? (y + 1) / 2 : 1 - (y + 1) / 2;
  const seg = Math.max(0, Math.min(strumCount() - 1, Math.floor(t * strumCount())));
  if (seg === gpLastStrumSeg) return;
  gpLastStrumSeg = seg;
  const n = strumNotes[seg];
  playNote(n, 100, 0);
  setTimeout(() => stopNote(n, 0), 400);
}

function gpParams(pad){ gpParamsFromSticks(pad, false); }

// Sticks drive assigned parameters. Only sent when the mapped value actually
// changes, so a resting stick is silent rather than flooding the link.
function gpParamsFromSticks(pad, skipRightY){
  for (let slot = 0; slot < 4; slot++){
    const idx = gpParamSlots[slot];
    if (idx === null) continue;
    if (skipRightY && slot === 3) continue;      // right Y is strumming
    const raw = pad.axes[slot] || 0;
    if (Math.abs(raw) < 0.12) continue;          // deadzone: leave it alone
    const p = PARAMS[idx];
    const t = (slot % 2 === 1) ? (1 - (raw + 1) / 2) : ((raw + 1) / 2);  // Y up = more
    const v = Math.round(p.min + t * (p.max - p.min));
    if (v === gpLastSent[slot]) continue;
    gpLastSent[slot] = v;
    setValue(idx, v, false);
  }
}

window.addEventListener('gamepadconnected', e => {
  log('sys', 'Gamepad connected: ' + e.gamepad.id);
});

/* ==== Section nav ======================================================= */
function buildNav(){
  const nav = document.getElementById('nav');
  const bands = [...document.querySelectorAll('.section')];
  const names = bands.map(b => b.querySelector('.section-head h2').textContent);

  function show(which){
    bands.forEach((b, i) => b.classList.toggle('hidden', which !== 'All' && names[i] !== which));
    [...nav.children].forEach(btn => btn.classList.toggle('active', btn.textContent === which));
    try { localStorage.setItem('8b8.section', which); } catch(e){}
  }
  for (const label of names.concat(['All'])){
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => show(label);
    nav.appendChild(b);
  }
  let want = names[0];
  try { want = localStorage.getItem('8b8.section') || want; } catch(e){}
  if (want !== 'All' && names.indexOf(want) < 0) want = names[0];
  show(want);
}

/* ==== MIDI learn ======================================================== */
const ccAssign = new Array(NUM_PARAMS).fill(255);

function renderCc(){
  document.querySelectorAll('.ctl .cc').forEach(el => {
    const cc = ccAssign[el.dataset.idx | 0];
    el.textContent = (cc === 255 || cc === undefined || isNaN(cc)) ? '' : ('CC' + cc);
  });
}

const learnBtn = document.getElementById('learnBtn');
learnBtn.onclick = () => {
  const on = document.body.classList.toggle('learning');
  learnBtn.style.color = on ? 'var(--accent-lit)' : '';
  if (on) log('sys','MIDI learn on: click a control name, then move a knob on your controller.');
  else document.querySelectorAll('.ctl .name.armed').forEach(e => e.classList.remove('armed'));
};

/* ==== Appearance ======================================================== */
// Theme and text size are per-viewer conveniences, so they live in
// localStorage rather than on the unit. Wrapped because storage can be
// unavailable, in which case the defaults simply apply.
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('8b8.theme', t); } catch(e){}
}
function applyScale(v){
  document.documentElement.style.setProperty('--ui-scale', v);
  try { localStorage.setItem('8b8.scale', v); } catch(e){}
}
const settingsBtn = document.getElementById('settingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
settingsBtn.onclick = () => {
  settingsPanel.hidden = !settingsPanel.hidden;
  settingsBtn.style.color = settingsPanel.hidden ? '' : 'var(--accent-lit)';
};

function applyDetail(v){
  document.body.classList.toggle('compact', v === 'compact');
  try { localStorage.setItem('8b8.detail', v); } catch(e){}
}

const themeSel = document.getElementById('themeSelect');
const sizeSel   = document.getElementById('sizeSelect');
const detailSel = document.getElementById('detailSelect');
themeSel.onchange  = () => applyTheme(themeSel.value);
sizeSel.onchange   = () => applyScale(sizeSel.value);
detailSel.onchange = () => applyDetail(detailSel.value);

(function restoreAppearance(){
  let t = 'nes', v = '1', d = 'full';
  try {
    t = localStorage.getItem('8b8.theme') || t;
    v = localStorage.getItem('8b8.scale') || v;
    d = localStorage.getItem('8b8.detail') || d;
  } catch(e){}
  themeSel.value = t; sizeSel.value = v; detailSel.value = d;
  applyTheme(t); applyScale(v); applyDetail(d);
})();

/* ==== Boot ============================================================== */
buildUI();
buildPlaySurface();
buildNav();
renderAll();
renderCc();
</script>
</body>
</html>
"""



def emit_temperaments(path):
    """Emits the temperament tables.

    The cent offsets come straight from temperaments.py, which is the same
    file used by the minichord firmware -- one source of truth for both, so
    the tunings can't drift apart. Nothing here re-derives the maths.

    The firmware turns an offset into a tone divisor with an integer
    multiply, so a factor table replaces any runtime pow():
        tempered = (equal * factor + 16384) >> 15,  factor = 32768 * 2^(-c/1200)
    """
    import math
    from temperaments import PROFILES, offsets

    rows = [offsets(p) for p in PROFILES]

    # Root rotation re-anchors on the root, so the offset range widens.
    span = 0
    for r in rows:
        for root in range(12):
            rot = [r[(i + root) % 12] for i in range(12)]
            span = max(span, max(abs(v - rot[0]) for v in rot))
    span += 2                                   # headroom for future entries

    lines = []
    a = lines.append
    a("// AUTO-GENERATED by generate.py -- do not edit by hand.")
    a("// Cent offsets are imported from temperaments.py, the same definitions")
    a("// used by the minichord firmware.")
    a("#ifndef TEMPERAMENTS_H")
    a("#define TEMPERAMENTS_H")
    a("")
    a("#include <avr/pgmspace.h>")
    a("")
    a(f"#define NUM_TEMPERAMENTS {len(rows)}")
    a(f"#define TEMPER_CENT_SPAN {span}   // table covers -SPAN..+SPAN cents")
    a("")
    a("// Cents from equal temperament, pitch classes C..B. A is 0 in every row.")
    a("static const int8_t temperCents[NUM_TEMPERAMENTS][12] PROGMEM = {")
    for p, r in zip(PROFILES, rows):
        a("  {" + ", ".join(f"{c:3d}" for c in r) + f" }},   // {p['name']}")
    a("};")
    a("")
    a("// 32768 * 2^(-cents/1200), indexed by cents + TEMPER_CENT_SPAN.")
    a("// A tone period is a DIVISOR, so a sharper note needs a SMALLER one.")
    a(f"static const uint16_t temperFactor[{2*span+1}] PROGMEM = {{")
    vals = [str(int(round(32768 * (2 ** (-c / 1200.0)))))
            for c in range(-span, span + 1)]
    for i in range(0, len(vals), 10):
        a("  " + ", ".join(vals[i:i+10]) + ",")
    a("};")
    a("")
    a("#endif // TEMPERAMENTS_H")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {path}  ({len(rows)} temperaments, +/-{span} cents)")


SERIAL_TRANSPORT_JS = "/* ==== Web Serial ======================================================== */\nlet port = null, reader = null, writer = null, inBuf = '';\nconst statusPill = document.getElementById('statusPill');\nconst statusText = document.getElementById('statusText');\nconst connectBtn = document.getElementById('connectBtn');\nconst disconnectBtn = document.getElementById('disconnectBtn');\n\nfunction setStatus(mode, text){\n  statusPill.className = 'status-pill' + (mode ? ' ' + mode : '');\n  statusText.textContent = text;\n}\n\nasync function connect(){\n  try{\n    port = await navigator.serial.requestPort();\n    await port.open({ baudRate: 115200 });\n    const dec = new TextDecoderStream();\n    port.readable.pipeTo(dec.writable).catch(()=>{});\n    reader = dec.readable.getReader();\n    const enc = new TextEncoderStream();\n    enc.readable.pipeTo(port.writable).catch(()=>{});\n    writer = enc.writable.getWriter();\n    connectBtn.disabled = true; disconnectBtn.disabled = false;\n    setStatus('on','Connected');\n    log('sys','Connected.');\n    readLoop();\n    sawLayout = false;\n    send('DUMP');\n    // Firmware older than the handshake answers DUMP with PRESET: but no\n    // LAYOUT: line at all, which is itself a mismatch worth reporting.\n    setTimeout(() => {\n      if (!sawLayout && port){\n        document.getElementById('mismatchDetail').textContent =\n          'The unit did not report a parameter layout at all, so it predates ' +\n          'this panel. This panel expects layout 0x' +\n          LAYOUT_VERSION.toString(16).toUpperCase() + ' with ' + NUM_PARAMS + ' parameters.';\n        document.getElementById('mismatch').style.display = 'block';\n        log('err', 'No LAYOUT reply \\u2014 flashed firmware is out of date.');\n      }\n    }, 1500);\n  }catch(err){\n    setStatus('err','Connect failed');\n    log('err','Connect failed: ' + err.message);\n  }\n}\n\nasync function disconnect(){\n  try{ await reader?.cancel(); }catch(e){}\n  try{ await writer?.close(); }catch(e){}\n  try{ await port?.close(); }catch(e){}\n  reader = writer = port = null;\n  connectBtn.disabled = false; disconnectBtn.disabled = true;\n  setStatus('','Disconnected');\n  log('sys','Disconnected.');\n}\n\nasync function readLoop(){\n  try{\n    while(true){\n      const { value, done } = await reader.read();\n      if (done) break;\n      inBuf += value;\n      let idx;\n      while ((idx = inBuf.indexOf('\\n')) >= 0){\n        const line = inBuf.slice(0, idx).replace('\\r','');\n        inBuf = inBuf.slice(idx + 1);\n        if (line.length) handleLine(line);\n      }\n    }\n  }catch(err){\n    log('err','Read error: ' + err.message);\n  }finally{\n    if (port) disconnect();\n  }\n}\n\nfunction playNote(note, vel, chan){ send('NON:' + (chan||0) + ':' + note + ':' + (vel||100)); }\nfunction stopNote(note, chan){ send('NOF:' + (chan||0) + ':' + note); }\n\nfunction send(cmd){\n  log('tx','\\u00bb ' + cmd);\n  if (writer) writer.write(cmd + '\\n').catch(err => log('err','Write failed: ' + err.message));\n}\n\n\n\nconnectBtn.onclick = connect;\ndisconnectBtn.onclick = disconnect;\nif (!('serial' in navigator)){\n  document.getElementById('unsupported').style.display = 'block';\n  connectBtn.disabled = true;\n}\n\n\n/* ---- sequencer engine: timer driven ---- */\n// Notes reach the unit over a serial link, so there is nothing to be\n// sample-accurate against. A drift-corrected timer is the best available:\n// each tick is scheduled from when it SHOULD have fired, not from now.\nlet seqRowsCache = [], seqHeld = [], seqTimer = null, seqNextAt = 0, seqPos = -1, seqBpm = 110, seqLen = 16;\n\nfunction seqEnginePattern(rows){\n  // Expand the sparse cells into a per-step lookup for the tick loop.\n  seqRowsCache = rows.map(r => {\n    const lens = [];\n    for (const [step, len] of r.cells) lens[step] = len;\n    return { note: r.note, chan: r.chan, lens };\n  });\n}\nlet seqSwing = 0;\nfunction seqEngineSwing(amount){ seqSwing = amount; }\nfunction seqEngineTempo(bpm){ seqBpm = bpm; }\nfunction seqEngineStep(){ return seqPos; }\nfunction seqEngineStop(){\n  if (seqTimer) clearTimeout(seqTimer);\n  seqTimer = null; seqPos = -1;\n  seqHeld.forEach(h => stopNote(h.note, 0));\n  seqHeld.length = 0;\n}\nfunction seqEngineStart(bpm, steps){\n  seqBpm = bpm; seqLen = steps; seqPos = 0;\n  seqNextAt = performance.now();\n  const tick = () => {\n    // Release melodic notes from the previous step before striking the next,\n    // or a pitch row would hold its first note forever. Drums are one-shots.\n    for (let i = seqHeld.length - 1; i >= 0; i--){\n      if (--seqHeld[i].left <= 0){\n        stopNote(seqHeld[i].note, 0);\n        seqHeld.splice(i, 1);\n      }\n    }\n    seqRowsCache.forEach(r => {\n      const len = r.lens ? (r.lens[seqPos] || 0) : 0;\n      if (!len) return;\n      const ch = (r.chan === undefined) ? 9 : r.chan;\n      playNote(r.note, 110, ch);\n      // Notes reach the unit down a wire, so the length is counted in ticks\n      // here rather than scheduled against an audio clock.\n      if (ch !== 9) seqHeld.push({ note: r.note, left: len });\n    });\n    seqPos = (seqPos + 1) % seqLen;\n    // A swung pair is one long step and one short; the pair still totals two\n    // steps, so the tempo is unchanged.\n    const base = 60000 / seqBpm / 4;\n    seqNextAt += base * ((seqPos & 1) ? (1 + seqSwing) : (1 - seqSwing));\n    seqTimer = setTimeout(tick, Math.max(0, seqNextAt - performance.now()));\n  };\n  tick();\n}\n\n/* ---- MIDI file transport: timer driven ---- */\n// Notes reach the unit over a wire, so there is no audio clock to time\n// against; the player keeps its own timer for this transport.\nlet midiSerialEvents = null;\nfunction midiEngineLoad(events){ midiSerialEvents = events; return false; }\nfunction midiEngineStart(){}\nfunction midiEngineStop(){}\nfunction midiEngineSpeed(){}\nfunction midiEngineLoop(){}\nfunction midiEnginePos(){ return -1; }\n"

SERIAL_TRANSPORT_UI = '        <button class="btn" id="connectBtn">Connect</button>\n        <button class="btn danger" id="disconnectBtn" disabled>Disconnect</button>'

WASM_TRANSPORT_UI = '        <button class="btn" id="startBtn">Start Audio</button>\n        <button class="btn danger" id="stopBtn" disabled>Stop</button>'

WASM_TRANSPORT_JS = '/* ==== Emulated transport ================================================ */\n// The firmware itself, compiled to WebAssembly, driving three emulated\n// AY-3-8910s into Web Audio. The panel above is byte-identical to the one\n// that talks to real hardware over serial -- the only thing that changes is\n// what send() writes to. Same firmware, same parameters, same protocol.\n\nlet audioCtx = null, node = null, ready = false, pollTimer = null;\nconst HEAP_SAMPLES = 2048;\nlet heapPtr = 0;\n\nconst statusPill = document.getElementById(\'statusPill\');\nconst statusText = document.getElementById(\'statusText\');\nconst startBtn = document.getElementById(\'startBtn\');\nconst stopBtn  = document.getElementById(\'stopBtn\');\n\nfunction setStatus(mode, text){\n  statusPill.className = \'status-pill\' + (mode ? \' \' + mode : \'\');\n  statusText.textContent = text;\n}\n\nfunction send(cmd){\n  log(\'tx\',\'\\u00bb \' + cmd);\n  if (ready) Module.ccall(\'emu_send_line\', null, [\'string\'], [cmd]);\n}\n\nfunction pollReplies(){\n  if (!ready) return;\n  const s = Module.ccall(\'emu_read_lines\', \'string\', [], []);\n  if (!s) return;\n  for (const line of s.split(\'\\n\')) if (line.length) handleLine(line);\n}\n\n// iOS routes Web Audio through the "ambient" session, which the hardware\n// silent switch mutes. Playing a real media element promotes the session to\n// "playback", which ignores that switch. It has to start inside the same\n// user gesture, so it goes first.\nlet iosUnmute = null;\nfunction promoteAudioSession(){\n  try {\n    if (!iosUnmute){\n      iosUnmute = new Audio(\'data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==\');\n      iosUnmute.loop = true;\n      iosUnmute.setAttribute(\'playsinline\', \'\');\n      iosUnmute.volume = 0.001;\n    }\n    const p = iosUnmute.play();\n    if (p && p.catch) p.catch(() => {});\n  } catch(e){}\n}\n\nlet sawAudioCallback = false;\n\nfunction startAudio(){\n  if (!window.Module || !Module.ccall){\n    log(\'err\',\'The emulator core has not loaded. Did you run build-wasm.sh?\');\n    setStatus(\'err\',\'No core\');\n    return;\n  }\n  promoteAudioSession();\n\n  audioCtx = new (window.AudioContext || window.webkitAudioContext)();\n\n  // Everything is built synchronously inside the gesture. Awaiting resume()\n  // first and creating the nodes afterwards leaves the gesture behind, which\n  // Safari treats as a non-user-initiated start.\n  Module.ccall(\'emu_init\', null, [\'number\'], [audioCtx.sampleRate]);\n  heapPtr = Module._malloc(HEAP_SAMPLES * 4);\n  ready = true;\n\n  // ONE input channel, not zero. Safari never fires onaudioprocess for a\n  // ScriptProcessorNode declared with no inputs, so on iPhone and iPad the\n  // node connects, the context runs, and nothing is ever rendered -- silence\n  // with no error anywhere.\n  node = audioCtx.createScriptProcessor(HEAP_SAMPLES, 1, 1);\n  node.onaudioprocess = (e) => {\n    sawAudioCallback = true;\n    const out = e.outputBuffer.getChannelData(0);\n    Module.ccall(\'emu_render\', null, [\'number\',\'number\'], [heapPtr, out.length]);\n    out.set(Module.HEAPF32.subarray(heapPtr >> 2, (heapPtr >> 2) + out.length));\n  };\n  node.connect(audioCtx.destination);\n\n  const r = audioCtx.resume();\n  if (r && r.catch) r.catch(() => {});\n\n  // Say something useful if the callback never runs, rather than sitting\n  // there looking connected.\n  sawAudioCallback = false;\n  setTimeout(() => {\n    if (sawAudioCallback) return;\n    log(\'err\', \'Audio is connected but nothing is being rendered. \'\n             + \'state=\' + (audioCtx ? audioCtx.state : \'?\')\n             + \', rate=\' + (audioCtx ? audioCtx.sampleRate : \'?\'));\n    log(\'err\', \'On iPhone or iPad, check the side switch is not set to silent.\');\n  }, 1200);\n\n  startBtn.disabled = true; stopBtn.disabled = false;\n  setStatus(\'on\',\'Running\');\n  log(\'sys\',\'Emulator running at \' + audioCtx.sampleRate + \'Hz.\');\n\n  pollTimer = setInterval(pollReplies, 60);\n  send(\'DUMP\');\n}\n\nfunction stopAudio(){\n  if (iosUnmute) { try { iosUnmute.pause(); } catch(e){} }\n  if (node) { node.disconnect(); node = null; }\n  if (audioCtx) { audioCtx.close(); audioCtx = null; }\n  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }\n  ready = false;\n  startBtn.disabled = false; stopBtn.disabled = true;\n  setStatus(\'\',\'Stopped\');\n}\n\nstartBtn.onclick = startAudio;\nstopBtn.onclick  = stopAudio;\n\n// iOS suspends the context when the page goes to the background, and coming\n// back does not resume it on its own -- the panel looks alive and plays\n// nothing. Nudge it whenever the page is shown again or is next touched.\nfunction nudgeAudio(){\n  if (!audioCtx || audioCtx.state !== \'suspended\') return;\n  const r = audioCtx.resume();\n  if (r && r.catch) r.catch(() => {});\n  promoteAudioSession();\n}\ndocument.addEventListener(\'visibilitychange\', () => { if (!document.hidden) nudgeAudio(); });\nwindow.addEventListener(\'pointerdown\', nudgeAudio, true);\n\n/* ---- playing it ---- */\nfunction playNote(note, vel, chan){\n  if (ready) Module.ccall(\'emu_note_on\', null, [\'number\',\'number\',\'number\'], [chan||0, note, vel||100]);\n}\nfunction stopNote(note, chan){\n  if (ready) Module.ccall(\'emu_note_off\', null, [\'number\',\'number\'], [chan||0, note]);\n}\n\n// Real MIDI hardware, if the browser offers it. Chrome, Edge, Opera and\n// Firefox 108+ have the Web MIDI API; Safari and iOS do not. It also needs a\n// secure context, so https or localhost.\nlet midiAccess = null;\n\nfunction attachMidiInput(inp){\n  inp.onmidimessage = m => {\n    const st = m.data[0], d1 = m.data[1], d2 = m.data[2];\n    const ch = st & 0x0F, cmd = st & 0xF0;\n    if (cmd === 0x90 && d2 > 0) playNote(d1, d2, ch);\n    else if (cmd === 0x80 || (cmd === 0x90 && d2 === 0)) stopNote(d1, ch);\n    else if (cmd === 0xB0 && ready){\n      // Control changes go to the firmware exactly as they would on the\n      // hardware, so the CC map and MIDI Learn behave identically here.\n      Module.ccall(\'emu_cc\', null, [\'number\',\'number\',\'number\'], [ch, d1, d2]);\n    }\n  };\n}\n\nfunction refreshMidiInputs(){\n  if (!midiAccess) return [];\n  const names = [];\n  for (const inp of midiAccess.inputs.values()){\n    attachMidiInput(inp);\n    names.push(inp.name || \'unnamed\');\n  }\n  const el = document.getElementById(\'midiStatus\');\n  if (el) el.textContent = names.length ? names[0].slice(0, 18) : \'none\';\n  return names;\n}\n\nif (navigator.requestMIDIAccess){\n  navigator.requestMIDIAccess().then(a => {\n    midiAccess = a;\n    // Controllers are routinely plugged in after the page is open, and\n    // without this they would simply never be heard from.\n    a.onstatechange = () => {\n      const n = refreshMidiInputs();\n      log(\'sys\', \'MIDI devices: \' + (n.length ? n.join(\', \') : \'none\'));\n    };\n    const n = refreshMidiInputs();\n    log(\'sys\', n.length ? (\'MIDI in: \' + n.join(\', \'))\n                        : \'MIDI ready \\u2014 no device found yet. Plug one in.\');\n  }).catch(e => log(\'err\', \'MIDI unavailable: \' + e.message));\n} else {\n  log(\'sys\', \'This browser has no Web MIDI. Chrome, Edge or Firefox 108+ do.\');\n}\n\n/* ---- sequencer engine: timed inside the audio render ---- */\n// Sample-counted in C++ rather than by setTimeout, because on this page the\n// audio callback runs on the main thread and any timer shares it. Measured\n// drift is about one sample over four seconds.\nfunction seqEnginePattern(rows){\n  if (!ready) return;\n  Module.ccall(\'emu_seq_clear\', null, [], []);\n  rows.forEach((r, i) => {\n    Module.ccall(\'emu_seq_row\', null, [\'number\',\'number\',\'number\'], [i, r.note, r.chan]);\n    // Sparse: only the filled cells cross the boundary.\n    for (const [step, len] of r.cells){\n      Module.ccall(\'emu_seq_cell\', null, [\'number\',\'number\',\'number\'], [i, step, len]);\n    }\n  });\n}\nfunction seqEngineStart(bpm, steps){\n  if (!ready) return;\n  Module.ccall(\'emu_seq_start\', null, [\'number\',\'number\'], [bpm, steps]);\n}\nfunction seqEngineSwing(amount){\n  if (ready) Module.ccall(\'emu_seq_swing\', null, [\'number\'], [amount]);\n}\nfunction seqEngineTempo(bpm){\n  if (ready) Module.ccall(\'emu_seq_tempo\', null, [\'number\'], [bpm]);\n}\nfunction seqEngineStop(){\n  if (ready) Module.ccall(\'emu_seq_stop\', null, [], []);\n}\nfunction seqEngineStep(){\n  return ready ? Module.ccall(\'emu_seq_step\', \'number\', [], []) : -1;\n}\n\n/* ---- MIDI file transport: timed in the audio render ---- */\n// The player hands the whole file to the engine once, then only starts and\n// stops it. Firing events from a JS timer put them behind each buffer render,\n// which on a dense file arrived as stutter.\nfunction midiEngineLoad(events){\n  if (!ready) return false;\n  Module.ccall(\'emu_midi_clear\', null, [], []);\n  for (const e of events){\n    const st = e.on ? (0x90 | (e.chan === 9 ? 9 : 0)) : (0x80 | (e.chan === 9 ? 9 : 0));\n    Module.ccall(\'emu_midi_add\', null, [\'number\',\'number\',\'number\',\'number\'],\n                 [e.t, st, e.note, e.on ? e.vel : 0]);\n  }\n  return true;\n}\nfunction midiEngineStart(speed, loop){\n  if (ready) Module.ccall(\'emu_midi_start\', null, [\'number\',\'number\'], [speed, loop ? 1 : 0]);\n}\nfunction midiEngineStop(){ if (ready) Module.ccall(\'emu_midi_stop\', null, [], []); }\nfunction midiEngineSpeed(x){ if (ready) Module.ccall(\'emu_midi_speed\', null, [\'number\'], [x]); }\nfunction midiEngineLoop(on){ if (ready) Module.ccall(\'emu_midi_loop\', null, [\'number\'], [on ? 1 : 0]); }\nfunction midiEnginePos(){ return ready ? Module.ccall(\'emu_midi_pos\', \'number\', [], []) : 0; }\n'

EMU_EXTRA_HEAD = '<script>var Module = { onRuntimeInitialized: function(){ if (window.onCoreReady) window.onCoreReady(); } };</script>\n<script src="8b8.js"></script>'

EMU_EXTRA_BODY = ''   # the Play section is shared now; nothing emulator-only here

SERIAL_INTRO = 'A control surface for the 8-Bit 8asterd: nine voices of chiptune across three AY-3-8910 chips, with effects built out of what those chips do when you push them past their limits. Everything below edits the unit live over USB. <b>Save to Unit</b> writes the settings into its memory so it keeps them with no computer attached.'

WASM_INTRO = 'The 8-Bit 8asterd, running in your browser. Nine voices of chiptune across three emulated AY-3-8910 chips, with effects built out of what those chips do when you push them past their limits &mdash; the crushing and glitching is the hardware straining, not something added on top. This runs the real firmware compiled to WebAssembly, the same code that is on the physical unit. Press <b>Start Audio</b>, then play it from the chord matrix, the keyboard, the sequencer or a game controller.'


def demo_midi_b64():
    """The bundled demo piece, base64 inlined.

    Inlined rather than fetched so the panel stays a single self-contained
    file that works from file:// with nothing beside it, as the logo does.
    It is under a kilobyte. Absent, the player simply starts with no file.
    """
    import base64, os
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "invention.mid")
    if not os.path.exists(f):
        return ""
    with open(f, "rb") as fh:
        b = base64.b64encode(fh.read()).decode("ascii")
    print(f"  demo: inlined invention.mid ({len(b)} chars base64)")
    return b


def brand_markup():
    """The header brand line, with the logo inlined if one is present.

    A logo file next to generate.py (logo.svg, logo.png or logo.webp) is
    base64-inlined rather than linked, so both panels stay single
    self-contained files that work from file:// with nothing alongside them.
    With no logo file the text stands on its own. Either way the whole thing
    links to the site, opened in a new tab so a click never loses an unsaved
    panel state or a running emulator.
    """
    import base64, os
    here = os.path.dirname(os.path.abspath(__file__))
    for fname, mime in (("logo.svg", "image/svg+xml"),
                        ("logo.png", "image/png"),
                        ("logo.webp", "image/webp")):
        f = os.path.join(here, fname)
        if os.path.exists(f):
            with open(f, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            print(f"  logo: inlined {fname} ({len(b64)//1024}KB base64)")
            return (f'<a class="brandlink" href="{SITE_HOME}" target="_blank"'
                    f' rel="noopener noreferrer">'
                    f'<img src="data:{mime};base64,{b64}" alt="The Key &amp; Cable Co.">'
                    '<span>The Key &amp; Cable Co.</span></a>')
    return (f'<a class="brandlink" href="{SITE_HOME}" target="_blank"'
            ' rel="noopener noreferrer"><span>The Key &amp; Cable Co.</span></a>')


def emit_html(path, transport="serial", extra_head="", extra_body=""):
    presets_arrays = {name: preset_array(v) for name, v in PRESETS.items()}
    tjs = SERIAL_TRANSPORT_JS if transport == "serial" else WASM_TRANSPORT_JS
    tui = SERIAL_TRANSPORT_UI if transport == "serial" else WASM_TRANSPORT_UI
    html = (HTML_TEMPLATE
            .replace("__PARAMS_JSON__", json.dumps(PARAMS))
            .replace("__PRESETS_JSON__", json.dumps(presets_arrays))
            .replace("__NUM_PARAMS__", str(len(PARAMS)))
            .replace("__LAYOUT_VERSION__", str(layout_version()))
            .replace("__SECTIONS_JSON__", json.dumps(SECTIONS))
            .replace("__TRANSPORT_JS__", tjs)
            .replace("__TRANSPORT_UI__", tui)
            .replace("__EXTRA_HEAD__", extra_head)
            .replace("__EXTRA_BODY__", extra_body)
            .replace("__BRAND__", brand_markup())
            .replace("__INTRO__", SERIAL_INTRO if transport == "serial" else WASM_INTRO)
            .replace("__SITE_URL__", SITE_URL)
            .replace("__SITE_TITLE__", SITE_TITLE)
            .replace("__SITE_DESC__", SITE_DESC)
            .replace("__DEMO_MIDI__", demo_midi_b64()))
    with open(path, "w") as f:
        f.write(html)
    print(f"wrote {path}  ({len(PRESETS)} presets)")


if __name__ == "__main__":
    out = os.path.dirname(os.path.abspath(__file__))
    normalise()
    emit_header(os.path.join(out, "parameters.h"))
    emit_temperaments(os.path.join(out, "temperaments.h"))
    emit_html(os.path.join(out, "index.html"))
    emu_dir = os.path.join(out, "emulator")
    if os.path.isdir(emu_dir):
        emit_html(os.path.join(emu_dir, "emulator.html"), transport="wasm",
                  extra_head=EMU_EXTRA_HEAD, extra_body=EMU_EXTRA_BODY)
