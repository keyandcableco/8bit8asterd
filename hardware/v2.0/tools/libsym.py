"""Load KiCad symbols from the stock libraries, flatten `extends`, and expose
their pins (number, name, electrical type, position, orientation)."""
import os
from sexp import parse, find, first, Sym

SYMDIR = '/usr/share/kicad/symbols'
_cache = {}


def _lib(libname):
    if libname not in _cache:
        with open(os.path.join(SYMDIR, libname + '.kicad_sym')) as f:
            tree = parse(f.read())
        _cache[libname] = {c[1]: c for c in find(tree, 'symbol')}
    return _cache[libname]


def raw(libname, name):
    return _lib(libname)[name]


def flatten(libname, name):
    """Return a self-contained symbol node named `libname:name`."""
    s = raw(libname, name)
    ext = first(s, 'extends')
    if not ext:
        out = [Sym('symbol'), f'{libname}:{name}'] + [c for c in s[2:]]
        return _rename_units(out, name, name)
    base = flatten(libname, ext[1])
    base_name = ext[1]
    # properties from the child override the parent's
    child_props = {p[1]: p for p in find(s, 'property')}
    out = [Sym('symbol'), f'{libname}:{name}']
    for c in base[2:]:
        if isinstance(c, list) and c and c[0] == 'property' and c[1] in child_props:
            out.append(child_props[c[1]])
        else:
            out.append(c)
    return _rename_units(out, base_name, name)


def _rename_units(node, old, new):
    for c in node:
        if isinstance(c, list) and c and c[0] == 'symbol':
            c[1] = c[1].replace(old + '_', new + '_', 1) if c[1].startswith(old + '_') else c[1]
    return node


def pins(libname, name):
    """List of dicts for every pin, all units merged (unit number kept)."""
    sym = flatten(libname, name)
    res = []
    for unit in find(sym, 'symbol'):
        tag = unit[1].rsplit('_', 2)
        u = int(tag[-2]) if len(tag) >= 3 else 0
        for p in find(unit, 'pin'):
            at = first(p, 'at')
            res.append(dict(
                number=first(p, 'number')[1], name=first(p, 'name')[1],
                etype=str(p[1]), x=float(at[1]), y=float(at[2]), angle=int(float(at[3])),
                length=float(first(p, 'length')[1]), unit=u,
                hidden=any(isinstance(c, list) and c and c[0] == 'hide' for c in p) or 'hide' in p))
    return res
