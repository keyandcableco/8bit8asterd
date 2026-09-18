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
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#12151a; --panel:#1b2027; --panel-2:#20262f; --groove:#0d0f13;
    --line:#2c333d; --text:#e9e7df; --text-dim:#8b93a1;
    --amber:#ffb454; --amber-dim:#6b5430;
    --green:#6fcf97; --green-dim:#2f4538;
    --red:#ff6b6b; --red-dim:#5a2f2f;
    --display:'Space Grotesk',sans-serif;
    --mono:'JetBrains Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{
    background:radial-gradient(ellipse at 20% -10%, #1d232c 0%, var(--bg) 55%);
    color:var(--text); font-family:var(--mono); min-height:100vh;
    padding:28px 16px 60px; display:flex; justify-content:center;
  }
  .rack{width:100%;max-width:980px}

  header{
    display:flex; align-items:flex-end; justify-content:space-between;
    gap:16px; flex-wrap:wrap; padding-bottom:18px;
    border-bottom:1px solid var(--line); margin-bottom:18px;
  }
  .brand .eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--text-dim)}
  .brand h1{font-family:var(--display);font-weight:700;font-size:28px;margin:2px 0 0;letter-spacing:.01em}
  .brand h1 span{color:var(--amber)}
  .brand p{margin:4px 0 0;color:var(--text-dim);font-size:12.5px;max-width:52ch}

  .connection{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .status-pill{
    display:flex;align-items:center;gap:7px;font-size:11.5px;
    letter-spacing:.06em;text-transform:uppercase;padding:7px 12px;
    border-radius:20px;border:1px solid var(--line);background:var(--panel);
    color:var(--text-dim);
  }
  .status-pill .dot{width:8px;height:8px;border-radius:50%;background:var(--text-dim);transition:all .2s}
  .status-pill.on{color:var(--green);border-color:var(--green-dim)}
  .status-pill.on .dot{background:var(--green);box-shadow:0 0 8px 1px var(--green)}
  .status-pill.err{color:var(--red);border-color:var(--red-dim)}
  .status-pill.err .dot{background:var(--red);box-shadow:0 0 8px 1px var(--red)}

  button.btn{
    font-family:var(--mono);font-size:12px;letter-spacing:.04em;
    text-transform:uppercase;font-weight:600;border:1px solid var(--line);
    background:var(--panel-2);color:var(--text);padding:8px 14px;
    border-radius:7px;cursor:pointer;
    transition:border-color .15s,color .15s;
  }
  button.btn:hover{border-color:var(--amber);color:var(--amber)}
  button.btn:active{transform:translateY(1px)}
  button.btn:disabled{opacity:.35;cursor:not-allowed}
  button.btn.danger:hover{border-color:var(--red);color:var(--red)}

  .unsupported{
    background:var(--panel);border:1px solid var(--red-dim);border-radius:10px;
    padding:16px 18px;color:var(--text-dim);font-size:13px;line-height:1.55;
    margin-bottom:20px;
  }
  .unsupported b{color:var(--red)}

  /* Presets bar */
  .presets{
    display:flex;align-items:center;gap:10px;flex-wrap:wrap;
    background:var(--panel);border:1px solid var(--line);border-radius:12px;
    padding:12px 14px;margin-bottom:18px;
  }
  .presets .label{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-dim);margin-right:2px}
  select{
    font-family:var(--mono);font-size:12.5px;background:var(--groove);
    color:var(--text);border:1px solid var(--line);border-radius:7px;
    padding:8px 10px;
  }

  /* Modules */
  .modules{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:16px}
  .module{
    position:relative;
    background:linear-gradient(180deg,var(--panel) 0%,var(--panel-2) 100%);
    border:1px solid var(--line);border-radius:14px;padding:16px 16px 18px;
  }
  .module::before,.module::after{
    content:'';position:absolute;width:5px;height:5px;border-radius:50%;
    background:var(--groove);box-shadow:inset 0 1px 1px #000,0 0 0 1px #2a313b;
  }
  .module::before{top:10px;left:10px}
  .module::after{top:10px;right:10px}
  .module h2{
    font-family:var(--display);font-size:13px;font-weight:600;
    letter-spacing:.08em;text-transform:uppercase;margin:0 0 12px;
  }

  .ctl{margin-bottom:14px}
  .ctl:last-child{margin-bottom:0}
  .ctl .row{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
  .ctl .name{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--text-dim)}
  .ctl .value{font-size:12.5px;font-weight:600;color:var(--amber)}
  .ctl .help{font-size:10.5px;color:var(--text-dim);margin-top:5px;line-height:1.45}

  .switch{
    width:46px;height:24px;border-radius:5px;background:var(--groove);
    border:1px solid var(--line);position:relative;cursor:pointer;padding:2px;
    flex:none;
  }
  .switch .led{width:18px;height:18px;border-radius:4px;background:#2a313b;transition:all .15s}
  .switch.active{border-color:var(--amber-dim)}
  .switch.active .led{transform:translateX(20px);background:var(--amber);box-shadow:0 0 10px 1px rgba(255,180,84,.6)}

  input[type=range]{
    -webkit-appearance:none;appearance:none;width:100%;height:22px;
    background:transparent;cursor:pointer;margin:0;
  }
  input[type=range]::-webkit-slider-runnable-track{
    height:6px;border-radius:3px;background:var(--groove);
    border:1px solid #000;
  }
  input[type=range]::-webkit-slider-thumb{
    -webkit-appearance:none;width:16px;height:16px;border-radius:4px;
    background:var(--text);border:1px solid #000;margin-top:-6px;
    box-shadow:0 1px 3px rgba(0,0,0,.6);
  }
  input[type=range]:hover::-webkit-slider-thumb{background:var(--amber)}
  input[type=range]::-moz-range-track{height:6px;border-radius:3px;background:var(--groove);border:1px solid #000}
  input[type=range]::-moz-range-thumb{width:16px;height:16px;border-radius:4px;background:var(--text);border:1px solid #000}

  .enum-row{display:flex;gap:6px;flex-wrap:wrap}
  .enum-btn{
    background:var(--groove);border:1px solid var(--line);border-radius:7px;
    padding:7px 10px;font-size:11px;letter-spacing:.03em;color:var(--text-dim);
    cursor:pointer;font-family:var(--mono);
  }
  .enum-btn.active{border-color:var(--amber-dim);color:var(--amber);box-shadow:inset 0 0 0 1px rgba(255,180,84,.15)}

  .console{
    margin-top:18px;background:var(--groove);border:1px solid var(--line);
    border-radius:10px;padding:12px 14px;
  }
  .console-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
  .console-head .label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-dim)}
  .console .clear{font-size:10.5px;color:var(--text-dim);background:none;border:none;cursor:pointer;text-transform:uppercase}
  .console .clear:hover{color:var(--amber)}
  .log{height:120px;overflow-y:auto;font-size:11.5px;line-height:1.6;color:var(--text-dim)}
  .log .tx{color:var(--green)}
  .log .rx{color:var(--amber)}
  .log .sys{color:var(--text-dim);font-style:italic}
  .log .err{color:var(--red)}

  footer{text-align:center;color:var(--text-dim);font-size:11px;margin-top:22px;letter-spacing:.03em}
</style>
</head>
<body>
<div class="rack">

  <header>
    <div class="brand">
      <div class="eyebrow">8B8 Firmware Companion</div>
      <h1>AY <span>Panel</span></h1>
      <p>Generated from generate.py — __NUM_PARAMS__ parameters, live over USB serial. Changes apply immediately; SAVE persists them to the unit's EEPROM.</p>
    </div>
    <div class="connection">
      <div class="status-pill" id="statusPill"><span class="dot"></span><span id="statusText">Disconnected</span></div>
      <button class="btn" id="connectBtn">Connect</button>
      <button class="btn danger" id="disconnectBtn" disabled>Disconnect</button>
    </div>
  </header>

  <div class="unsupported" id="unsupported" style="display:none;">
    <b>Web Serial isn't available here.</b> This panel needs Chrome or Edge (desktop),
    opened as a local file — Safari and Firefox don't implement the Web Serial API yet.
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
  </div>

  <div class="modules" id="modules"></div>

  <div class="console">
    <div class="console-head">
      <span class="label">Serial Log</span>
      <button class="clear" id="clearLog">Clear</button>
    </div>
    <div class="log" id="log"></div>
  </div>

  <footer>8B8 — three AY-3-8910s, one Leonardo, no filters, no regrets.</footer>
</div>

<script>
/* ==== Generated data ==================================================== */
const PARAMS  = __PARAMS_JSON__;
const PRESETS = __PRESETS_JSON__;
const NUM_PARAMS = __NUM_PARAMS__;

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
    send('DUMP');
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
            .replace("__NUM_PARAMS__", str(len(PARAMS))))
    with open(path, "w") as f:
        f.write(html)
    print(f"wrote {path}  ({len(PRESETS)} presets)")


if __name__ == "__main__":
    out = os.path.dirname(os.path.abspath(__file__))
    normalise()
    emit_header(os.path.join(out, "parameters.h"))
    emit_html(os.path.join(out, "index.html"))
