#!/usr/bin/env python3
"""Write the JLCPCB order package into hardware/v2.0/fab/:

    8b8_v2_gerbers.zip   gerbers + drill files (upload as the PCB)
    8b8_v2_bom.csv       Comment, Designator, Footprint, LCSC Part #
    8b8_v2_cpl.csv       Designator, Mid X, Mid Y, Layer, Rotation

Only SMD parts go in the assembly files. The through-hole parts (DIP-40
sockets, terminal blocks) are listed in fab/hand_solder.txt: either add
them to the JLC order as through-hole assembly, or solder them yourself.
"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
import design

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, '..', '8b8_v2.kicad_pcb')
FAB = os.path.join(HERE, '..', 'fab')
THT = ('Package_DIP:DIP-40', 'Connector_Phoenix_MC:')
SKIP = ('TP_', 'H')


def kicad(*args):
    subprocess.run(['kicad-cli', *args], check=True, capture_output=True)


def main():
    os.makedirs(FAB, exist_ok=True)
    tmp = tempfile.mkdtemp()
    layers = 'F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts'
    kicad('pcb', 'export', 'gerbers', '--layers', layers, '--subtract-soldermask',
          '--no-x2', '-o', tmp + '/', PCB)
    kicad('pcb', 'export', 'drill', '--format', 'excellon', '--excellon-separate-th',
          '--generate-map', '--map-format', 'gerberx2', '-o', tmp + '/', PCB)
    zpath = os.path.join(FAB, '8b8_v2_gerbers.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(tmp)):
            z.write(os.path.join(tmp, f), f)

    # placement from the board itself
    pos = os.path.join(tmp, 'pos.csv')
    kicad('pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'both',
          '--exclude-dnp', '-o', pos, PCB)
    with open(pos) as f:
        rows = list(csv.DictReader(f))

    smd, hand = {}, []
    for ref, p in design.ordered():
        if ref.startswith(SKIP):
            continue
        if p['fp'].startswith(THT) or not p['lcsc']:
            hand.append((ref, p))
        else:
            smd[ref] = p

    with open(os.path.join(FAB, '8b8_v2_cpl.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        for r in rows:
            if r['Ref'] in smd:
                w.writerow([r['Ref'], r['PosX'] + 'mm', r['PosY'] + 'mm',
                            'Top' if r['Side'] == 'top' else 'Bottom', r['Rot']])

    groups = {}
    for ref, p in smd.items():
        groups.setdefault((p['value'], p['fp'].split(':')[1], p['lcsc']), []).append(ref)
    with open(os.path.join(FAB, '8b8_v2_bom.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC Part #'])
        for (val, fp, lcsc), refs in sorted(groups.items(), key=lambda g: g[1][0]):
            w.writerow([val, ','.join(sorted(refs, key=lambda r: (r.rstrip('0123456789'), int(r[len(r.rstrip('0123456789')):] or 0)))), fp, lcsc])

    with open(os.path.join(FAB, 'hand_solder.txt'), 'w') as f:
        f.write('Through-hole parts, not in the SMT assembly files.\n'
                'Add them to the JLC order as through-hole assembly, or solder by hand.\n\n')
        for ref, p in hand:
            f.write(f'{ref:5} {p["value"]:20} {p["fp"].split(":")[1]:58} LCSC {p["lcsc"]}\n')
        f.write('\nPlus your three YM2149 / AY-3-8910 chips in U7, U8, U9 (pin 1 at the square pad).\n')
        f.write('The terminal blocks are pluggable headers: buy matching 3.5 mm plugs\n'
                '(DORABO DB2EK-3.5-10P-GN-S, LCSC C395846, x2; DB2EK-3.5-2P-GN-S,\n'
                'LCSC C395828, x2) for the wires.\n')
    shutil.rmtree(tmp)
    print('wrote', os.path.normpath(FAB), '-', len(smd), 'SMD parts in',
          len(groups), 'BOM lines;', len(hand), 'through-hole parts')


if __name__ == '__main__':
    main()
