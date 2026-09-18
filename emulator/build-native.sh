#!/bin/bash
# Native build, for the tests. Same source the browser build uses.
set -e
cd "$(dirname "$0")"
g++ -std=c++17 -O2 -x c++ -I. -I.. \
    -Wno-narrowing -Wno-write-strings \
    host.cpp test_main.cpp -o /tmp/8b8emu
echo "built /tmp/8b8emu"
