#!/usr/bin/env python3
"""Generate the 8b8 v2.0 board (placement, outline, planes, silkscreen) from
design.py, using KiCad's own pcbnew API.  Routing is done afterwards by
route.py (Freerouting); this script produces the unrouted board.

Run with the system Python that has KiCad's bindings:  /usr/bin/python3 gen_pcb.py
"""
import math
import os
import sys
import uuid

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projfile

sys.path.insert(0, os.path.dirname(__file__))
import design

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', '8b8_v2.kicad_pcb')
FPDIR = '/usr/share/kicad/footprints'
ROOT = uuid.UUID('8b8a2000-0000-4000-8000-000000000001')

OX, OY = 50.0, 40.0          # board origin on the KiCad page
W, H = 110.0, 90.0           # board size, mm
POWER_NETS = {'GND', '+5V', '+3V3', '+1V1'}
HOLES = [(3.5, 3.5), (W - 3.5, 3.5), (3.5, H - 3.5), (W - 3.5, H - 3.5)]

mm = pcbnew.FromMM


def P(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def netname(n):
    return n if n in POWER_NETS else '/' + n


# ---------------------------------------------------------------------------
# Hand placement of the parts that set the floor plan.  (x, y, rotation)
# Coordinates are the footprint origin, board-relative, mm.
# ---------------------------------------------------------------------------
SOCK_Y = 9.0
SOCK_X = [6.0, 26.5, 47.0]          # pin-1 x of chips A, B, C
PLACE = {
    'U7': (SOCK_X[0], SOCK_Y, 0), 'U8': (SOCK_X[1], SOCK_Y, 0), 'U9': (SOCK_X[2], SOCK_Y, 0),
    # level buffers, flipped so the 5V B-side faces the chips
    'U5': (71.5, 23.0, 180), 'U6': (71.5, 44.0, 180),
    # RP2040 and friends
    'U2': (88.0, 31.0, 0), 'U3': (89.5, 17.0, 0), 'Y1': (84.0, 41.5, 0),
    'J1': (105.6, 20.0, 90), 'U4': (96.5, 22.5, 90), 'U1': (99.0, 35.0, 0),
    'F1': (100.5, 12.0, 0),
    'SW1': (104.5, 44.0, 90), 'SW2': (104.5, 51.5, 90),
    'D1': (100.0, 56.5, 0), 'D2': (100.0, 59.0, 0), 'D3': (100.0, 61.5, 0),
    # bottom edge: terminals (wire entry faces the board edge)
    'J4': (10.0, 81.0, 0), 'J5': (48.0, 81.0, 0), 'J2': (85.8, 81.0, 0), 'J3': (95.5, 81.0, 0),
    # input chips above their terminals
    'U11': (27.0, 66.0, 90), 'U12': (65.0, 66.0, 90),
    'U10': (98.0, 69.5, 0),
}
for i, (x, y) in enumerate(HOLES):
    PLACE[f'H{i + 1}'] = (x, y, 0)

# test points: along the right edge and in the spare corner by the chips
PLACE.update({
    'TP_SWCLK': (108.0, 31.0, 0), 'TP_SWD': (108.0, 33.5, 0), 'TP_GND': (108.0, 36.0, 0),
    'TP_3V3': (108.0, 38.5, 0), 'TP_5V': (108.0, 59.0, 0),
    'TP_GP16': (93.0, 45.0, 0), 'TP_GP27': (95.5, 45.0, 0), 'TP_GP28': (93.0, 47.5, 0),
    'TP_GP29': (95.5, 47.5, 0),
})

# Decoupling capacitors: which pin each power-only cap belongs beside, in the
# order design.py creates them (per section).
DECOUPLE = {
    ('mcu', '+3V3'): [('U2', p) for p in ('1', '10', '22', '33', '42', '49', '48', '43', '44')] +
                     [('U3', '8')],
    ('mcu', '+1V1'): [('U2', '23'), ('U2', '50'), ('U2', '45')],
    ('buffers', '+5V'): [('U5', '20'), ('U6', '20')],
    ('midi', '+5V'): [('U10', '8')],
    ('inputs', '+3V3'): [('U11', '16'), ('U12', '16')],
    ('power', '+5V'): [('F1', '2'), ('U5', '1'), ('U6', '1'), ('U1', '1')],
    ('power', '+3V3'): [('U1', '5')],
}

_io = pcbnew.PCB_IO_MGR.PluginFind(pcbnew.PCB_IO_MGR.KICAD_SEXP)


def load_fp(fpid):
    lib, name = fpid.split(':')
    fp = _io.FootprintLoad(os.path.join(FPDIR, lib + '.pretty'), name)
    if fp is None:
        raise SystemExit('footprint not found: ' + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def courtyard(fp, margin=0.15):
    fp.BuildCourtyardCaches()
    c = fp.GetCourtyard(pcbnew.F_CrtYd if not fp.IsFlipped() else pcbnew.B_CrtYd)
    bb = c.BBox() if c.OutlineCount() else fp.GetBoundingBox(False)
    m = mm(margin)
    return (bb.GetLeft() - m, bb.GetTop() - m, bb.GetRight() + m, bb.GetBottom() + m)


def overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def inside_board(bb, edge=0.6):
    return (bb[0] >= mm(OX + edge) and bb[1] >= mm(OY + edge) and
            bb[2] <= mm(OX + W - edge) and bb[3] <= mm(OY + H - edge))


def pad_pos(board_fps, ref, pin):
    for p in board_fps[ref].Pads():
        if p.GetNumber() == pin:
            v = p.GetPosition()
            return pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY
    raise KeyError((ref, pin))


def sch_netmap():
    """(ref, pin) -> net name exactly as KiCad derives it from the schematic, so
    the board passes schematic-parity DRC (including unconnected-(...) nets)."""
    import subprocess, tempfile
    import xml.etree.ElementTree as ET
    sch = os.path.join(HERE, '..', '8b8_v2.kicad_sch')
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'n.xml')
        subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                        '-o', out, sch], check=True, capture_output=True)
        root = ET.parse(out).getroot()
    m = {}
    for net in root.find('nets'):
        for node in net:
            m[(node.get('ref'), node.get('pin'))] = net.get('name')
    return m


