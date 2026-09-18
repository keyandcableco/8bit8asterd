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
         options=["Off", "Sync Buzz", "Stutter", "Scramble", "Zap"],
         default=0,
         help="Sync Buzz restarts the envelope (hard-sync). Stutter gates "
              "the mixer. Scramble throws junk at random registers. Zap "
              "sweeps the noise period."),
    dict(key="warp_rate", label="Rate", group="Warp Zone", kind="int",
         min=1, max=120, default=30,
         help="Low = slow chopping. High reaches audio rate and becomes a "
              "tone in its own right."),
    dict(key="warp_depth", label="Depth", group="Warp Zone", kind="int",
         min=1, max=63, default=20,
         help="How violent the effect is."),

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
    "1-Up Arp": dict(arp_mode=1, arp_rate=20, env_mode=1, env_attack=1,
                     env_decay=6, env_sustain=26, env_release=8),
    "Laser Jump": dict(sweep_amount=52, env_mode=1, env_attack=1,
                       env_decay=10, env_sustain=6, env_release=4),
    "Machine Gun": dict(retrig_rate=18, noise_enable=1, noise_period=4,
                        env_mode=1, env_attack=1, env_decay=14,
                        env_sustain=10, env_release=6),
}

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
  /* ---- NES front-loader palette -------------------------------------
     The console body was two greys over a near-black slot, with the red
     stripe as the only colour. Everything here is drawn from that: hard
     edges, chunky plastic bevels, red used sparingly so it still reads
     as an accent and not decoration. ------------------------------- */
  :root{
    --slot:      #0b0b0c;   /* the cartridge slot: near black        */
    --body-dark: #2e2f31;   /* lower console body grey               */
    --body:      #3c3d40;   /* main body grey                        */
    --body-lit:  #5a5c60;   /* top bevel highlight                   */
    --bezel:     #17181a;
    --red:       #c8322b;   /* the stripe                            */
    --red-lit:   #e8483f;
    --red-dim:   #6d1f1b;
    --text:      #dedbd2;   /* light grey plastic                    */
    --text-dim:  #8c8d90;
    --green:     #7bbf5a;   /* power LED                             */
    --pixel:     'Press Start 2P', monospace;
    --mono:      'JetBrains Mono', ui-monospace, monospace;
  }

  *{ box-sizing:border-box; }
  html,body{ margin:0; padding:0; }
  body{
    background:
      repeating-linear-gradient(0deg,
        rgba(255,255,255,.012) 0 2px, transparent 2px 4px),
      var(--slot);
    color:var(--text);
    font-family:var(--mono);
    min-height:100vh;
    padding:26px 14px 56px;
    display:flex;
    justify-content:center;
  }
  .rack{ width:100%; max-width:1000px; }

  /* Hard plastic bevel: light on top, dark underneath. No radius --
     the NES had almost none. */
  .bevel{
    border:2px solid var(--bezel);
    box-shadow:
      inset 0 2px 0 var(--body-lit),
      inset 0 -2px 0 rgba(0,0,0,.55);
  }

  /* ---- Header: the console face ---- */
  header{
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow:
      inset 0 2px 0 var(--body-lit),
      inset 0 -2px 0 rgba(0,0,0,.55);
    padding:0;
    margin-bottom:16px;
    overflow:hidden;
  }
  /* the stripe */
  .stripe{ display:flex; height:9px; }
  .stripe i{ flex:1; }
  .stripe i:nth-child(1){ background:var(--red); }
  .stripe i:nth-child(2){ background:var(--body-dark); flex:0 0 26px; }
  .stripe i:nth-child(3){ background:var(--red); }
  .stripe i:nth-child(4){ background:var(--body-dark); flex:0 0 26px; }
  .stripe i:nth-child(5){ background:var(--red); }

  .head-inner{
    display:flex; align-items:flex-end; justify-content:space-between;
    gap:16px; flex-wrap:wrap; padding:16px 18px 18px;
  }
  .brand .eyebrow{
    font-family:var(--pixel); font-size:7px; letter-spacing:.12em;
    color:var(--text-dim); margin-bottom:9px;
  }
  .brand h1{
    font-family:var(--pixel); font-size:19px; margin:0; line-height:1.35;
    color:var(--text); text-shadow:2px 2px 0 rgba(0,0,0,.65);
  }
  .brand h1 span{ color:var(--red-lit); }
  .brand p{ margin:10px 0 0; color:var(--text-dim); font-size:12px; max-width:50ch; line-height:1.55; }

  .connection{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }
  .status-pill{
    display:flex; align-items:center; gap:7px;
    font-family:var(--pixel); font-size:7px; letter-spacing:.06em;
    padding:9px 11px; background:var(--slot);
    border:2px solid var(--bezel); color:var(--text-dim);
  }
  .status-pill .dot{ width:7px; height:7px; background:#4a4b4d; }
  .status-pill.on{ color:var(--green); }
  .status-pill.on .dot{ background:var(--green); box-shadow:0 0 7px var(--green); }
  .status-pill.err{ color:var(--red-lit); }
  .status-pill.err .dot{ background:var(--red-lit); box-shadow:0 0 7px var(--red); }

  /* ---- Buttons: console plastic ---- */
  button.btn{
    font-family:var(--pixel); font-size:7px; letter-spacing:.04em;
    color:var(--text); cursor:pointer; padding:10px 12px;
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.5);
  }
  button.btn:hover{ color:var(--red-lit); }
  button.btn:active{
    box-shadow: inset 0 2px 4px rgba(0,0,0,.7);
    transform:translateY(1px);
  }
  button.btn:disabled{ opacity:.35; cursor:not-allowed; }
  button.btn:focus-visible, .switch:focus-visible, .enum-btn:focus-visible{
    outline:2px solid var(--red-lit); outline-offset:2px;
  }

  .unsupported, .mismatch{
    background:var(--body-dark); border:2px solid var(--red);
    padding:14px 16px; color:var(--text); font-size:12px;
    line-height:1.6; margin-bottom:16px;
  }
  .unsupported b, .mismatch b{
    color:var(--red-lit); font-family:var(--pixel); font-size:8px;
    display:block; margin-bottom:7px; line-height:1.5;
  }
  .mismatch code{ background:var(--slot); padding:1px 5px; color:var(--text); }

  /* ---- Preset bar ---- */
  .presets{
    display:flex; align-items:center; gap:8px; flex-wrap:wrap;
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.55);
    padding:12px 13px; margin-bottom:16px;
  }
  .presets .label{
    font-family:var(--pixel); font-size:7px; color:var(--text-dim);
    margin-right:4px;
  }
  select{
    font-family:var(--mono); font-size:12px; font-weight:500;
    background:var(--slot); color:var(--text);
    border:2px solid var(--bezel); padding:9px 9px;
  }

  /* ---- Modules: cartridge labels ---- */
  .modules{ display:grid; grid-template-columns:repeat(auto-fill,minmax(285px,1fr)); gap:14px; }
  .module{
    background:linear-gradient(180deg, var(--body) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 2px 0 var(--body-lit), inset 0 -2px 0 rgba(0,0,0,.55);
    padding:0 0 14px;
  }
  .module h2{
    font-family:var(--pixel); font-size:8px; letter-spacing:.04em;
    margin:0 0 13px; padding:11px 12px; line-height:1.5;
    color:var(--text);
    background:var(--red);
    border-bottom:2px solid var(--bezel);
    text-shadow:1px 1px 0 rgba(0,0,0,.45);
  }
  .ctl{ margin:0 13px 14px; }
  .ctl:last-child{ margin-bottom:0; }
  .ctl .row{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:7px; }
  .ctl .name{ font-family:var(--pixel); font-size:7px; color:var(--text-dim); line-height:1.5; }
  .ctl .value{ font-size:12px; font-weight:700; color:var(--red-lit); }
  .ctl .help{ font-size:10.5px; color:var(--text-dim); margin-top:6px; line-height:1.5; }

  /* ---- Toggle: the controller's A/B button ---- */
  .switch{
    width:34px; height:34px; border-radius:50%; flex:none; cursor:pointer;
    background:radial-gradient(circle at 38% 32%, #6a2621 0%, #47110e 70%);
    border:2px solid var(--bezel);
    box-shadow: inset 0 -2px 3px rgba(0,0,0,.6);
    display:flex; align-items:center; justify-content:center;
  }
  .switch .led{
    width:9px; height:9px; border-radius:50%;
    background:#2a0c0a; transition:background .12s, box-shadow .12s;
  }
  .switch.active{
    background:radial-gradient(circle at 38% 32%, var(--red-lit) 0%, var(--red) 72%);
  }
  .switch.active .led{ background:#ffd9d4; box-shadow:0 0 8px rgba(255,120,110,.95); }

  /* ---- Sliders ---- */
  input[type=range]{
    -webkit-appearance:none; appearance:none; width:100%;
    height:20px; background:transparent; cursor:pointer; margin:0;
  }
  input[type=range]::-webkit-slider-runnable-track{
    height:8px; background:var(--slot); border:2px solid var(--bezel);
  }
  input[type=range]::-webkit-slider-thumb{
    -webkit-appearance:none; width:14px; height:18px; margin-top:-7px;
    background:linear-gradient(180deg, var(--body-lit) 0%, var(--body-dark) 100%);
    border:2px solid var(--bezel);
  }
  input[type=range]:hover::-webkit-slider-thumb{
    background:linear-gradient(180deg, var(--red-lit) 0%, var(--red) 100%);
  }
  input[type=range]::-moz-range-track{
    height:8px; background:var(--slot); border:2px solid var(--bezel);
  }
  input[type=range]::-moz-range-thumb{
    width:14px; height:18px; border-radius:0;
    background:var(--body-lit); border:2px solid var(--bezel);
  }

  /* ---- Enum rows ---- */
  .enum-row{ display:flex; gap:5px; flex-wrap:wrap; }
  .enum-btn{
    font-family:var(--pixel); font-size:7px; line-height:1.5;
    background:var(--slot); color:var(--text-dim);
    border:2px solid var(--bezel); padding:8px 8px; cursor:pointer;
  }
  .enum-btn:hover{ color:var(--text); }
  .enum-btn.active{
    background:var(--red); color:#fff;
    text-shadow:1px 1px 0 rgba(0,0,0,.45);
  }

  /* ---- Serial log ---- */
  .console{
    margin-top:16px; background:var(--slot);
    border:2px solid var(--bezel); padding:12px 13px;
  }
  .console-head{ display:flex; justify-content:space-between; align-items:center; margin-bottom:9px; }
  .console-head .label{ font-family:var(--pixel); font-size:7px; color:var(--text-dim); }
  .console .clear{
    font-family:var(--pixel); font-size:7px; color:var(--text-dim);
    background:none; border:none; cursor:pointer;
  }
  .console .clear:hover{ color:var(--red-lit); }
  .log{ height:122px; overflow-y:auto; font-size:11.5px; line-height:1.65; color:var(--text-dim); }
  .log .tx{ color:var(--green); }
  .log .rx{ color:#d8c98a; }
  .log .sys{ color:var(--text-dim); font-style:italic; }
  .log .err{ color:var(--red-lit); }

  footer{
    text-align:center; color:var(--text-dim);
    font-family:var(--pixel); font-size:7px; line-height:1.9;
    margin-top:20px;
  }

  @media (prefers-reduced-motion: reduce){
    *{ transition:none !important; }
  }
  @media (max-width:560px){
    .brand h1{ font-size:15px; }
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

  <div class="presets">
    <span class="label">Preset</span>
    <select id="presetSelect"></select>
    <button class="btn" id="applyPreset">Apply</button>
    <button class="btn" id="readUnit">Read Unit</button>
    <button class="btn" id="saveUnit">Save to Unit</button>
    <button class="btn" id="defaultsBtn">Defaults</button>
    <button class="btn" id="exportBtn">Export</button>
    <button class="btn" id="importBtn">Import</button>
    <button class="btn" id="diagBtn">Diag</button>
  </div>

  <div class="modules" id="modules"></div>

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
const LAYOUT_VERSION = __LAYOUT_VERSION__;  // must match the flashed firmware

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
  const modules = document.getElementById('modules');
  const groups = [...new Set(PARAMS.map(p => p.group))];
  for (const g of groups){
    const mod = document.createElement('section');
    mod.className = 'module';
    const h = document.createElement('h2');
    h.textContent = g;
    mod.appendChild(h);
    for (const p of PARAMS.filter(x => x.group === g)){
      mod.appendChild(buildControl(p));
    }
    modules.appendChild(mod);
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

/* ==== Boot ============================================================== */
buildUI();
renderAll();
</script>
</body>
</html>
"""


def emit_html(path):
    presets_arrays = {name: preset_array(v) for name, v in PRESETS.items()}
    html = (HTML_TEMPLATE
            .replace("__PARAMS_JSON__", json.dumps(PARAMS))
            .replace("__PRESETS_JSON__", json.dumps(presets_arrays))
            .replace("__NUM_PARAMS__", str(len(PARAMS)))
            .replace("__LAYOUT_VERSION__", str(layout_version())))
    with open(path, "w") as f:
        f.write(html)
    print(f"wrote {path}  ({len(PRESETS)} presets)")


if __name__ == "__main__":
    out = os.path.dirname(os.path.abspath(__file__))
    normalise()
    emit_header(os.path.join(out, "parameters.h"))
    emit_html(os.path.join(out, "index.html"))
