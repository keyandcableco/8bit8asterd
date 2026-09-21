#!/bin/bash
# Builds the firmware + chip emulator to WebAssembly for the web page.
#
# Needs Emscripten once:
#   git clone https://github.com/emscripten-core/emsdk
#   cd emsdk && ./emsdk install latest && ./emsdk activate latest
#   source ./emsdk_env.sh
set -e
cd "$(dirname "$0")"
command -v em++ >/dev/null || { echo "em++ not found - source emsdk_env.sh first"; exit 1; }

em++ -std=c++17 -O3 -x c++ -I. -I.. \
  -Wno-narrowing -Wno-write-strings \
  host.cpp \
  -o 8b8.js \
  -s EXPORTED_FUNCTIONS='["_emu_init","_emu_render","_emu_note_on","_emu_note_off","_emu_cc","_emu_send_line","_emu_read_lines","_emu_seq_row","_emu_seq_cell","_emu_seq_clear","_emu_seq_start","_emu_seq_stop","_emu_seq_tempo","_emu_seq_swing","_emu_seq_step","_emu_reg","_emu_voice_playing","_malloc","_free"]' \
  -s EXPORTED_RUNTIME_METHODS='["ccall","cwrap","HEAPF32"]' \
  -s ALLOW_MEMORY_GROWTH=1 \
  -s MODULARIZE=0 \
  -s ENVIRONMENT=web

echo
echo "built 8b8.js + 8b8.wasm"
echo "serve this folder and open emulator.html, e.g.:"
echo "    python3 -m http.server 9111"
echo "then open http://localhost:9111/emulator.html"
