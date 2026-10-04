#!/usr/bin/env python3
"""Generate 8b8_v2.kicad_sch from design.py.

Every symbol pin gets a short wire stub ending in a net label (or a power
symbol for GND/+5V/+3V3/+1V1).  Unused pins get no-connect flags.  Nothing is
hand-drawn, so the schematic is exactly the netlist in design.py.
"""
import math
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
import design
import libsym
from sexp import Sym, dump, fmt_num

PROJECT = '8b8_v2'
OUT = os.path.join(os.path.dirname(__file__), '..', PROJECT + '.kicad_sch')
ROOT = str(uuid.UUID('8b8a2000-0000-4000-8000-000000000001'))
POWER = {'GND': 'power:GND', '+5V': 'power:+5V', '+3V3': 'power:+3V3', '+1V1': 'power:+1V1'}
STUB = 2.54
LABEL_W = 15.0     # mm of label text we reserve beyond each stub
FONT = [Sym('font'), [Sym('size'), 1.27, 1.27]]

_seq = [0]


def uid():
    _seq[0] += 1
    return str(uuid.uuid5(uuid.UUID(ROOT), str(_seq[0])))


def symbol_uuid(ref):
    """Stable per-reference UUID; gen_pcb.py uses the same one to link footprints."""
    return str(uuid.uuid5(uuid.UUID(ROOT), 'sym:' + ref))


def is_nc(net):
    return net.startswith('NC_') or net.startswith('SBU')


def eff(hide=False, justify=None, size=1.27):
    e = [Sym('effects'), [Sym('font'), [Sym('size'), size, size]]]
    if justify:
        e.append([Sym('justify')] + [Sym(j) for j in justify.split()])
    if hide:
        e.append([Sym('hide'), Sym('yes')])
    return e


def rot_point(px, py, r):
    a = math.radians(r)
    return (px * math.cos(a) - py * math.sin(a), px * math.sin(a) + py * math.cos(a))


def pin_geom(pin, x, y, r):
    """Connection point (sch coords) and outward unit vector (sch coords)."""
    X, Y = rot_point(pin['x'], pin['y'], r)
    ang = math.radians(pin['angle'] + 180 + r)
    return (x + X, y - Y), (round(math.cos(ang)), -round(math.sin(ang)))


