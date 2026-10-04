#!/usr/bin/env python3
"""Give every SMD ground pad that DRC reports as unconnected its own via to the
In1 ground plane: a 0.6/0.3 via beside the pad plus a short stub.  Each via
is placed only where it clears all other copper by the board clearance.

    /usr/bin/python3 stitch.py        (reads DRC itself, edits 8b8_v2.kicad_pcb)
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projfile

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, '..', '8b8_v2.kicad_pcb')
mm = pcbnew.FromMM
CLEAR = mm(0.18)        # a little over the 0.15mm rule
VIA_D, VIA_DRILL, STUB_W = mm(0.6), mm(0.3), mm(0.3)
COPPER = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]


def drc_unconnected_gnd():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'drc.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error',
                        '-o', out, PCB], capture_output=True)
        r = json.load(open(out))
    pads = set()
    for item in r['unconnected_items']:
        for x in item['items']:
            m = re.match(r'Pad (\S+) \[GND\] of (\S+) on F\.Cu', x['description'])
            if m:
                pads.add((m.group(2), m.group(1)))
    return pads


def blocked(board, gnd, shape_via, shape_stub, layers_via, skip):
    """True if the via or stub would come within CLEAR of other-net copper."""
    for t in board.GetTracks():
        if t.GetNetCode() == gnd.GetNetCode():
            continue
        for layer in COPPER:
            if not t.IsOnLayer(layer):
                continue
            s = t.GetEffectiveShape(layer)
            if s.Collide(shape_via, CLEAR) or (layer == pcbnew.F_Cu and s.Collide(shape_stub, CLEAR)):
                return True
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p is skip:
                continue
            same = p.GetNetCode() == gnd.GetNetCode()
            for layer in COPPER:
                if not p.IsOnLayer(layer):
                    continue
                s = p.GetEffectiveShape(layer)
                # the via must clear other-net pads by the clearance, and must not
                # overlap a ground pad either (no via-in-pad)
                if s.Collide(shape_via, mm(0.02) if same else CLEAR):
                    return True
                if not same and layer == pcbnew.F_Cu and s.Collide(shape_stub, CLEAR):
                    return True
        # stay off the holes of through-hole parts / mounting holes
    return False


def main():
    board = pcbnew.LoadBoard(PCB)
    gnd = board.FindNet('GND')
    targets = drc_unconnected_gnd()
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    added, failed = 0, []
    for ref, num in sorted(targets):
        pad = next(p for p in fps[ref].Pads() if p.GetNumber() == num)
        c = pad.GetPosition()
        ok = False
        for dist in (0.75, 0.95, 1.2, 1.5, 1.9, 2.4, 3.0):
            for k in range(24):
                a = 2 * math.pi * k / 24
                v = pcbnew.VECTOR2I(int(c.x + mm(dist) * math.cos(a)), int(c.y + mm(dist) * math.sin(a)))
                sv = pcbnew.SHAPE_CIRCLE(v, VIA_D // 2)
                ss = pcbnew.SHAPE_SEGMENT(c, v, STUB_W)
                if blocked(board, gnd, sv, ss, COPPER, pad):
                    continue
                if not board.GetBoardEdgesBoundingBox().Contains(v):
                    continue
                via = pcbnew.PCB_VIA(board)
                via.SetPosition(v)
                via.SetWidth(VIA_D)
                via.SetDrill(VIA_DRILL)
                via.SetNet(gnd)
                board.Add(via)
                tr = pcbnew.PCB_TRACK(board)
                tr.SetStart(c)
                tr.SetEnd(v)
                tr.SetWidth(STUB_W)
                tr.SetLayer(pcbnew.F_Cu)
                tr.SetNet(gnd)
                board.Add(tr)
                ok = True
                break
            if ok:
                break
        if ok:
            added += 1
        else:
            failed.append(f'{ref}.{num}')
    # ---- grid stitching: tie every piece of the outer pours to the In1 plane
    for z in board.Zones():
        if z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu):
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    outer = [z for z in board.Zones() if z.GetNetname() == 'GND' and
             z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu)]
    edge = board.GetBoardEdgesBoundingBox()
    grid = 0
    step = mm(4.0)
    x = edge.GetLeft() + mm(2.0)
    while x < edge.GetRight() - mm(2.0):
        y = edge.GetTop() + mm(2.0)
        while y < edge.GetBottom() - mm(2.0):
            v = pcbnew.VECTOR2I(int(x), int(y))
            in_f = any(z.GetLayer() == pcbnew.F_Cu and z.HitTestFilledArea(pcbnew.F_Cu, v) for z in outer)
            in_b = any(z.GetLayer() == pcbnew.B_Cu and z.HitTestFilledArea(pcbnew.B_Cu, v) for z in outer)
            if in_f and in_b:
                sv = pcbnew.SHAPE_CIRCLE(v, VIA_D // 2)
                if not blocked(board, gnd, sv, pcbnew.SHAPE_SEGMENT(v, v, 1), COPPER, None) \
                        and not via_near(board, v):
                    via = pcbnew.PCB_VIA(board)
                    via.SetPosition(v)
                    via.SetWidth(VIA_D)
                    via.SetDrill(VIA_DRILL)
                    via.SetNet(gnd)
                    board.Add(via)
                    grid += 1
            y += step
        x += step
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(PCB, board)
    projfile.restore_erc()
    print(f'pad vias added: {added}; no room for: {failed}; grid stitching vias: {grid}')


def island_vias(board):
    """A via inside every filled piece of an outer GND pour that has none, so
    pockets walled in by traces still reach the In1 plane."""
    gnd = board.FindNet('GND')
    vias = [t.GetPosition() for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T
            and t.GetNetCode() == gnd.GetNetCode()]
    tht_gnd = [p for f in board.GetFootprints() for p in f.Pads()
               if p.GetNetCode() == gnd.GetNetCode() and p.GetDrillSizeX() > 0]
    added = 0
    for z in list(board.Zones()):
        if z.GetNetname() != 'GND' or z.GetLayer() not in (pcbnew.F_Cu, pcbnew.B_Cu):
            continue
        polys = z.GetFilledPolysList(z.GetLayer())
        for i in range(polys.OutlineCount()):
            ol = polys.Outline(i)

            def inside(v, i=i, ol=ol):     # in this outline and not in one of its holes
                return ol.PointInside(v) and not any(
                    polys.Hole(i, h).PointInside(v) for h in range(polys.HoleCount(i)))
            if any(inside(v) for v in vias) or any(
                    inside(p.GetPosition()) for p in tht_gnd):
                continue
            bb = ol.BBox()
            placed = False
            y = bb.GetTop() + VIA_D // 2
            while y < bb.GetBottom() and not placed:
                x = bb.GetLeft() + VIA_D // 2
                while x < bb.GetRight() and not placed:
                    v = pcbnew.VECTOR2I(int(x), int(y))
                    if inside(v):
                        sv = pcbnew.SHAPE_CIRCLE(v, VIA_D // 2)
                        if not blocked(board, gnd, sv, pcbnew.SHAPE_SEGMENT(v, v, 1), COPPER, None):
                            via = pcbnew.PCB_VIA(board)
                            via.SetPosition(v)
                            via.SetWidth(VIA_D)
                            via.SetDrill(VIA_DRILL)
                            via.SetNet(gnd)
                            board.Add(via)
                            vias.append(v)
                            added += 1
                            placed = True
                    x += mm(0.1)
                y += mm(0.1)
    return added


def via_near(board, v, dist=mm(1.0)):
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and (t.GetPosition() - v).EuclideanNorm() < dist:
            return True
    return False


if __name__ == '__main__' and '--island-tracks' not in sys.argv:
    if '--islands' in sys.argv:
        b = pcbnew.LoadBoard(PCB)
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        n = island_vias(b)
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        pcbnew.SaveBoard(PCB, b)
        projfile.restore_erc()
        print('island vias added:', n)
    else:
        main()


def island_tracks(board):
    """For a ground pocket too small for a via: route its ground pad to the
    nearest ground via with fixroute's A* router."""
    import fixroute
    gnd = board.FindNet('GND')
    vias = [t.GetPosition() for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T
            and t.GetNetCode() == gnd.GetNetCode()]
    pads = [p for f in board.GetFootprints() for p in f.Pads() if p.GetNetCode() == gnd.GetNetCode()]
    jobs = []
    for z in board.Zones():
        if z.GetNetname() != 'GND' or z.GetLayer() not in (pcbnew.F_Cu, pcbnew.B_Cu):
            continue
        polys = z.GetFilledPolysList(z.GetLayer())
        for i in range(polys.OutlineCount()):
            ol = polys.Outline(i)

            def inside(v, i=i, ol=ol):
                return ol.PointInside(v) and not any(
                    polys.Hole(i, h).PointInside(v) for h in range(polys.HoleCount(i)))
            if any(inside(v) for v in vias) or any(
                    inside(p.GetPosition()) for p in pads if p.GetDrillSizeX() > 0):
                continue
            mine = [p for p in pads if p.IsOnLayer(z.GetLayer()) and inside(p.GetPosition())]
            if mine:
                jobs.append(mine[0])
    done = 0
    for pad in jobs:
        a = pad.GetPosition()
        for v in sorted(vias, key=lambda v: (v - a).EuclideanNorm())[:6]:
            path = fixroute.Router(board, gnd).route((a.x, a.y), (v.x, v.y),
                                                     [pcbnew.F_Cu], list(fixroute.LAYERS))
            if not path:
                continue
            pts = [(n[0] * fixroute.GRID, n[1] * fixroute.GRID, n[2]) for n in path]
            pts[0] = (a.x, a.y, pts[0][2])
            pts[-1] = (v.x, v.y, pts[-1][2])
            for p, q in zip(pts, pts[1:]):
                if p[2] != q[2]:
                    nv = pcbnew.PCB_VIA(board)
                    nv.SetPosition(pcbnew.VECTOR2I(int(p[0]), int(p[1])))
                    nv.SetWidth(VIA_D)
                    nv.SetDrill(VIA_DRILL)
                    nv.SetNet(gnd)
                    board.Add(nv)
                elif (p[0], p[1]) != (q[0], q[1]):
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(int(p[0]), int(p[1])))
                    t.SetEnd(pcbnew.VECTOR2I(int(q[0]), int(q[1])))
                    t.SetWidth(fixroute.TRACK_W)
                    t.SetLayer(p[2])
                    t.SetNet(gnd)
                    board.Add(t)
            done += 1
            break
    return done, len(jobs)


if __name__ == '__main__' and '--island-tracks' in sys.argv:
    b = pcbnew.LoadBoard(PCB)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    d, n = island_tracks(b)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    projfile.restore_erc()
    print(f'island pads routed to a ground via: {d} of {n}')
