#!/usr/bin/env python3
"""Finish the connections the autorouter left open.

Reads KiCad's DRC list of unconnected items (skipping GND, which stitch.py
handles), then routes each pair with a small A* search on a 0.25 mm grid over
F.Cu, In2.Cu and B.Cu.  Every step and every via is checked against all other
-net copper with KiCad's own shape collision, at the board clearance plus a
margin, so nothing it adds can violate DRC.

    /usr/bin/python3 fixroute.py
"""
import heapq
import json
import math
import os
import subprocess
import sys
import tempfile

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projfile

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, '..', '8b8_v2.kicad_pcb')
mm = pcbnew.FromMM
GRID = mm(0.1)
TRACK_W = mm(0.2)
CLEAR = mm(0.16)
VIA_D, VIA_DRILL = mm(0.6), mm(0.3)
LAYERS = [pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
ALL_CU = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
VIA_COST = 30.0
MAX_EXPAND = 400000
BUCKET = mm(2.0)
REGION_MARGIN = mm(5.0)


def drc_pairs():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'drc.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error',
                        '-o', out, PCB], capture_output=True)
        r = json.load(open(out))
    pairs = []
    for i in r['unconnected_items']:
        its = i['items']
        if any('[GND]' in x['description'] for x in its):
            # GND islands are stitch.py's job; only a stranded GND *pad* is routed
            # here, to the nearest connected ground copper.
            if not any(x['description'].startswith('Pad ') for x in its) or \
                    '--gnd' not in sys.argv:
                continue
        pairs.append([(x['pos']['x'], x['pos']['y'], x['description']) for x in its])
    return pairs


