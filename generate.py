#!/usr/bin/env python3
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
         help="Lowest held note's volume follows the chip's envelope generator."),
    dict(key="buzz_ratio", label="Ratio", group="Buzzy Bass", kind="int",
         min=1, max=8, default=1,
         help="Envelope rate vs. note pitch. No exact formula — tune by ear."),
    dict(key="buzz_shape", label="Shape", group="Buzzy Bass", kind="enum",
         options=["Saw \u2193", "Tri \u2193\u2191", "Saw \u2191", "Tri \u2191\u2193"],
         default=0,
         help="The four AY envelope shapes that loop continuously."),
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
                  "Tape Stop", "Siren", "Crush", "Ring"],
         default=0,
         help="Sync Buzz restarts the envelope. Stutter gates the mixer. "
              "Scramble throws junk at registers. Zap sweeps noise. The "
              "last four move on their own: Tape Stop drags pitch down and "
              "snaps back, Siren sweeps it, Crush quantises it coarser and "
              "coarser, Ring is audio-rate amplitude modulation."),
    dict(key="warp_rate", label="Rate", group="Warp Zone", kind="int",
         min=1, max=120, default=30,
         help="Low = slow chopping. High reaches audio rate and becomes a "
              "tone in its own right."),
    dict(key="warp_depth", label="Depth", group="Warp Zone", kind="int",
         min=1, max=63, default=20,
         help="How violent the effect is."),
    dict(key="warp_motion", label="Motion", group="Warp Zone", kind="int",
         min=0, max=64, default=0,
         help="Sweeps Rate up and down on its own, hands free. The sweep "
              "itself is usually the interesting part, not where it "
              "settles. 0 = Rate stays put."),

    # --- Vibrato -----------------------------------------------------------
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
              "values flam, high values become a buzz roll. 0 = off."),
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
         help="Re-strikes the envelope while a note is held. 0 = off."),

    # --- Envelope ----------------------------------------------------------
    dict(key="env_mode", label="Mode", group="Envelope", kind="enum",
         options=["MIDI ch presets", "Custom ADSR"], default=0,
         help="Presets = the original per-MIDI-channel tones[] table."),
    dict(key="env_attack", label="Attack", group="Envelope", kind="int",
         min=1, max=32, default=1,
         help=""),
    dict(key="env_decay", label="Decay", group="Envelope", kind="int",
         min=1, max=32, default=8,
         help=""),
    dict(key="env_sustain", label="Sustain", group="Envelope", kind="int",
         min=0, max=32, default=32,
         help=""),
    dict(key="env_release", label="Release", group="Envelope", kind="int",
         min=1, max=32, default=32,
         help=""),

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
    "Buzz Bass Lead": dict(buzz_enable=1, buzz_ratio=2, buzz_shape=0,
                           vib_enable=1, vib_rate=55, vib_depth=5, vib_delay=60),
    "Haunted Organ": dict(vib_enable=1, vib_rate=30, vib_depth=12,
                          trem_enable=1, trem_rate=35, trem_depth=6,
                          env_mode=1, env_attack=6, env_decay=4,
                          env_sustain=28, env_release=20),
    "Wind Machine": dict(noise_enable=1, noise_period=13,
                         trem_enable=1, trem_rate=25, trem_depth=7,
                         env_mode=1, env_attack=10, env_decay=2,
                         env_sustain=30, env_release=4, vel_sense=0),
    "Chip Whistle": dict(transpose=36, glide=25,
                         vib_enable=1, vib_rate=80, vib_depth=4, vib_delay=40),
    "808 Kit": dict(drum_tune=78, drum_decay=165, drum_bend=130, drum_noise=9),
    "Tight Kit": dict(drum_tune=118, drum_decay=62, drum_bend=70, drum_noise=5),
    "MadMax Buzzer": dict(buzz_enable=1, buzz_ratio=2, buzz_shape=1,
                          buzz_detune=38, env_mode=1, env_attack=1,
                          env_decay=3, env_sustain=30, env_release=12),
    "Arcade Warp": dict(warp_mode=1, warp_rate=95, warp_depth=40,
                        buzz_enable=1, buzz_ratio=3, buzz_detune=36),
    "Broken Cabinet": dict(warp_mode=3, warp_rate=22, warp_depth=55,
                           noise_enable=1, noise_period=6),
    "Tape Eaten": dict(warp_mode=5, warp_rate=70, warp_depth=34,
                       warp_motion=12),
    "Air Raid": dict(warp_mode=6, warp_rate=60, warp_depth=44,
                     warp_motion=20),
    "Dying Console": dict(warp_mode=7, warp_rate=48, warp_depth=40,
                          warp_motion=9, noise_enable=1, noise_period=9),
    "Ring Zone": dict(warp_mode=8, warp_rate=105, warp_depth=30,
                      warp_motion=26, buzz_enable=1, buzz_ratio=2),
    "Buzz Roll": dict(drum_roll=32, drum_decay=70, drum_chaos=10),
    "Drunk Drummer": dict(drum_flam=7, drum_chaos=38, drum_tune=92,
                          drum_decay=120),
    "Reverse Kit": dict(drum_reverse=1, drum_decay=150, drum_bend=40),
    "Broken Machine": dict(drum_roll=44, drum_chaos=58, drum_noise=4,
                           drum_decay=55, drum_bend=170),
    "1-Up Arp": dict(arp_mode=1, arp_rate=20, env_mode=1, env_attack=1,
                     env_decay=6, env_sustain=26, env_release=8),
    "Laser Jump": dict(sweep_amount=52, env_mode=1, env_attack=1,
                       env_decay=10, env_sustain=6, env_release=4),
    "Machine Gun": dict(retrig_rate=18, noise_enable=1, noise_period=4,
                        env_mode=1, env_attack=1, env_decay=14,
                        env_sustain=10, env_release=6),
}

