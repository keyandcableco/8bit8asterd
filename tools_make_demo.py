#!/usr/bin/env python3
"""Writes demo/invention.mid, an original two-voice piece in a baroque style.

Generated rather than transcribed: a notated piece by a known composer is
public domain, but the MIDI transcription of it is its own work with its own
rights, and one of unknown provenance is not safe to redistribute. This is
written here from scratch, so it ships under the repository's own licence.

Three voices, which suits the instrument: a subject in the right hand, the
same subject answered a fifth below, and a walking bass. Kept inside two
octaves so the AY's coarse tone divider stays in tune.
"""
import struct

DIV = 480                      # ticks per quarter
BPM = 96
MAJOR = [0, 2, 4, 5, 7, 9, 11]

def deg(n):
    """Scale degree n (0 = tonic) to a semitone offset, any octave."""
    return 12 * (n // 7) + MAJOR[n % 7]

# The subject: an eight-note figure, stepwise with one leap, that inverts well.
SUBJECT = [0, 2, 4, 2, 5, 4, 2, 0]
COUNTER = [7, 6, 4, 5, 3, 2, 1, 0]

def line(root, pattern, start, dur, transpose=0):
    """Notes as (tick, pitch, length) from a degree pattern."""
    out, t = [], start
    for d in pattern:
        out.append((t, root + deg(d) + transpose, dur))
        t += dur
    return out

E = DIV // 2                   # eighth note
Q = DIV

right, left, bass = [], [], []

# Exposition: subject alone, then answered a fifth below while the first
# voice continues in counterpoint.
right += line(72, SUBJECT, 0, E)
right += line(72, COUNTER, 8 * E, E)
left  += line(65, SUBJECT, 8 * E, E)

# Development: the subject a step up, both voices, bass walking underneath.
right += line(74, SUBJECT, 16 * E, E)
left  += line(67, COUNTER, 16 * E, E)
right += line(71, COUNTER, 24 * E, E)
left  += line(64, SUBJECT, 24 * E, E)

# Close: back to the tonic, the subject in longer notes over a held bass.
right += line(72, SUBJECT[:4], 32 * E, Q)
left  += line(60, SUBJECT[:4], 32 * E, Q)

for i, d in enumerate([0, 4, 3, 4, 0, 5, 4, 0]):
    bass.append((i * 4 * E, 48 + deg(d), 4 * E - 20))
bass.append((32 * E, 48, 4 * Q))

def vlq(n):
    out = [n & 0x7F]; n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80); n >>= 7
    return bytes(out)

def track(notes, chan, vel):
    evs = []
    for t, p, d in notes:
        evs.append((t, bytes([0x90 | chan, p, vel])))
        evs.append((t + d, bytes([0x80 | chan, p, 0])))
    evs.sort(key=lambda e: e[0])
    data, last = b'', 0
    for t, ev in evs:
        data += vlq(t - last) + ev
        last = t
    data += vlq(0) + bytes([0xFF, 0x2F, 0x00])
    return b'MTrk' + struct.pack('>I', len(data)) + data

tempo = int(60_000_000 / BPM)
meta = b''.join([
    vlq(0), bytes([0xFF, 0x51, 0x03]) + tempo.to_bytes(3, 'big'),
    vlq(0), bytes([0xFF, 0x2F, 0x00]),
])
head = b'MThd' + struct.pack('>IHHH', 6, 1, 4, DIV)
out = (head + b'MTrk' + struct.pack('>I', len(meta)) + meta
       + track(right, 0, 100) + track(left, 0, 88) + track(bass, 0, 78))

with open('demo/invention.mid', 'wb') as f:
    f.write(out)
print(f"wrote demo/invention.mid  {len(out)} bytes, {BPM} bpm, 3 voices")
