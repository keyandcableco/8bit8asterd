#!/usr/bin/env python3
"""Compare the netlist KiCad extracts from 8b8_v2.kicad_sch with design.py.
Exit status 0 only if every net has exactly the same pins."""
import os, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(__file__))
import design

here = os.path.dirname(os.path.abspath(__file__))
sch = os.path.join(here, '..', '8b8_v2.kicad_sch')
with tempfile.TemporaryDirectory() as d:
    out = os.path.join(d, 'n.xml')
    subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', out, sch],
                   check=True, capture_output=True)
    root = ET.parse(out).getroot()
got = {}
for net in root.find('nets'):
    name = net.get('name').lstrip('/')
    got[name] = sorted((n.get('ref'), n.get('pin')) for n in net)
want = {}
for name, nodes in design.nets().items():
    if name.startswith('NC_') or name.startswith('SBU'):
        continue
    want[name] = sorted(nodes)
# KiCad names unconnected single-pin nets Net-(...) / unconnected-(...): ignore those
got = {k: v for k, v in got.items() if not k.startswith(('unconnected-', 'Net-('))}
bad = 0
for k in sorted(set(want) | set(got)):
    if want.get(k) != got.get(k):
        bad += 1
        print('MISMATCH', k)
        w, g = set(map(tuple, want.get(k, []))), set(map(tuple, got.get(k, [])))
        print('   only in design   :', sorted(w - g))
        print('   only in schematic:', sorted(g - w))
print(f'{len(want)} nets in design, {len(got)} in schematic, {bad} mismatches')
sys.exit(1 if bad else 0)
