#!/usr/bin/env python3
"""Check that every pad on 8b8_v2.kicad_pcb carries the net design.py says,
then run KiCad's DRC and summarise it.  /usr/bin/python3 check_pcb.py"""
import json, os, subprocess, sys, tempfile
import pcbnew
sys.path.insert(0, os.path.dirname(__file__))
import design
from gen_pcb import sch_netmap

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, '..', '8b8_v2.kicad_pcb')
board = pcbnew.LoadBoard(PCB)
smap = sch_netmap()
bad = 0
for fp in board.GetFootprints():
    ref = fp.GetReference()
    for pad in fp.Pads():
        n = pad.GetNumber()
        if not n:
            continue
        w = smap.get((ref, n), '')
        if pad.GetNetname() != w:
            bad += 1
            print(f'NET MISMATCH {ref}.{n}: board {pad.GetNetname()!r}, design {w!r}')
print(f'pad nets: {bad} mismatches')

with tempfile.TemporaryDirectory() as d:
    rpt = os.path.join(d, 'drc.json')
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                    '--schematic-parity', '-o', rpt, PCB], capture_output=True)
    r = json.load(open(rpt))
from collections import Counter
for key in ('violations', 'unconnected_items', 'schematic_parity'):
    items = r.get(key, [])
    c = Counter((i['severity'], i['type']) for i in items)
    print(f'{key}: {len(items)}')
    for (sev, t), k in sorted(c.items()):
        print(f'   {k:4} {sev:8} {t}')
if '-v' in sys.argv:
    for key in ('violations', 'unconnected_items', 'schematic_parity'):
        for i in r.get(key, [])[:40]:
            print(key, i['type'], '|', ' / '.join(x['description'] for x in i['items'])[:200])
