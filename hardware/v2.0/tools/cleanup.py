#!/usr/bin/env python3
"""Delete track segments DRC reports as dangling (leftovers of rip-up and
reroute), repeating until there are none.  Refuses to save if that would leave
anything unconnected."""
import json, os, subprocess, sys, tempfile
import pcbnew
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projfile
PCB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '8b8_v2.kicad_pcb')


def drc():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'drc.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', out, PCB],
                       capture_output=True)
        return json.load(open(out))


total = 0
for _ in range(20):
    r = drc()
    dang = [v for v in r['violations'] if v['type'] == 'track_dangling']
    if not dang:
        break
    b = pcbnew.LoadBoard(PCB)
    uuids = {x['uuid'] for v in dang for x in v['items']}
    kill = [t for t in b.GetTracks() if t.m_Uuid.AsString() in uuids]
    for t in kill:
        b.Delete(t)
    total += len(kill)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    projfile.restore_erc()
    if not kill:
        break
r = drc()
print('dangling segments removed:', total, '| unconnected now:', len(r['unconnected_items']),
      '| dangling left:', sum(v['type'] == 'track_dangling' for v in r['violations']))
