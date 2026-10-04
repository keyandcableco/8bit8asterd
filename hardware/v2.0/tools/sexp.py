"""Minimal KiCad S-expression reader/writer (enough for symbol libraries and
generated schematics)."""
import re

_TOK = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


class Sym(str):
    """A bare (unquoted) atom, to tell it apart from a quoted string."""


def parse(text):
    pos = 0
    stack = [[]]
    while True:
        m = _TOK.match(text, pos)
        if not m:
            break
        pos = m.end()
        op, cl, qs, atom = m.groups()
        if op:
            stack.append([])
        elif cl:
            done = stack.pop()
            stack[-1].append(done)
        elif qs is not None:
            stack[-1].append(qs.replace('\\"', '"').replace('\\\\', '\\'))
        else:
            stack[-1].append(Sym(atom))
    return stack[0][0]


def find(node, tag):
    """Direct children that are lists beginning with `tag`."""
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def first(node, tag, default=None):
    r = find(node, tag)
    return r[0] if r else default


def dump(node, indent=0, inline_limit=60):
    pad = '\t' * indent
    if isinstance(node, Sym):
        return str(node)
    if isinstance(node, str):
        return '"' + node.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if isinstance(node, (int, float)):
        return fmt_num(node)
    inner = [dump(c, indent + 1) for c in node]
    one = '(' + ' '.join(inner) + ')'
    if '\n' not in one and len(one) <= inline_limit or not any(isinstance(c, list) for c in node):
        return one
    head = []
    rest = []
    for c in node:
        (rest if isinstance(c, list) else (head if not rest else rest)).append(c)
    out = '(' + ' '.join(dump(h, indent + 1) for h in head)
    for c in rest:
        out += '\n' + pad + '\t' + dump(c, indent + 1)
    return out + '\n' + pad + ')'


def fmt_num(v):
    if isinstance(v, int):
        return str(v)
    s = ('%.4f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s