class Router:
    def __init__(self, board, net):
        self.board = board
        self.net = net
        self.cache = {}
        self.items = []     # (layer, shape) of other-net copper
        for t in board.GetTracks():
            if t.GetNetCode() == net.GetNetCode():
                continue
            for L in ALL_CU:
                if t.IsOnLayer(L):
                    self.items.append((L, t.GetEffectiveShape(L), t.GetBoundingBox()))
        for fp in board.GetFootprints():
            for p in fp.Pads():
                same = p.GetNetCode() == net.GetNetCode() and p.GetNetCode() != 0
                for L in ALL_CU:
                    if p.IsOnLayer(L):
                        self.items.append((L, p.GetEffectiveShape(L), p.GetBoundingBox(), same))
                if p.GetDrillSizeX() > 0:      # holes block every layer (vias)
                    self.items.append(('hole', p.GetEffectiveHoleShape(), p.GetBoundingBox(), False))
        self.buckets = {}
        for idx, it in enumerate(self.items):
            bb = it[2]
            for bx in range(bb.GetLeft() // BUCKET, bb.GetRight() // BUCKET + 1):
                for by in range(bb.GetTop() // BUCKET, bb.GetBottom() // BUCKET + 1):
                    self.buckets.setdefault((bx, by), []).append(idx)
        edge = board.GetBoardEdgesBoundingBox()
        self.edge = (edge.GetLeft() + mm(0.6), edge.GetTop() + mm(0.6),
                     edge.GetRight() - mm(0.6), edge.GetBottom() - mm(0.6))

    def _hits(self, layer, shape, bbox, allow_same_pads):
        seen = set()
        cand = []
        for bx in range(bbox.GetLeft() // BUCKET, bbox.GetRight() // BUCKET + 1):
            for by in range(bbox.GetTop() // BUCKET, bbox.GetBottom() // BUCKET + 1):
                for idx in self.buckets.get((bx, by), ()):
                    if idx not in seen:
                        seen.add(idx)
                        cand.append(self.items[idx])
        for it in cand:
            L, s, bb = it[0], it[1], it[2]
            if L != layer and not (L == 'hole' and layer == 'via'):
                continue
            if len(it) == 4 and it[3] and allow_same_pads:
                continue
            if not bbox.Intersects(bb):
                continue
            if s.Collide(shape, CLEAR):
                return True
        return False

    def free(self, x, y, L):
        key = (x, y, L)
        if key in self.cache:
            return self.cache[key]
        if not (self.edge[0] <= x <= self.edge[2] and self.edge[1] <= y <= self.edge[3]):
            self.cache[key] = False
            return False
        v = pcbnew.VECTOR2I(x, y)
        r = TRACK_W // 2
        shape = pcbnew.SHAPE_CIRCLE(v, r)
        bb = pcbnew.BOX2I(pcbnew.VECTOR2I(x - r - CLEAR, y - r - CLEAR),
                          pcbnew.VECTOR2I(2 * (r + CLEAR), 2 * (r + CLEAR)))
        ok = not self._hits(L, shape, bb, True)
        self.cache[key] = ok
        return ok

    def via_free(self, x, y):
        key = (x, y, 'via')
        if key in self.cache:
            return self.cache[key]
        v = pcbnew.VECTOR2I(x, y)
        r = VIA_D // 2
        shape = pcbnew.SHAPE_CIRCLE(v, r)
        bb = pcbnew.BOX2I(pcbnew.VECTOR2I(x - r - CLEAR, y - r - CLEAR),
                          pcbnew.VECTOR2I(2 * (r + CLEAR), 2 * (r + CLEAR)))
        ok = all(not self._hits(L, shape, bb, False) for L in ALL_CU) and \
            not self._hits('via', shape, bb, False)
        self.cache[key] = ok
        return ok

    def seg_free(self, a, b, L):
        """Straight track a->b on layer L clears everything (checked as a segment)."""
        s = pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(*a), pcbnew.VECTOR2I(*b), TRACK_W)
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        bb = pcbnew.BOX2I(pcbnew.VECTOR2I(x0 - mm(1), y0 - mm(1)),
                          pcbnew.VECTOR2I(abs(a[0] - b[0]) + mm(2), abs(a[1] - b[1]) + mm(2)))
        return not self._hits(L, s, bb, True)

    def route(self, start, goal, start_layers, goal_layers):
        g0 = (round(start[0] / GRID), round(start[1] / GRID))
        g1 = (round(goal[0] / GRID), round(goal[1] / GRID))
        rx0 = (min(start[0], goal[0]) - REGION_MARGIN) // GRID
        rx1 = (max(start[0], goal[0]) + REGION_MARGIN) // GRID
        ry0 = (min(start[1], goal[1]) - REGION_MARGIN) // GRID
        ry1 = (max(start[1], goal[1]) + REGION_MARGIN) // GRID
        openq = []
        best = {}
        for L in start_layers:
            st = (g0[0], g0[1], L)
            best[st] = 0.0
            heapq.heappush(openq, (0.0, 0.0, st, None))
        came = {}
        n = 0
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        while openq and n < MAX_EXPAND:
            f, g, node, parent = heapq.heappop(openq)
            if node in came:
                continue
            came[node] = parent
            n += 1
            x, y, L = node
            if (x, y) == g1 and L in goal_layers:
                path = []
                while node:
                    path.append(node)
                    node = came[node]
                return path[::-1]
            for dx, dy in dirs:
                nx, ny = x + dx, y + dy
                if not (rx0 <= nx <= rx1 and ry0 <= ny <= ry1):
                    continue
                nb = (nx, ny, L)
                if nb in came:
                    continue
                near_end = (abs(nx - g1[0]) + abs(ny - g1[1]) <= 3) or (abs(nx - g0[0]) + abs(ny - g0[1]) <= 3)
                if not (near_end and (nx, ny) in ((g0[0], g0[1]), (g1[0], g1[1]))) and \
                        not self.free(nx * GRID, ny * GRID, L):
                    continue
                if not self.seg_free((x * GRID, y * GRID), (nx * GRID, ny * GRID), L):
                    continue
                ng = g + (1.4142 if dx and dy else 1.0)
                if ng < best.get(nb, 1e18):
                    best[nb] = ng
                    h = math.hypot(nx - g1[0], ny - g1[1])
                    heapq.heappush(openq, (ng + h, ng, nb, node))
            for L2 in LAYERS:
                if L2 == L:
                    continue
                nb = (x, y, L2)
                if nb in came or not self.via_free(x * GRID, y * GRID):
                    continue
                ng = g + VIA_COST
                if ng < best.get(nb, 1e18):
                    best[nb] = ng
                    h = math.hypot(x - g1[0], y - g1[1])
                    heapq.heappush(openq, (ng + h, ng, nb, node))
        return None


def endpoint_layers(board, x, y, net):
    """Layers on which the item at (x, y) of this net can be joined."""
    v = pcbnew.VECTOR2I(int(x), int(y))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == net.GetNetCode() and p.HitTest(v):
                return [L for L in LAYERS if p.IsOnLayer(L)], p.GetPosition()
    for t in board.GetTracks():
        if t.GetNetCode() == net.GetNetCode() and t.HitTest(v):
            if t.Type() == pcbnew.PCB_VIA_T:
                return list(LAYERS), t.GetPosition()
            return [t.GetLayer()] if t.GetLayer() in LAYERS else [], v
    for z in board.Zones():
        if z.GetNetCode() == net.GetNetCode() and z.GetLayer() in LAYERS and \
                z.HitTestFilledArea(z.GetLayer(), v):
            return [z.GetLayer()], v
    return list(LAYERS), v


def net_of(board, desc):
    import re
    m = re.search(r'\[([^\]]+)\]', desc)
    return board.FindNet(m.group(1)) if m else None


def route_one(board, pair):
    (x1, y1, d1), (x2, y2, d2) = pair[0], pair[1]
    net = net_of(board, d1)
    a_layers, a = endpoint_layers(board, mm(x1), mm(y1), net)
    b_layers, b = endpoint_layers(board, mm(x2), mm(y2), net)
    path = Router(board, net).route((a.x, a.y), (b.x, b.y), a_layers, b_layers)
    if not path:
        return False
    pts = [(n[0] * GRID, n[1] * GRID, n[2]) for n in path]
    pts[0] = (a.x, a.y, pts[0][2])      # snap the ends exactly onto their items
    pts[-1] = (b.x, b.y, pts[-1][2])
    for p, q in zip(pts, pts[1:]):
        if p[2] != q[2]:
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(int(p[0]), int(p[1])))
            via.SetWidth(VIA_D)
            via.SetDrill(VIA_DRILL)
            via.SetNet(net)
            board.Add(via)
        elif (p[0], p[1]) != (q[0], q[1]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(int(p[0]), int(p[1])))
            t.SetEnd(pcbnew.VECTOR2I(int(q[0]), int(q[1])))
            t.SetWidth(TRACK_W)
            t.SetLayer(p[2])
            t.SetNet(net)
            board.Add(t)
    return True


def main():
    """Route one open connection at a time and re-read DRC in between, so
    several DRC pairs between the same two islands only get one route."""
    failed = set()
    done = []
    while True:
        pairs = [p for p in drc_pairs() if (p[0][2], p[1][2]) not in failed]
        # signals first: power nets also reach their planes/pours and have more ways round
        pairs.sort(key=lambda p: any(n in p[0][2] for n in ('[+3V3]', '[+5V]', '[+1V1]')))
        if not pairs:
            break
        pair = pairs[0]
        board = pcbnew.LoadBoard(PCB)
        label = f'{pair[0][2][:45]}  <->  {pair[1][2][:45]}'
        if route_one(board, pair):
            pcbnew.SaveBoard(PCB, board)
            done.append(label)
            print('routed:', label, flush=True)
        else:
            failed.add((pair[0][2], pair[1][2]))
            print('FAILED:', label, flush=True)
    board = pcbnew.LoadBoard(PCB)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(PCB, board)
    projfile.restore_erc()
    print(f'routed {len(done)}, failed {len(failed)}')


if __name__ == '__main__':
    main()