def build():
    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(4)
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(mm(1.6))

    # ---- net classes: JLC 4-layer capability is far below these ------------
    ns = ds.m_NetSettings
    dflt = ns.GetDefaultNetclass()
    dflt.SetClearance(mm(0.15))
    dflt.SetTrackWidth(mm(0.2))
    dflt.SetViaDiameter(mm(0.6))
    dflt.SetViaDrill(mm(0.3))
    pwr = pcbnew.NETCLASS('Power')
    # 0.25mm still reaches a 0.4mm-pitch QFN pin with 0.15mm to its neighbours,
    # and carries well over half an amp in 1oz copper.
    pwr.SetClearance(mm(0.15))
    pwr.SetTrackWidth(mm(0.25))
    pwr.SetViaDiameter(mm(0.7))
    pwr.SetViaDrill(mm(0.35))
    ns.SetNetclass('Power', pwr)
    for n in ('+5V', '+3V3', '+1V1', 'GND', '/VBUS', '/AUDIO', '/AUDIO_OUT'):
        ns.SetNetclassPatternAssignment(n, 'Power')

    # ---- nets ---------------------------------------------------------------
    smap = sch_netmap()
    netinfo = {}
    for name in sorted(set(smap.values())):
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        netinfo[name] = ni
    nets = {n: netinfo[netname(n)] for n in design.nets() if netname(n) in netinfo}

    # ---- footprints -----------------------------------------------------------
    fps = {}
    for ref, p in design.ordered():
        fp = load_fp(p['fp'])
        fp.SetReference(ref)
        fp.SetValue(p['value'])
        fp.SetPath(pcbnew.KIID_PATH('/' + str(uuid.uuid5(ROOT, 'sym:' + ref))))
        if p['lcsc']:
            fld = pcbnew.PCB_FIELD(fp, fp.GetNextFieldId(), 'LCSC')
            fld.SetText(p['lcsc'])
            fld.SetVisible(False)
            fld.SetLayer(pcbnew.F_Fab)
            fp.AddField(fld)
        padnums = {pd.GetNumber() for pd in fp.Pads()}
        for pin, net in p['pins'].items():
            if pin not in padnums:
                raise SystemExit(f'{ref}: pin {pin} has no pad in {p["fp"]} (pads {sorted(padnums)})')
        for pd in fp.Pads():
            pin = pd.GetNumber()
            name = smap.get((ref, pin))
            if name is None:
                continue
            want = p['pins'].get(pin)
            if want and not want.startswith(('NC_', 'SBU')) and name != netname(want):
                raise SystemExit(f'{ref}.{pin}: schematic net {name} != design {want}')
            pd.SetNet(netinfo[name])
        if ref == 'J1':     # USB shield tabs: solid to the ground pour
            for pd in fp.Pads():
                if pd.GetNumber() == 'S1':
                    pd.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        if ref[0] in 'RCF' and not ref.startswith('TP_'):
            # passives are too dense for readable silkscreen: their references
            # stay on the fab layer (assembly drawing) instead
            fp.Reference().SetLayer(pcbnew.F_Fab)
        if ref.startswith('TP_') or ref.startswith('H'):
            fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM)
            fp.Value().SetVisible(False)
        if ref.startswith('TP_'):
            fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
            fp.Reference().SetTextThickness(mm(0.12))
        fps[ref] = fp

    # ---- hand placement -------------------------------------------------------
    placed_bbs = []
    for ref, (x, y, r) in PLACE.items():
        fp = fps[ref]
        fp.SetPosition(P(x, y))
        fp.SetOrientationDegrees(r)
        board.Add(fp)
        # keep a clear ring round the RP2040 so its pins can fan out
        placed_bbs.append((ref, courtyard(fp, 1.2 if ref == 'U2' else 0.15)))

    # ---- chip decoupling: 100nF just above each socket, pin 1 (GND) to pin 40 (VCC)
    ay_caps = [r for r, p in design.ordered() if p['section'] == 'ay' and r.startswith('C')]
    for cref, sx in zip(ay_caps, SOCK_X):
        fp = fps[cref]
        fp.SetPosition(P(sx + 7.62, SOCK_Y - 3.0))
        fp.SetOrientationDegrees(0)
        board.Add(fp)
        placed_bbs.append((cref, courtyard(fp)))

    # ---- automatic placement of the remaining small parts ----------------------
    queues = {k: list(v) for k, v in DECOUPLE.items()}
    remaining = [r for r, _ in design.ordered() if r not in PLACE and r not in ay_caps]

    def anchor_for(ref):
        p = design.PARTS[ref]
        signal = [n for n in p['pins'].values() if n not in POWER_NETS and not n.startswith('NC_')]
        if not signal:
            power = [n for n in p['pins'].values() if n != 'GND'] or ['GND']
            q = queues.get((p['section'], power[0]))
            if q:
                tref, tpin = q.pop(0)
                return pad_pos(fps, tref, tpin)
            return None
        pts = []
        for n in signal:
            best = None
            for oref, op in design.PARTS.items():
                if oref == ref or oref not in PLACE:
                    continue
                for pin, on in op['pins'].items():
                    if on == n:
                        xy = pad_pos(fps, oref, pin)
                        pri = 0 if oref.startswith('U') else 1
                        if best is None or pri < best[0]:
                            best = (pri, xy)
            if best:
                pts.append(best[1])
        if not pts:
            return None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    unplaced = []
    for ref in remaining:
        a = anchor_for(ref)
        fp = fps[ref]
        board.Add(fp)
        if a is None:
            unplaced.append(ref)
            a = (W / 2, H / 2)
        ok = False
        step = 0.5
        for ring in range(0, 60):
            rad = ring * step
            n = max(1, int(2 * math.pi * rad / step)) if ring else 1
            for k in range(n):
                ang = 2 * math.pi * k / n
                x = round((a[0] + rad * math.cos(ang)) * 4) / 4
                y = round((a[1] + rad * math.sin(ang)) * 4) / 4
                for rot in (0, 90):
                    fp.SetPosition(P(x, y))
                    fp.SetOrientationDegrees(rot)
                    bb = courtyard(fp)
                    if not inside_board(bb):
                        continue
                    if any(overlap(bb, o) for _, o in placed_bbs):
                        continue
                    ok = True
                    break
                if ok:
                    break
            if ok:
                break
        if not ok:
            unplaced.append(ref)
        placed_bbs.append((ref, courtyard(fp)))

    # ---- outline ----------------------------------------------------------------
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    for i in range(4):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(P(*corners[i]))
        seg.SetEnd(P(*corners[(i + 1) % 4]))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(0.1))
        board.Add(seg)

    # ---- planes -----------------------------------------------------------------
    def zone(netn, layer, poly, priority=0):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(nets[netn])
        z.SetAssignedPriority(priority)
        z.SetLocalClearance(mm(0.25))
        z.SetMinThickness(mm(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in poly:
            ol.Append(mm(OX + x), mm(OY + y))
        board.Add(z)
        return z

    e = 0.5
    zone('GND', pcbnew.In1_Cu, [(e, e), (W - e, e), (W - e, H - e), (e, H - e)])
    # In2 is a routing layer during autorouting; add_outer_pours() later floods
    # what is left of it with +5V (chip side) and +3V3 (MCU / inputs side).
    # Outer-layer ground pours are added by route.py AFTER autorouting: the
    # router treats a pour as solid copper, so pouring first leaves it no layer.

    # ---- silkscreen ---------------------------------------------------------------
    def silk(txt, x, y, size=1.0, layer=pcbnew.F_SilkS, bold=False, rot=0):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(txt)
        t.SetPosition(P(x, y))
        t.SetLayer(layer)
        if layer == pcbnew.B_SilkS:
            t.SetMirrored(True)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(size * (0.2 if bold else 0.15)))
        t.SetTextAngleDegrees(rot)
        board.Add(t)

    B = pcbnew.B_SilkS
    jx = PLACE['J4'][0]
    for i, lab in enumerate(['1', '2', '3', '4', '5', '6', '7', '8', '3V3', 'GND']):
        silk(lab, jx + 3.5 * i, 77.6, 0.9)
    jx = PLACE['J5'][0]
    for i, lab in enumerate(['1', '2', '3', '4', '5', '6', '7', '8', 'GND', '3V3']):
        silk(lab, jx + 3.5 * i, 77.6, 0.9)
    silk('OUT  GND', PLACE['J2'][0] + 1.75, 77.4, 0.8)
    silk('MIDI 5  4', PLACE['J3'][0] + 1.75, 77.4, 0.8)
    BX = 88.0
    silk('8-BIT 8ASTERD v2.0', BX, 52.0, 2.2, B, bold=True)
    silk('The Key & Cable Company', BX, 55.5, 1.2, B)
    silk('3x YM2149 / AY-3-8910', BX, 57.2, 1.0, B)
    for i, line in enumerate([
            'ANALOG 1-8: pot wipers; ends to 3V3 + GND',
            'SWITCH 1-8: to GND = on',
            'MIDI IN: DIN 5, DIN 4',
            'AUDIO OUT: line level',
            'Flash: hold BOOT, plug USB, copy .uf2']):
        silk(line, BX, 59.0 + 2.0 * i, 0.9, B)
    silk('A', SOCK_X[0] + 7.62, SOCK_Y + 26, 3.0, bold=True)
    silk('B', SOCK_X[1] + 7.62, SOCK_Y + 26, 3.0, bold=True)
    silk('C', SOCK_X[2] + 7.62, SOCK_Y + 26, 3.0, bold=True)
    silk('BOOT', PLACE['SW1'][0] - 4.0, PLACE['SW1'][1], 0.8, rot=0)
    silk('RESET', PLACE['SW2'][0] - 4.2, PLACE['SW2'][1], 0.8, rot=0)

    return board, unplaced


def add_outer_pours(board):
    """Post-routing pours: GND on F.Cu/B.Cu, +5V/+3V3 on In2."""
    gnd = board.FindNet('GND')
    e = 0.5
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetLocalClearance(mm(0.25))
        z.SetMinThickness(mm(0.2))
        # SMD pads solid (reflowed, and no starved thermals); through-hole pads
        # keep reliefs so the sockets and terminals stay easy to hand-solder.
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in [(e, e), (W - e, e), (W - e, H - e), (e, H - e)]:
            ol.Append(mm(OX + x), mm(OY + y))
        board.Add(z)
    # In2: +5V under the sound chips and buffers, +3V3 under the RP2040 and inputs,
    # flooded around whatever signals the router put on In2.
    for netn, poly in (('+5V', [(e, e), (78.0, e), (78.0, 60.0), (e, 60.0)]),
                       ('+3V3', [(78.0, e), (W - e, e), (W - e, H - e), (e, H - e),
                                 (e, 60.0), (78.0, 60.0)])):
        z = pcbnew.ZONE(board)
        z.SetLayer(pcbnew.In2_Cu)
        z.SetNet(board.FindNet(netn))
        z.SetLocalClearance(mm(0.25))
        z.SetMinThickness(mm(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in poly:
            ol.Append(mm(OX + x), mm(OY + y))
        board.Add(z)


def tidy_silk(board):
    """Keep reference text off pads, neighbours and the board edge."""
    def at(fp, dx, dy, just=None):
        r = fp.Reference()
        p = fp.GetPosition()
        r.SetPosition(pcbnew.VECTOR2I(p.x + mm(dx), p.y + mm(dy)))
        r.SetTextAngleDegrees(0)
        if just is not None:
            r.SetHorizJustify(just)
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        r = fp.Reference()
        if ref.startswith('TP_'):
            x = pcbnew.ToMM(fp.GetPosition().x) - OX
            if x > W - 6 or ref in ('TP_GP16', 'TP_GP28'):   # label to the left
                at(fp, -1.4, 0, pcbnew.GR_TEXT_H_ALIGN_RIGHT)
            else:
                at(fp, 1.4, 0, pcbnew.GR_TEXT_H_ALIGN_LEFT)
        elif ref in ('U7', 'U8', 'U9'):        # inside the socket, under the A/B/C
            at(fp, 7.62, 31.0)
        elif ref in ('U11', 'U12', 'U2', 'U5', 'U6', 'U3', 'U10'):
            at(fp, 0, 0)                       # centre of the body
        elif ref[0] in 'JYH' or ref.startswith('SW'):
            r.SetLayer(pcbnew.F_Fab)           # custom silk labels name these
        elif ref in ('D1', 'D2', 'D3'):        # show PWR / MIDI / STATUS instead
            r.SetLayer(pcbnew.F_Fab)
            v = fp.Value()
            v.SetLayer(pcbnew.F_SilkS)
            v.SetVisible(True)
            v.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
            v.SetTextThickness(mm(0.12))
            p = fp.GetPosition()
            v.SetPosition(pcbnew.VECTOR2I(p.x - mm(1.6), p.y))
            v.SetTextAngleDegrees(0)
            v.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)


def main():
    board, unplaced = build()
    tidy_silk(board)
    pcbnew.SaveBoard(OUT, board)
    projfile.restore_erc()
    print('wrote', os.path.normpath(OUT))
    if unplaced:
        print('COULD NOT PLACE:', unplaced)


if __name__ == '__main__':
    main()
