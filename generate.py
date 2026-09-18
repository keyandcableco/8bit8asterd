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
    flex:0 0 120px; display:flex; flex-direction:column; gap:2px;
    touch-action:none; min-height:260px;
  }
  .strum .seg{
    flex:1; border:2px solid var(--bezel); cursor:pointer;
    background:linear-gradient(90deg, var(--body-dark) 0%, var(--slot) 100%);
  }
  .strum .seg.lit{ background:var(--accent); }
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

  /* ---- Sequencer ---- */
  .seq{ padding:0 13px 13px; overflow-x:auto; }
  .seqrow{ display:flex; align-items:center; gap:3px; margin-bottom:3px; }
  .seqname{
    flex:0 0 72px; font-family:var(--pixel);
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
        <div class="eyebrow">Semiotic Sounds</div>
        <h1>8-BIT<br>8ASTERD <span>&#9632;</span></h1>
        <p>Three AY-3-8910s under live control. Generated from generate.py &mdash; __NUM_PARAMS__ parameters over USB serial. SAVE writes them to the unit.</p>
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
    <button class="btn" id="learnBtn">MIDI Learn</button>
    <button class="btn" id="diagBtn">Diag</button>
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
      <div class="bar" style="margin:0 13px 10px;">
        <span class="label">Sharp</span>
        <div class="switch" id="sharpSw"><div class="led"></div></div>
        <span class="label">Hold</span>
        <div class="switch" id="latchSw"><div class="led"></div></div>
        <span class="spacer"></span>
        <span class="label" id="chordName">&mdash;</span>
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
      <div class="bar" style="margin:0 13px 10px;">
        <button class="btn" id="seqPlay">Play</button>
        <button class="btn" id="seqClear">Clear</button>
        <span class="label">Pattern</span>
        <select id="seqPreset"></select>
        <span class="label">Tempo</span>
        <input type="range" id="seqTempo" min="50" max="200" value="110" style="flex:1 1 120px;max-width:200px">
        <span class="value" id="seqBpm">110 BPM</span>
      </div>
      <div class="seq">
        <div class="seqhead" id="seqHead"></div>
        <div id="seqRows"></div>
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
const QUALITY = [
  { suffix: '',  name: 'maj', tones: [0,4,7]    },
  { suffix: 'm', name: 'min', tones: [0,3,7]    },
  { suffix: '7', name: '7th', tones: [0,4,7,10] }
];
const STRUM_SEGMENTS = 12;

let sharpOn = false, latchOn = false;
let heldChord = null;          // {root, quality} currently sounding
let chordNotes = [];           // midi notes we switched on
let strumNotes = [];           // the harp ladder for that chord
let lastStrumSeg = -1, strumming = false;

function chordFor(rootIdx, qIdx){
  const base = ROOTS[rootIdx][1] + (sharpOn ? 1 : 0);
  return QUALITY[qIdx].tones.map(t => base + t);
}

// The harp: chord tones stacked upward until twelve sections are filled, so
// a strum walks the chord rather than a scale.
function ladderFor(rootIdx, qIdx){
  const tones = QUALITY[qIdx].tones;
  let base = ROOTS[rootIdx][1] + (sharpOn ? 1 : 0) + 12;
  let out = [];
  for (let i = 0; i < STRUM_SEGMENTS; i++){
    out.push(base + tones[i % tones.length] + 12 * Math.floor(i / tones.length));
  }
  // The firmware's note table covers MIDI 24..96. Twelve chord tones stacked
  // can overshoot that, and anything past the top would clamp onto the same
  // note, so drop the whole ladder by octaves until it fits. Shifting all of
  // it keeps the shape of the strum intact.
  while (Math.max.apply(null, out) > 96) out = out.map(n => n - 12);
  while (Math.min.apply(null, out) < 24) out = out.map(n => n + 12);
  return out;
}

function releaseChord(){
  chordNotes.forEach(n => stopNote(n, 0));
  chordNotes = [];
}

function pressChord(rootIdx, qIdx){
  releaseChord();
  heldChord = { rootIdx, qIdx };
  chordNotes = chordFor(rootIdx, qIdx);
  chordNotes.forEach(n => playNote(n, 100, 0));
  strumNotes = ladderFor(rootIdx, qIdx);
  document.getElementById('chordName').textContent =
    ROOTS[rootIdx][0] + (sharpOn ? '#' : '') + QUALITY[qIdx].suffix;
  paintChords();
}

function paintChords(){
  document.querySelectorAll('.chordbtn').forEach(b => {
    const on = heldChord && (b.dataset.root | 0) === heldChord.rootIdx
                         && (b.dataset.q | 0) === heldChord.qIdx;
    b.classList.toggle('latched', !!on && latchOn);
    b.classList.toggle('down', !!on && !latchOn);
  });
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
      b.addEventListener('pointerdown', e => {
        e.preventDefault();
        b.setPointerCapture(e.pointerId);
        pressChord(ri, qi);
      });
      b.addEventListener('pointerup', () => {
        if (!latchOn) { releaseChord(); heldChord = null; paintChords(); }
      });
      grid.appendChild(b);
    });
  });

  // Strumpad: twelve sections, highest note at the top.
  const strum = document.getElementById('strum');
  const lbl = document.createElement('div');
  lbl.className = 'label'; lbl.textContent = 'STRUM';
  strum.appendChild(lbl);
  for (let i = STRUM_SEGMENTS - 1; i >= 0; i--){
    const seg = document.createElement('div');
    seg.className = 'seg'; seg.dataset.seg = i;
    strum.appendChild(seg);
  }

  function hit(seg, el){
    if (seg === lastStrumSeg) return;      // only on crossing into a new one
    lastStrumSeg = seg;
    if (!strumNotes.length) return;
    const n = strumNotes[seg];
    playNote(n, 100, 0);
    // The harp is plucked, not held: let the voice's own envelope end it.
    setTimeout(() => stopNote(n, 0), 400);
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
  const endStrum = () => { strumming = false; lastStrumSeg = -1; };
  strum.addEventListener('pointerup', endStrum);
  strum.addEventListener('pointercancel', endStrum);

  // Modifier switches
  const sharpSw = document.getElementById('sharpSw');
  const latchSw = document.getElementById('latchSw');
  sharpSw.onclick = () => {
    sharpOn = !sharpOn;
    sharpSw.classList.toggle('active', sharpOn);
    if (heldChord) pressChord(heldChord.rootIdx, heldChord.qIdx);
  };
  latchSw.onclick = () => {
    latchOn = !latchOn;
    latchSw.classList.toggle('active', latchOn);
    if (!latchOn) { releaseChord(); heldChord = null; }
    paintChords();
  };

  buildKeyboard();
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

function paintKey(letter, on){
  document.querySelectorAll('[data-key="' + letter + '"]').forEach(e => e.classList.toggle('down', on));
}

window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const k = e.key.toLowerCase();
  const m = keyToNote[k];
  if (!m || heldKeys.has(k)) return;
  heldKeys.add(k);
  playNote(m.note, 100, m.chan);
  paintKey(k, true);
});
window.addEventListener('keyup', e => {
  const k = e.key.toLowerCase();
  if (!heldKeys.has(k)) return;
  heldKeys.delete(k);
  const m = keyToNote[k];
  if (m && m.chan !== 9) stopNote(m.note, 0);
  paintKey(k, false);
});

