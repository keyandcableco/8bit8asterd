#!/usr/bin/env python3
"""Autoroute 8b8_v2.kicad_pcb with Freerouting, then fill the copper zones.

    /usr/bin/python3 route.py /path/to/freerouting-1.9.0.jar [passes]

Exports Specctra DSN, runs Freerouting headless, imports the session and
refills every zone.  The routed board overwrites 8b8_v2.kicad_pcb.
"""
import os
import subprocess
import sys
import tempfile

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projfile

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, '..', '8b8_v2.kicad_pcb')


def main():
    jar = sys.argv[1]
    passes = sys.argv[2] if len(sys.argv) > 2 else '40'
    board = pcbnew.LoadBoard(PCB)
    # Pours on routing layers look like solid copper to the router: drop them
    # (they are re-added after import).  Existing tracks are kept, so this also
    # works as a second pass over a partly routed board.
    # (Delete, not Remove: Remove leaves orphaned SWIG wrappers that break later
    # zone objects in the same process.)
    for z in [z for z in board.Zones() if z.GetLayer() != pcbnew.In1_Cu]:
        board.Delete(z)
    work = tempfile.mkdtemp(prefix='8b8route-')
    dsn = os.path.join(work, '8b8_v2.dsn')
    ses = os.path.join(work, '8b8_v2.ses')
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise SystemExit('DSN export failed')
    # In1 is the solid ground plane: tell the router it is not a routing layer.
    with open(dsn) as f:
        txt = f.read()
    import re
    txt, n = re.subn(r'(\(layer In1\.Cu\s*\(type )signal\)', r'\1power)', txt)
    if n != 1:
        raise SystemExit('could not mark In1.Cu as a plane layer in the DSN')
    with open(dsn, 'w') as f:
        f.write(txt)
    print('DSN:', dsn, flush=True)
    # Freerouting 1.9 needs a display (it shows its progress window, then saves
    # the session and exits).  -da turns off its usage analytics.
    cmd = ['java', '-jar', jar, '-de', dsn, '-do', ses, '-mp', passes, '-da', '-dct', '0']
    print(' '.join(cmd), flush=True)
    subprocess.run(cmd, check=True, timeout=7200)
    if not os.path.exists(ses):
        raise SystemExit('Freerouting produced no session file')
    if not pcbnew.ImportSpecctraSES(board, ses):
        raise SystemExit('SES import failed')
    import gen_pcb
    gen_pcb.add_outer_pours(board)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(PCB, board)
    projfile.restore_erc()
    print('routed board saved:', os.path.normpath(PCB), ' tracks:', len(board.GetTracks()))


if __name__ == '__main__':
    main()
