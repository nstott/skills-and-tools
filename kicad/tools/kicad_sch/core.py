"""Pin geometry, connectivity checker and s-expression element writers for KiCad 10 schematics.

Units: millimetres unless a name says grid. `g(n)` converts 1.27 mm grid units to mm.
KiCad connects a pin to a wire only at a wire *endpoint*, and labels connect anywhere along a wire;
`connectivity` models exactly that so layouts can be verified before a file is written.
"""
import math
import re
import uuid
from .sx import block_head, children, parse_lib_pins

G = 1.27


def g(n):
    return round(n * G, 4)


def key(x, y):
    return (round(x * 100), round(y * 100))


class Sheet:
    def __init__(self, text):
        self.text = text
        top = children(text, (0, len(text)))
        self.top = top
        self.lib_span = [s for s in top if block_head(text, s) == 'lib_symbols'][0]
        self.lib_pins = parse_lib_pins(text, self.lib_span)
        self.symbols = []  # dicts: span, lib_id, ref, x, y, rot, mirror
        for sp in top:
            if block_head(text, sp) != 'symbol':
                continue
            body = text[sp[0]:sp[1]]
            lib_id = re.search(r'\(lib_id "([^"]+)"', body).group(1)
            at = re.search(r'\(at ([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\)', body)
            mir = re.search(r'\(mirror (x|y)\)', body.split('(property')[0])
            ref = re.search(r'\(property "Reference" "([^"]*)"', body).group(1)
            self.symbols.append(dict(span=sp, lib_id=lib_id, ref=ref, x=float(at.group(1)),
                                     y=float(at.group(2)), rot=float(at.group(3)),
                                     mirror=mir.group(1) if mir else None))

    def by_ref(self, ref):
        return [s for s in self.symbols if s['ref'] == ref][0]


def pin_abs(lib_pins, lib_id, x, y, rot, mirror, num):
    """Absolute (x, y) of pin `num`; every pin entry with that number is identical in position."""
    for (n, name, px, py, ang) in lib_pins[lib_id]:
        if n != num:
            continue
        if mirror == 'y':
            px = -px
        elif mirror == 'x':
            py = -py
        th = math.radians(rot)
        xr = px * math.cos(th) - py * math.sin(th)
        yr = px * math.sin(th) + py * math.cos(th)
        return (round(x + xr, 4), round(y - yr, 4))
    raise KeyError((lib_id, num))


def all_pins(lib_pins, lib_id, x, y, rot, mirror):
    seen, out = set(), []
    for (n, name, px, py, ang) in lib_pins[lib_id]:
        if n in seen:
            continue
        seen.add(n)
        out.append((n, pin_abs(lib_pins, lib_id, x, y, rot, mirror, n)))
    return out


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def on_segment(pt, a, b):
    (px, py), (ax, ay), (bx, by) = pt, a, b
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > 2:  # grid units are 1/100 mm
        return False
    return min(ax, bx) - 1 <= px <= max(ax, bx) + 1 and min(ay, by) - 1 <= py <= max(ay, by) + 1