// Pointer play. Drums are one-shots, so they are never held.
document.addEventListener('pointerdown', e => {
  const el = e.target.closest('.wkey, .bkey, .pad');
  if (!el) return;
  e.preventDefault();
  const note = el.dataset.note | 0;
  const drum = el.dataset.drum === '1';
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

/* ==== Drum sequencer ==================================================== */
// Sixteen steps against a drift-corrected clock: each tick schedules the
// next from when it SHOULD have fired, so the pattern does not wander the
// way a plain setInterval does.
const SEQ_STEPS = 16;
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
  'Tom Roll': { LoTom:[0,2,4,6], HiTom:[8,10,12,14], Crash:[0] }
};

let seqGrid = SEQ_ROWS.map(() => new Array(SEQ_STEPS).fill(false));
let seqPlaying = false, seqStep = 0, seqTimer = null, seqNext = 0;

function buildSequencer(){
  const head = document.getElementById('seqHead');
  const rows = document.getElementById('seqRows');
  if (!head || !rows) return;

  for (let i = 0; i < SEQ_STEPS; i++){
    const d = document.createElement('div');
    d.textContent = (i % 4 === 0) ? String(i / 4 + 1) : '';
    if (i % 4 === 0) d.className = 'beat';
    head.appendChild(d);
  }

  SEQ_ROWS.forEach((r, ri) => {
    const row = document.createElement('div');
    row.className = 'seqrow';
    const nm = document.createElement('div');
    nm.className = 'seqname'; nm.textContent = r.name;
    nm.onclick = () => playNote(r.note, 110, 9);       // audition
    row.appendChild(nm);
    for (let si = 0; si < SEQ_STEPS; si++){
      const c = document.createElement('div');
      c.className = 'step' + (si % 4 === 0 ? ' beat' : '');
      c.dataset.r = ri; c.dataset.s = si;
      c.addEventListener('pointerdown', e => {
        e.preventDefault();
        seqGrid[ri][si] = !seqGrid[ri][si];
        c.classList.toggle('on', seqGrid[ri][si]);
        if (seqGrid[ri][si]) playNote(r.note, 110, 9);
      });
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

  const tempo = document.getElementById('seqTempo');
  tempo.oninput = () => { document.getElementById('seqBpm').textContent = tempo.value + ' BPM'; };

  document.getElementById('seqPlay').onclick = toggleSeq;
  document.getElementById('seqClear').onclick = () => loadPattern('Empty');

  loadPattern('Four/Four');
  document.getElementById('seqPreset').value = 'Four/Four';
}

function paintSeq(){
  document.querySelectorAll('.step').forEach(c => {
    c.classList.toggle('on', seqGrid[c.dataset.r | 0][c.dataset.s | 0]);
  });
}

function loadPattern(name){
  const p = SEQ_PATTERNS[name] || {};
  seqGrid = SEQ_ROWS.map(r => {
    const on = p[r.name] || [];
    const row = new Array(SEQ_STEPS).fill(false);
    on.forEach(i => { if (i < SEQ_STEPS) row[i] = true; });
    return row;
  });
  paintSeq();
}

function seqTick(){
  document.querySelectorAll('.step.playing').forEach(c => c.classList.remove('playing'));
  SEQ_ROWS.forEach((r, ri) => {
    if (seqGrid[ri][seqStep]) playNote(r.note, 110, 9);
    const cell = document.querySelector('.step[data-r="' + ri + '"][data-s="' + seqStep + '"]');
    if (cell) cell.classList.add('playing');
  });
  seqStep = (seqStep + 1) % SEQ_STEPS;

  const bpm = parseInt(document.getElementById('seqTempo').value, 10) || 110;
  const interval = 60000 / bpm / 4;            // sixteenth notes
  seqNext += interval;
  const drift = seqNext - performance.now();
  seqTimer = setTimeout(seqTick, Math.max(0, drift));
}

function toggleSeq(){
  const btn = document.getElementById('seqPlay');
  if (seqPlaying){
    clearTimeout(seqTimer); seqTimer = null;
    seqPlaying = false; btn.textContent = 'Play';
    document.querySelectorAll('.step.playing').forEach(c => c.classList.remove('playing'));
  } else {
    seqPlaying = true; btn.textContent = 'Stop';
    seqStep = 0; seqNext = performance.now();
    seqTick();
  }
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
  // Shoulders and left/right walk the seven roots; face buttons pick quality.
  if (hit(GP.LEFT)  || hit(GP.L1)) gpRootIdx = (gpRootIdx + ROOTS.length - 1) % ROOTS.length;
  if (hit(GP.RIGHT) || hit(GP.R1)) gpRootIdx = (gpRootIdx + 1) % ROOTS.length;
  if (hit(GP.Y)){
    sharpOn = !sharpOn;
    const sw = document.getElementById('sharpSw');
    if (sw) sw.classList.toggle('active', sharpOn);
  }
  if (hit(GP.A)) pressChord(gpRootIdx, 0);
  if (hit(GP.B)) pressChord(gpRootIdx, 1);
  if (hit(GP.X)) pressChord(gpRootIdx, 2);
  if (hit(GP.START)){ releaseChord(); heldChord = null; paintChords(); }
}

function gpDrums(hit){
  GP_DRUMS.forEach(([b, note]) => { if (hit(b)) playNote(note, 110, 9); });
}

// Right stick vertical walks the strum ladder, so a flick sweeps the chord.
function gpStrum(pad){
  const y = pad.axes[3] || 0;
  if (Math.abs(y) < 0.12){ gpLastStrumSeg = -1; return; }
  if (!strumNotes.length) return;
  const seg = Math.max(0, Math.min(STRUM_SEGMENTS - 1,
    Math.floor((1 - (y + 1) / 2) * STRUM_SEGMENTS)));
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


SERIAL_TRANSPORT_JS = "/* ==== Web Serial ======================================================== */\nlet port = null, reader = null, writer = null, inBuf = '';\nconst statusPill = document.getElementById('statusPill');\nconst statusText = document.getElementById('statusText');\nconst connectBtn = document.getElementById('connectBtn');\nconst disconnectBtn = document.getElementById('disconnectBtn');\n\nfunction setStatus(mode, text){\n  statusPill.className = 'status-pill' + (mode ? ' ' + mode : '');\n  statusText.textContent = text;\n}\n\nasync function connect(){\n  try{\n    port = await navigator.serial.requestPort();\n    await port.open({ baudRate: 115200 });\n    const dec = new TextDecoderStream();\n    port.readable.pipeTo(dec.writable).catch(()=>{});\n    reader = dec.readable.getReader();\n    const enc = new TextEncoderStream();\n    enc.readable.pipeTo(port.writable).catch(()=>{});\n    writer = enc.writable.getWriter();\n    connectBtn.disabled = true; disconnectBtn.disabled = false;\n    setStatus('on','Connected');\n    log('sys','Connected.');\n    readLoop();\n    sawLayout = false;\n    send('DUMP');\n    // Firmware older than the handshake answers DUMP with PRESET: but no\n    // LAYOUT: line at all, which is itself a mismatch worth reporting.\n    setTimeout(() => {\n      if (!sawLayout && port){\n        document.getElementById('mismatchDetail').textContent =\n          'The unit did not report a parameter layout at all, so it predates ' +\n          'this panel. This panel expects layout 0x' +\n          LAYOUT_VERSION.toString(16).toUpperCase() + ' with ' + NUM_PARAMS + ' parameters.';\n        document.getElementById('mismatch').style.display = 'block';\n        log('err', 'No LAYOUT reply \\u2014 flashed firmware is out of date.');\n      }\n    }, 1500);\n  }catch(err){\n    setStatus('err','Connect failed');\n    log('err','Connect failed: ' + err.message);\n  }\n}\n\nasync function disconnect(){\n  try{ await reader?.cancel(); }catch(e){}\n  try{ await writer?.close(); }catch(e){}\n  try{ await port?.close(); }catch(e){}\n  reader = writer = port = null;\n  connectBtn.disabled = false; disconnectBtn.disabled = true;\n  setStatus('','Disconnected');\n  log('sys','Disconnected.');\n}\n\nasync function readLoop(){\n  try{\n    while(true){\n      const { value, done } = await reader.read();\n      if (done) break;\n      inBuf += value;\n      let idx;\n      while ((idx = inBuf.indexOf('\\n')) >= 0){\n        const line = inBuf.slice(0, idx).replace('\\r','');\n        inBuf = inBuf.slice(idx + 1);\n        if (line.length) handleLine(line);\n      }\n    }\n  }catch(err){\n    log('err','Read error: ' + err.message);\n  }finally{\n    if (port) disconnect();\n  }\n}\n\nfunction playNote(note, vel, chan){ send('NON:' + (chan||0) + ':' + note + ':' + (vel||100)); }\nfunction stopNote(note, chan){ send('NOF:' + (chan||0) + ':' + note); }\n\nfunction send(cmd){\n  log('tx','\\u00bb ' + cmd);\n  if (writer) writer.write(cmd + '\\n').catch(err => log('err','Write failed: ' + err.message));\n}\n\n\n\nconnectBtn.onclick = connect;\ndisconnectBtn.onclick = disconnect;\nif (!('serial' in navigator)){\n  document.getElementById('unsupported').style.display = 'block';\n  connectBtn.disabled = true;\n}\n\n"

SERIAL_TRANSPORT_UI = '        <button class="btn" id="connectBtn">Connect</button>\n        <button class="btn danger" id="disconnectBtn" disabled>Disconnect</button>'

WASM_TRANSPORT_UI = '        <button class="btn" id="startBtn">Start Audio</button>\n        <button class="btn danger" id="stopBtn" disabled>Stop</button>'

WASM_TRANSPORT_JS = "/* ==== Emulated transport ================================================ */\n// The firmware itself, compiled to WebAssembly, driving three emulated\n// AY-3-8910s into Web Audio. The panel above is byte-identical to the one\n// that talks to real hardware over serial -- the only thing that changes is\n// what send() writes to. Same firmware, same parameters, same protocol.\n\nlet audioCtx = null, node = null, ready = false, pollTimer = null;\nconst HEAP_SAMPLES = 2048;\nlet heapPtr = 0;\n\nconst statusPill = document.getElementById('statusPill');\nconst statusText = document.getElementById('statusText');\nconst startBtn = document.getElementById('startBtn');\nconst stopBtn  = document.getElementById('stopBtn');\n\nfunction setStatus(mode, text){\n  statusPill.className = 'status-pill' + (mode ? ' ' + mode : '');\n  statusText.textContent = text;\n}\n\nfunction send(cmd){\n  log('tx','\\u00bb ' + cmd);\n  if (ready) Module.ccall('emu_send_line', null, ['string'], [cmd]);\n}\n\nfunction pollReplies(){\n  if (!ready) return;\n  const s = Module.ccall('emu_read_lines', 'string', [], []);\n  if (!s) return;\n  for (const line of s.split('\\n')) if (line.length) handleLine(line);\n}\n\nasync function startAudio(){\n  if (!window.Module || !Module.ccall){\n    log('err','The emulator core has not loaded. Did you run build-wasm.sh?');\n    setStatus('err','No core');\n    return;\n  }\n  audioCtx = new (window.AudioContext || window.webkitAudioContext)();\n  await audioCtx.resume();\n\n  Module.ccall('emu_init', null, ['number'], [audioCtx.sampleRate]);\n  heapPtr = Module._malloc(HEAP_SAMPLES * 4);\n  ready = true;\n\n  node = audioCtx.createScriptProcessor(HEAP_SAMPLES, 0, 1);\n  node.onaudioprocess = (e) => {\n    const out = e.outputBuffer.getChannelData(0);\n    Module.ccall('emu_render', null, ['number','number'], [heapPtr, out.length]);\n    out.set(Module.HEAPF32.subarray(heapPtr >> 2, (heapPtr >> 2) + out.length));\n  };\n  node.connect(audioCtx.destination);\n\n  startBtn.disabled = true; stopBtn.disabled = false;\n  setStatus('on','Running');\n  log('sys','Emulator running at ' + audioCtx.sampleRate + 'Hz.');\n\n  pollTimer = setInterval(pollReplies, 60);\n  send('DUMP');\n}\n\nfunction stopAudio(){\n  if (node) { node.disconnect(); node = null; }\n  if (audioCtx) { audioCtx.close(); audioCtx = null; }\n  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }\n  ready = false;\n  startBtn.disabled = false; stopBtn.disabled = true;\n  setStatus('','Stopped');\n}\n\nstartBtn.onclick = startAudio;\nstopBtn.onclick  = stopAudio;\n\n/* ---- playing it ---- */\nfunction playNote(note, vel, chan){\n  if (ready) Module.ccall('emu_note_on', null, ['number','number','number'], [chan||0, note, vel||100]);\n}\nfunction stopNote(note, chan){\n  if (ready) Module.ccall('emu_note_off', null, ['number','number'], [chan||0, note]);\n}\n\n// Real MIDI hardware, if the browser offers it.\nif (navigator.requestMIDIAccess){\n  navigator.requestMIDIAccess().then(a => {\n    for (const inp of a.inputs.values()){\n      inp.onmidimessage = m => {\n        const [st, d1, d2] = m.data;\n        const ch = st & 0x0F, cmd = st & 0xF0;\n        if (cmd === 0x90 && d2 > 0) playNote(d1, d2, ch);\n        else if (cmd === 0x80 || (cmd === 0x90 && d2 === 0)) stopNote(d1, ch);\n      };\n    }\n    log('sys','MIDI input connected.');\n  }).catch(() => {});\n}\n"

EMU_EXTRA_HEAD = '<script>var Module = { onRuntimeInitialized: function(){ if (window.onCoreReady) window.onCoreReady(); } };</script>\n<script src="8b8.js"></script>'

EMU_EXTRA_BODY = ''   # the Play section is shared now; nothing emulator-only here

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
            .replace("__EXTRA_BODY__", extra_body))
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