def label_angle(out):
    return {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[out]


class Sheet:
    def __init__(self):
        self.items = []
        self.libs = {}
        self.pwr_n = 0

    def lib(self, lib_id):
        if lib_id not in self.libs:
            l, n = lib_id.split(':')
            self.libs[lib_id] = libsym.flatten(l, n)
        return self.libs[lib_id]

    def wire(self, a, b):
        self.items.append([Sym('wire'), [Sym('pts'), [Sym('xy'), fmt(a[0]), fmt(a[1])],
                                         [Sym('xy'), fmt(b[0]), fmt(b[1])]],
                           [Sym('stroke'), [Sym('width'), 0], [Sym('type'), Sym('default')]],
                           [Sym('uuid'), uid()]])

    def label(self, name, pos, out):
        ang = label_angle(out)
        just = 'left bottom' if ang in (0, 90) else 'right bottom'
        self.items.append([Sym('label'), name, [Sym('at'), fmt(pos[0]), fmt(pos[1]), ang],
                           eff(justify=just), [Sym('uuid'), uid()]])

    def noconn(self, pos):
        self.items.append([Sym('no_connect'), [Sym('at'), fmt(pos[0]), fmt(pos[1])],
                           [Sym('uuid'), uid()]])

    def text(self, s, pos, size=2.54):
        self.items.append([Sym('text'), s, [Sym('exclude_from_sim'), Sym('no')],
                           [Sym('at'), fmt(pos[0]), fmt(pos[1]), 0],
                           eff(justify='left bottom', size=size), [Sym('uuid'), uid()]])

    def power(self, net, pos, out):
        lib_id = POWER[net]
        sym = self.lib(lib_id)
        pp = libsym.pins(*lib_id.split(':'))[0]
        # body must continue outward: rotate so pin angle == outward angle
        want = math.degrees(math.atan2(-out[1], out[0])) % 360
        r = (want - pp['angle']) % 360
        self.pwr_n += 1
        ref = f'#PWR{self.pwr_n:03d}'
        self.items.append(self.symbol(lib_id, ref, net, '', pos[0], pos[1], r, {}, power=True))

    def symbol(self, lib_id, ref, value, footprint, x, y, r, extra, power=False, in_bom=True):
        sym = self.lib(lib_id)
        bx0, bx1, by0, by1 = bbox(lib_id, r)
        props = [
            [Sym('property'), 'Reference', ref, [Sym('at'), fmt(x + bx0), fmt(y - by1 - 2.54), 0],
             eff(hide=power, justify='left')],
            [Sym('property'), 'Value', value, [Sym('at'), fmt(x + bx0), fmt(y - by0 + 2.54), 0],
             eff(hide=power, justify='left')],
            [Sym('property'), 'Footprint', footprint, [Sym('at'), fmt(x), fmt(y), 0],
             eff(hide=True)],
            [Sym('property'), 'Datasheet', '', [Sym('at'), fmt(x), fmt(y), 0], eff(hide=True)],
            [Sym('property'), 'Description', '', [Sym('at'), fmt(x), fmt(y), 0], eff(hide=True)],
        ]
        for k, v in extra.items():
            props.append([Sym('property'), k, v, [Sym('at'), fmt(x), fmt(y), 0], eff(hide=True)])
        pinlist = [[Sym('pin'), p['number'], [Sym('uuid'), uid()]]
                   for p in libsym.pins(*lib_id.split(':'))]
        node = [Sym('symbol'), [Sym('lib_id'), lib_id], [Sym('at'), fmt(x), fmt(y), int(r)],
                [Sym('unit'), 1], [Sym('exclude_from_sim'), Sym('no')],
                [Sym('in_bom'), Sym('yes' if (in_bom and not power) else 'no')],
                [Sym('on_board'), Sym('no' if power else 'yes')], [Sym('dnp'), Sym('no')],
                [Sym('uuid'), uid() if power else symbol_uuid(ref)]] + props + pinlist + \
               [[Sym('instances'), [Sym('project'), PROJECT,
                                    [Sym('path'), '/' + ROOT, [Sym('reference'), ref],
                                     [Sym('unit'), 1]]]]]
        return node


def fmt(v):
    return Sym(fmt_num(v))


def snap(v):
    return round(v / 1.27) * 1.27


def bbox(lib_id, rot):
    pins = libsym.pins(*lib_id.split(':'))
    if not pins:
        return (-6.0, 6.0, -6.0, 6.0)
    pts = [rot_point(p['x'], p['y'], rot) for p in pins]
    return (min(p[0] for p in pts), max(p[0] for p in pts),
            min(p[1] for p in pts), max(p[1] for p in pts))


def cell_size(lib_id, rot):
    x0, x1, y0, y1 = bbox(lib_id, rot)
    return x1 - x0, y1 - y0


def build():
    sh = Sheet()
    nets = design.nets()
    # ---- layout: sections in a fixed order, parts flow in rows --------------
    order = ['power', 'mcu', 'buffers', 'ay', 'audio', 'midi', 'inputs', 'mech']
    titles = {'power': 'POWER: USB-C -> fuse -> 5V -> 3.3V', 'mcu': 'RP2040 CORE',
              'buffers': '3.3V -> 5V LEVEL BUFFERS', 'ay': 'THREE YM2149 / AY-3-8910 SOUND CHIPS',
              'audio': 'AUDIO OUT (same network as v1.0)', 'midi': 'MIDI IN',
              'inputs': 'INPUTS: 8 analog (74HC4051) + 8 digital (74HC165)', 'mech': 'MECHANICAL'}
    PAGE_W = 1100.0
    cursor_y = 60.0
    placed = {}
    for sec in order:
        parts = [(r, p) for r, p in design.ordered() if p['section'] == sec]
        if not parts:
            continue
        sh.text(titles[sec], (30, cursor_y), 3.5)
        cursor_y += 14
        # big parts first so the small ones fill in beneath
        parts.sort(key=lambda rp: -len(libsym.pins(*rp[1]['sym'].split(':'))))
        x = 40.0
        row_h = 0.0
        for ref, p in parts:
            sp = libsym.pins(*p['sym'].split(':'))
            vertical_2pin = len(sp) == 2 and sp[0]['x'] == sp[1]['x']
            rot = 90 if vertical_2pin else 0
            w, h = cell_size(p['sym'], rot)
            cw = w + 2 * (STUB + LABEL_W) + 6
            ch = h + 14
            if x + cw > PAGE_W - 40:
                x = 40.0
                cursor_y += row_h + 6
                row_h = 0.0
            cx = snap(x + cw / 2)
            cy = snap(cursor_y + ch / 2)
            placed[ref] = (cx, cy, rot, cw, ch)
            x += cw
            row_h = max(row_h, ch)
        cursor_y += row_h + 24
    page_h = cursor_y + 40

    # ---- emit symbols + stubs ---------------------------------------------
    for ref, p in design.ordered():
        cx, cy, rot, cw, ch = placed[ref]
        sym_pins = libsym.pins(*p['sym'].split(':'))
        extra = {'LCSC': p['lcsc']} if p['lcsc'] else {}
        in_bom = not (ref.startswith(('H', 'TP_')))
        sh.items.append(sh.symbol(p['sym'], ref, p['value'], p['fp'], cx, cy, rot, extra,
                                  in_bom=in_bom))
        for pin in sym_pins:
            net = p['pins'].get(pin['number'])
            pos, out = pin_geom(pin, cx, cy, rot)
            if net is None or is_nc(net):
                if pin['etype'] != 'no_connect' or True:
                    sh.noconn(pos)
                continue
            end = (pos[0] + out[0] * STUB, pos[1] + out[1] * STUB)
            sh.wire(pos, end)
            if net in POWER:
                sh.power(net, end, out)
            else:
                sh.label(net, end, out)
    # PWR_FLAGs on the rails fed from outside the regulator chain
    flag_x = 40.0
    for n, net in enumerate(['+5V', 'GND', 'VBUS']):
        pos = (snap(flag_x + 30 * n), snap(36.0))
        lib_id = 'power:PWR_FLAG'
        sh.items.append(sh.symbol(lib_id, f'#FLG{n + 1:02d}', 'PWR_FLAG', '', pos[0], pos[1], 0, {},
                                  power=True))
        pp = libsym.pins('power', 'PWR_FLAG')[0]
        pin_pos, out = pin_geom(pp, pos[0], pos[1], 0)
        end = (pin_pos[0], pin_pos[1] + STUB)
        sh.wire(pin_pos, end)
        if net in POWER:
            sh.power(net, end, (0, 1))
        else:
            sh.label(net, end, (0, 1))
    return sh, page_h


def main():
    sh, page_h = build()
    lib_syms = [Sym('lib_symbols')] + [sh.libs[k] for k in sorted(sh.libs)]
    paper_w, paper_h = 1189.0, 841.0     # A0
    root = [Sym('kicad_sch'), [Sym('version'), 20250114], [Sym('generator'), 'eeschema'],
            [Sym('generator_version'), '9.0'], [Sym('uuid'), ROOT],
            [Sym('paper'), 'User', 1200, 960],
            [Sym('title_block'), [Sym('title'), '8-Bit 8asterd v2.0'],
             [Sym('date'), '2026-10-03'], [Sym('rev'), '2.0'],
             [Sym('company'), 'The Key & Cable Company'],
             [Sym('comment'), 1, 'RP2040, 5V level-shifted bus, 8 analog + 8 digital inputs'],
             [Sym('comment'), 2, 'Generated by hardware/v2.0/tools/gen_sch.py from design.py']],
            lib_syms] + sh.items + [
        [Sym('sheet_instances'), [Sym('path'), '/', [Sym('page'), '1']]],
        [Sym('embedded_fonts'), Sym('no')]]
    with open(OUT, 'w') as f:
        f.write(dump(root) + '\n')
    print('wrote', os.path.normpath(OUT), '-', len(sh.items), 'items; sheet height needed ~%.0f mm' % page_h)


if __name__ == '__main__':
    main()