def connectivity(lib_pins, comps, wires, labels, powers):
    """Net extraction that follows KiCad's rules.

    comps: [(ref, lib_id, x, y, rot, mirror)]; wires: [((x1,y1),(x2,y2))];
    labels: [(name, (x,y))]; powers: [(name, (x,y))] (power symbols; their pin is at that point).

    Rules modelled: pins/wire endpoints/power pins that share a point are connected; a wire endpoint or
    a label that lies on the *interior* of another wire is connected to it; a *pin* in the interior of a
    wire is NOT connected (KiCad needs a wire endpoint at the pin).
    Returns {tuple_of_net_names or ('<unnamed>', pins): set((ref, pin))}.
    """
    uf = UF()
    seg_hit = {}  # points that may join a wire's interior: wire endpoints and labels
    for (ref, lid, x, y, rot, mir) in comps:
        for (n, pt) in all_pins(lib_pins, lid, x, y, rot, mir):
            uf.union(('pin', ref, n), ('pt', key(*pt)))
    segs = []
    for (a, b) in wires:
        ka, kb = key(*a), key(*b)
        uf.union(('pt', ka), ('pt', kb))
        seg_hit[ka] = seg_hit[kb] = 1
        segs.append((ka, kb))
    for (name, pt) in list(labels) + list(powers):
        k = key(*pt)
        uf.union(('name', name), ('pt', k))
    for (name, pt) in labels:
        seg_hit[key(*pt)] = 1
    for (ka, kb) in segs:
        for k in seg_hit:
            if on_segment(k, ka, kb):
                uf.union(('pt', k), ('pt', ka))
    groups = {}
    for node in list(uf.p):
        if node[0] == 'pin':
            groups.setdefault(uf.find(node), set()).add((node[1], node[2]))
    nets = {}
    for root, pins in groups.items():
        nm = sorted({n[1] for n in uf.p if n[0] == 'name' and uf.find(n) == root})
        nets[tuple(nm) if nm else ('<unnamed>', tuple(sorted(pins)))] = pins
    return nets


def uid():
    return str(uuid.uuid4())


def fmt(v):
    s = ('%.4f' % v).rstrip('0').rstrip('.')
    return s if s != '-0' else '0'


def wire_el(a, b):
    return ('\t(wire\n\t\t(pts\n\t\t\t(xy %s %s) (xy %s %s)\n\t\t)\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "%s")\n\t)\n'
            % (fmt(a[0]), fmt(a[1]), fmt(b[0]), fmt(b[1]), uid()))


def junction_el(p):
    return ('\t(junction\n\t\t(at %s %s)\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n\t\t(uuid "%s")\n\t)\n'
            % (fmt(p[0]), fmt(p[1]), uid()))


def nc_el(p):
    return '\t(no_connect\n\t\t(at %s %s)\n\t\t(uuid "%s")\n\t)\n' % (fmt(p[0]), fmt(p[1]), uid())


def label_el(name, p, ang):
    just = {0: 'left bottom', 90: 'left bottom', 180: 'right bottom', 270: 'right bottom'}[ang]
    return ('\t(label "%s"\n\t\t(at %s %s %d)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify %s)\n\t\t)\n\t\t(uuid "%s")\n\t)\n'
            % (name, fmt(p[0]), fmt(p[1]), ang, just, uid()))


def text_el(s, p, size=1.905, bold=True):
    return ('\t(text "%s"\n\t\t(exclude_from_sim no)\n\t\t(at %s %s 0)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size %s %s)\n\t\t\t\t%s)\n\t\t\t(justify left bottom)\n\t\t)\n\t\t(uuid "%s")\n\t)\n'
            % (s, fmt(p[0]), fmt(p[1]), size, size, '(bold yes)\n\t\t\t' if bold else '', uid()))


def rect_el(a, b):
    return ('\t(rectangle\n\t\t(start %s %s)\n\t\t(end %s %s)\n\t\t(stroke\n\t\t\t(width 0.254)\n\t\t\t(type dash)\n\t\t)\n\t\t(fill\n\t\t\t(type none)\n\t\t)\n\t\t(uuid "%s")\n\t)\n'
            % (fmt(a[0]), fmt(a[1]), fmt(b[0]), fmt(b[1]), uid()))


def global_label_el(name, p, ang):
    just = {0: 'left', 90: 'left', 180: 'right', 270: 'right'}[ang]
    return ('\t(global_label "%s"\n\t\t(shape passive)\n\t\t(at %s %s %d)\n\t\t(fields_autoplaced yes)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify %s)\n\t\t)\n\t\t(uuid "%s")\n'
            '\t\t(property "Intersheet References" "${INTERSHEET_REFS}"\n\t\t\t(at %s %s 0)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n\t\t\t(hide yes)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n'
            % (name, fmt(p[0]), fmt(p[1]), ang, just, uid(), fmt(p[0]), fmt(p[1])))