# ---------------------------------------------------------------------------
# Panel sections
# ---------------------------------------------------------------------------
# Tones and drums are different voices sharing the same three chips, and the
# FX reach across both. The panel says so out loud rather than leaving it to
# be discovered: each band is labelled with what it touches, and the Warp
# Zone sits across the bottom spanning both because it hits them differently.

SECTIONS = [
    dict(key="mix", title="Master Mixer", scope="everything out",
         blurb="Output levels. Tone and noise share one amplitude register "
               "per channel on this chip, so noise is thinned by gating "
               "rather than by a level control that does not exist.",
         groups=["Mixer"]),
    dict(key="tone", title="Tone Voices", scope="pitched voices",
         blurb="The melodic side. Nothing here touches the drum channel.",
         groups=["Envelope", "Tuning", "Pitch & Response", "Buzzy Bass",
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
         groups=["Warp Zone"]),
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
    font-family:var(--pixel); font-size:calc(7px * var(--ui-scale));
    letter-spacing:.12em; color:var(--text-dim); margin-bottom:9px;
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
</head>
<body>
<div class="rack">

  <header>
    <div class="stripe"><i></i><i></i><i></i><i></i><i></i></div>
    <div class="head-inner">
      <div class="brand">
        <div class="eyebrow">Semiotic Sounds</div>
        <h1>8-BIT<br>8ASTERD <span>&#9632;</span></h1>
        <p>Three AY-3-8910s under live control. Generated from generate.py &mdash; __NUM_PARAMS__ parameters over USB serial. SAVE writes them to the unit.</p>
      </div>
      <div class="connection">
        <div class="status-pill" id="statusPill"><span class="dot"></span><span id="statusText">Disconnected</span></div>
        <button class="btn" id="connectBtn">Connect</button>
        <button class="btn danger" id="disconnectBtn" disabled>Disconnect</button>
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

  <div class="bar">
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
    <span class="spacer"></span>
    <button class="btn" id="diagBtn">Diag</button>
  </div>

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

/* ==== Web Serial ======================================================== */
let port = null, reader = null, writer = null, inBuf = '';
let sawLayout = false;
const statusPill = document.getElementById('statusPill');
const statusText = document.getElementById('statusText');
const connectBtn = document.getElementById('connectBtn');
const disconnectBtn = document.getElementById('disconnectBtn');

function setStatus(mode, text){
  statusPill.className = 'status-pill' + (mode ? ' ' + mode : '');
  statusText.textContent = text;
}

async function connect(){
  try{
    port = await navigator.serial.requestPort();
    await port.open({ baudRate: 115200 });
    const dec = new TextDecoderStream();
    port.readable.pipeTo(dec.writable).catch(()=>{});
    reader = dec.readable.getReader();
    const enc = new TextEncoderStream();
    enc.readable.pipeTo(port.writable).catch(()=>{});
    writer = enc.writable.getWriter();
    connectBtn.disabled = true; disconnectBtn.disabled = false;
    setStatus('on','Connected');
    log('sys','Connected.');
    readLoop();
    sawLayout = false;
    send('DUMP');
    // Firmware older than the handshake answers DUMP with PRESET: but no
    // LAYOUT: line at all, which is itself a mismatch worth reporting.
    setTimeout(() => {
      if (!sawLayout && port){
        document.getElementById('mismatchDetail').textContent =
          'The unit did not report a parameter layout at all, so it predates ' +
          'this panel. This panel expects layout 0x' +
          LAYOUT_VERSION.toString(16).toUpperCase() + ' with ' + NUM_PARAMS + ' parameters.';
        document.getElementById('mismatch').style.display = 'block';
        log('err', 'No LAYOUT reply \u2014 flashed firmware is out of date.');
      }
    }, 1500);
  }catch(err){
    setStatus('err','Connect failed');
    log('err','Connect failed: ' + err.message);
  }
}

async function disconnect(){
  try{ await reader?.cancel(); }catch(e){}
  try{ await writer?.close(); }catch(e){}
  try{ await port?.close(); }catch(e){}
  reader = writer = port = null;
  connectBtn.disabled = false; disconnectBtn.disabled = true;
  setStatus('','Disconnected');
  log('sys','Disconnected.');
}

async function readLoop(){
  try{
    while(true){
      const { value, done } = await reader.read();
      if (done) break;
      inBuf += value;
      let idx;
      while ((idx = inBuf.indexOf('\n')) >= 0){
        const line = inBuf.slice(0, idx).replace('\r','');
        inBuf = inBuf.slice(idx + 1);
        if (line.length) handleLine(line);
      }
    }
  }catch(err){
    log('err','Read error: ' + err.message);
  }finally{
    if (port) disconnect();
  }
}

function send(cmd){
  log('tx','\u00bb ' + cmd);
  if (writer) writer.write(cmd + '\n').catch(err => log('err','Write failed: ' + err.message));
}

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
  else if (line.startsWith('DIAG')){
    log('rx','\u00ab ' + line);
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

connectBtn.onclick = connect;
disconnectBtn.onclick = disconnect;
if (!('serial' in navigator)){
  document.getElementById('unsupported').style.display = 'block';
  connectBtn.disabled = true;
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
const themeSel = document.getElementById('themeSelect');
const sizeSel  = document.getElementById('sizeSelect');
themeSel.onchange = () => applyTheme(themeSel.value);
sizeSel.onchange  = () => applyScale(sizeSel.value);

(function restoreAppearance(){
  let t = 'nes', v = '1';
  try {
    t = localStorage.getItem('8b8.theme') || t;
    v = localStorage.getItem('8b8.scale') || v;
  } catch(e){}
  themeSel.value = t; sizeSel.value = v;
  applyTheme(t); applyScale(v);
})();

/* ==== Boot ============================================================== */
buildUI();
renderAll();
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


def emit_html(path):
    presets_arrays = {name: preset_array(v) for name, v in PRESETS.items()}
    html = (HTML_TEMPLATE
            .replace("__PARAMS_JSON__", json.dumps(PARAMS))
            .replace("__PRESETS_JSON__", json.dumps(presets_arrays))
            .replace("__NUM_PARAMS__", str(len(PARAMS)))
            .replace("__LAYOUT_VERSION__", str(layout_version()))
            .replace("__SECTIONS_JSON__", json.dumps(SECTIONS)))
    with open(path, "w") as f:
        f.write(html)
    print(f"wrote {path}  ({len(PRESETS)} presets)")


if __name__ == "__main__":
    out = os.path.dirname(os.path.abspath(__file__))
    normalise()
    emit_header(os.path.join(out, "parameters.h"))
    emit_temperaments(os.path.join(out, "temperaments.h"))
    emit_html(os.path.join(out, "index.html"))
