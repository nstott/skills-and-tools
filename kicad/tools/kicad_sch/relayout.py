"""Relayout a KiCad 10 schematic: place symbols, draw wires, add power symbols, verify, write atomically.

Typical use is a small spec script (see examples/rp2350_devboard_layout.py):

    from kicad_sch.relayout import run, g

    def layout(r):
        r.place('U1', 200, 125)                 # grid units (1.27 mm); rot=0/90/180/270, mirror='y' flips left-right
        r.place('C1', 190, 100)
        r.seg(r.pin('U1', '5'), r.pin('C1', '1'))
        r.pwr('GND', r.pin('C1', '2'))
        r.stub_label('U1', '7', 'SIG', 'r')

    if __name__ == '__main__':
        run(layout)

    python3 spec.py SRC.kicad_sch DST.kicad_sch --expect nets.json

Nothing is written unless the nets of the result match `--expect` (made with `python3 -m kicad_sch nets`).
SRC is read-only. Always generate from the same SRC (a file without the generated wires/power symbols);
running on the previous output duplicates power symbols.
"""
import json
import os
import re
import sys

from .core import (Sheet, all_pins, connectivity, fmt, g, global_label_el, junction_el, key, label_el, nc_el,
                   on_segment, pin_abs, rect_el, text_el, uid, wire_el)
from .sx import block_head, children

REMOVED = ('label', 'wire', 'junction', 'no_connect', 'global_label', 'text', 'rectangle')
SIMPLE_PARTS = ('Device:R', 'Device:C', 'Device:L', 'Device:LED', 'Device:D_Schottky')
POWER_LIB_CANDIDATES = (
    '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/power.kicad_sym',
    '/usr/share/kicad/symbols/power.kicad_sym',
    '/usr/local/share/kicad/symbols/power.kicad_sym',
)


def find_power_lib():
    env = os.environ.get('KICAD_SYMBOL_DIR')
    cands = ([os.path.join(env, 'power.kicad_sym')] if env else []) + list(POWER_LIB_CANDIDATES)
    for c in cands:
        if os.path.exists(c):
            return c
    raise FileNotFoundError('power.kicad_sym not found; set KICAD_SYMBOL_DIR')


def prop_text(name, val, x, y, rot=0, hide=False, justify=None):
    """One KiCad 10 `(property ...)` block, two-tab indented."""
    j = '\t\t\t\t(justify %s)\n' % justify if justify else ''
    h = '\t\t\t(hide yes)\n' if hide else ''
    return ('\t\t(property "%s" "%s"\n\t\t\t(at %s %s %d)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n%s'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n%s\t\t\t)\n\t\t)\n'
            % (name, val, fmt(x), fmt(y), rot, h, j))


class Relayout:
    def __init__(self, src, power_lib=None, global_label_names=('GND', '+3V3', '+1V1', 'VBUS')):
        self.src = src
        self.text = open(src).read()
        self.sh = Sheet(self.text)
        self.project = re.search(r'\(project "([^"]*)"', self.text).group(1)
        root_uuid = [s for s in self.sh.top if block_head(self.text, s) == 'uuid'][0]
        self.sheet_uuid = re.search(r'\(uuid "?([^")\s]+)', self.text[root_uuid[0]:root_uuid[1]]).group(1)
        self.power_lib = power_lib
        self.global_label_names = set(global_label_names)
        self.pl = {}                      # ref -> (x_mm, y_mm, rot, mirror)
        self.W, self.J, self.L, self.NC, self.PW, self.TX = [], [], [], [], [], []
        self.hide_value = set()
        self.field_text = {}              # ref -> {'Reference'|'Value': (x_g, y_g, justify, hide)}
        self.parts = {}                   # ref -> (lcsc, mpn)
        self.footprints = {}              # ref -> footprint string
        self.dnp = set()
        self.paper = None
        self.title_block = None

    # ---------------------------------------------------------------- placement / metadata
    def place(self, ref, x, y, rot=0, mirror=None):
        """Place symbol `ref` at grid coords (x, y). rot: 0/90/180/270 CCW. mirror: 'y' = flip left-right."""
        self.sh.by_ref(ref)
        self.pl[ref] = (g(x), g(y), rot, mirror)

    def set_text(self, ref, field, x, y, justify=None, hide=False):
        """Pin a Reference/Value field to grid coords. Rotated (90/270) symbols get text angle 90 automatically."""
        self.field_text.setdefault(ref, {})[field] = (x, y, justify, hide)

    def set_part(self, ref, lcsc, mpn):
        self.parts[ref] = (lcsc, mpn)

    def set_footprint(self, ref, footprint):
        self.footprints[ref] = footprint

    def set_dnp(self, ref):
        self.dnp.add(ref)

    def set_paper(self, paper):
        self.paper = paper

    def set_title_block(self, title, date='', rev='', comment=''):
        self.title_block = (title, date, rev, comment)

    # ---------------------------------------------------------------- geometry
    def pin(self, ref, num):
        s = self.sh.by_ref(ref)
        x, y, rot, mir = self.pl[ref]
        return pin_abs(self.sh.lib_pins, s['lib_id'], x, y, rot, mir, num)

    @staticmethod
    def at(x, y):
        """Grid coords -> mm point."""
        return (g(x), g(y))

    # ---------------------------------------------------------------- drawing (all points in mm)
    def seg(self, a, b):
        if a != b:
            self.W.append((a, b))

    def path(self, *pts):
        for a, b in zip(pts, pts[1:]):
            self.seg(a, b)

    def junction(self, p):
        self.J.append(p)

    def no_connect(self, p):
        self.NC.append(p)

    def pwr(self, name, pt, hide_value=False):
        """Power symbol (GND, +3V3, ...) whose pin sits at `pt`. Put it on a wire end or a pin."""
        self.PW.append((name, pt))
        if hide_value:
            self.hide_value.add(key(*pt))

    def label(self, name, pt, ang=0):
        self.L.append((name, pt, ang))

    def stub_label(self, ref, num, name, direction, length=2):
        """Short wire from a pin, ending in a net label. direction: r/l/u/d; length in grid units."""
        x, y = self.pin(ref, num)
        dx, dy = {'r': (1, 0), 'l': (-1, 0), 'u': (0, -1), 'd': (0, 1)}[direction]
        e = (round(x + dx * g(length), 4), round(y + dy * g(length), 4))
        self.seg((x, y), e)
        self.label(name, e, {'r': 0, 'l': 180, 'u': 90, 'd': 270}[direction])

    def vstub(self, ref, num, name, direction):
        """Label placed right at the pin tip with vertical text (direction 'u' text rises, 'd' text hangs)."""
        x, y = self.pin(ref, num)
        self.label(name, (x, y), 90 if direction == 'u' else 270)

    def pin_or_power(self, ref, num, name, length, power_names=('GND', '+3V3', 'VBUS')):
        """Dense connectors: power pins get a long stub + power symbol, signals a short stub + label.
        Long stubs keep power symbols clear of the neighbouring pins' label text."""
        if name in power_names:
            x, y = self.pin(ref, num)
            e = (round(x - g(length + (4 if name != 'GND' else 0)), 4), y)
            self.seg((x, y), e)
            self.pwr(name, e)
        else:
            self.stub_label(ref, num, name, 'l', 3)

    def title(self, text, x, y):
        """Block heading at grid coords."""
        self.TX.append((text, (g(x), g(y))))

    # ---------------------------------------------------------------- checks
    def _split_and_junction(self):
        """KiCad only connects a pin at a wire *endpoint*: split wires at every pin on their interior, then
        add junction dots wherever 3+ things meet."""
        pin_pts = {}
        for s in self.sh.symbols:
            x, y, rot, mir = self.pl[s['ref']]
            for (n, pt) in all_pins(self.sh.lib_pins, s['lib_id'], x, y, rot, mir):
                pin_pts.setdefault(key(*pt), []).append(pt)
        for (n, pt) in self.PW:
            pin_pts.setdefault(key(*pt), []).append(pt)
        pts = {k: v[0] for k, v in pin_pts.items()}
        for (a, b) in self.W:
            pts[key(*a)] = a
            pts[key(*b)] = b
        new_w = []
        for (a, b) in self.W:
            ka, kb = key(*a), key(*b)
            inside = [k for k in pts if k not in (ka, kb) and on_segment(k, ka, kb)]
            inside.sort(key=lambda k: (k[0] - ka[0]) ** 2 + (k[1] - ka[1]) ** 2)
            chain = [a] + [pts[k] for k in inside] + [b]
            new_w += list(zip(chain, chain[1:]))
        seen, w2 = set(), []
        for (u, v) in new_w:
            kk = frozenset((key(*u), key(*v)))
            if kk in seen or key(*u) == key(*v):
                continue
            seen.add(kk)
            w2.append((u, v))
        self.W = w2
        inc = {}
        for (u, v) in self.W:
            for q in (u, v):
                inc[key(*q)] = inc.get(key(*q), 0) + 1
        for k, items in pin_pts.items():
            inc[k] = inc.get(k, 0) + len(items)
        ends = {key(*q) for (u, v) in self.W for q in (u, v)}
        self.J = [pts[k] for k, c in inc.items() if c >= 3 and k in ends]

    def nets(self):
        comps = [(s['ref'], s['lib_id']) + self.pl[s['ref']] for s in self.sh.symbols]
        return connectivity(self.sh.lib_pins, comps, self.W, [(n, p) for (n, p, a) in self.L], self.PW)

    def verify(self, expected_json, quiet=False):
        """Compare net partitions (sets of pins) with a snapshot. Returns the number of mismatches."""
        exp = json.load(open(expected_json))
        exp_sets = {frozenset(tuple(p.split('/', 1)) for p in v): k for k, v in exp.items()}
        new_sets = {frozenset(v): k for k, v in self.nets().items()}
        bad = 0
        for s, name in exp_sets.items():
            if s not in new_sets:
                bad += 1
                best = max(new_sets, key=lambda t: len(t & s))
                print('NET MISMATCH %s: expected %d pins; closest new net %s (%d pins) missing %s extra %s'
                      % (name, len(s), new_sets[best], len(best), sorted(s - best)[:8], sorted(best - s)[:8]))
        for s, name in new_sets.items():
            if s not in exp_sets and len(s) > 1:
                bad += 1
                print('UNEXPECTED NET', name, sorted(s)[:10])
        if not quiet:
            print('connectivity mismatches:', bad)
        return bad

    # ---------------------------------------------------------------- writing
    def _retext(self, block, ref, lib_id):
        x, y, rot, mir = self.pl[ref]
        old = self.sh.by_ref(ref)
        block = re.sub(r'\(at [-\d.e]+ [-\d.e]+ [-\d.e]+\)(\n\t\t\(mirror [xy]\))?',
                       '(at %s %s %d)%s' % (fmt(x), fmt(y), rot, ('\n\t\t(mirror %s)' % mir) if mir else ''),
                       block, count=1)
        dx, dy = x - old['x'], y - old['y']
        trot = 90 if rot in (90, 270) else 0
        pieces = []
        for (a, b) in [sp for sp in children(block, (0, len(block))) if block_head(block, sp) == 'property']:
            pb = block[a:b]
            nm = re.match(r'\(property (?:private )?"([^"]+)" "((?:[^"\\]|\\.)*)"', pb)
            pname, pval = nm.group(1), nm.group(2)
            if pname in ('Reference', 'Value') and lib_id == 'power:PWR_FLAG':
                pieces.append((a, b, prop_text(pname, pval, x, y, 0, True)[2:-1]))
                continue
            if pname in ('Reference', 'Value') and pname in self.field_text.get(ref, {}):
                tx, ty, tj, th = self.field_text[ref][pname]
                pieces.append((a, b, prop_text(pname, pval, g(tx), g(ty), trot, th, tj)[2:-1]))
                continue
            if pname in ('Reference', 'Value'):
                pos = None
                if lib_id in SIMPLE_PARTS:
                    if rot in (0, 180) or (lib_id == 'Device:LED' and rot == 90):
                        pos = (x + g(3), y - g(1 if pname == 'Reference' else -1)); just = 'left'
                    else:
                        pos = (x, y - g(3 if pname == 'Reference' else -3)); just = None
                elif not lib_id.startswith('power:'):
                    at = re.search(r'\(at ([-\d.e]+) ([-\d.e]+)', pb)
                    if mir or rot:
                        pos = (x, y - g(6) if pname == 'Reference' else y + g(6)); just = None
                    else:
                        pos = (float(at.group(1)) + dx, float(at.group(2)) + dy)
                        j = re.search(r'\(justify ([a-z ]+)\)', pb)
                        just = j.group(1) if j else None
                if pos is not None:
                    hide = '(hide yes)' in pb.split('(effects')[0]
                    pieces.append((a, b, prop_text(pname, pval, pos[0], pos[1], trot, hide, just)[2:-1]))
                    continue
            if pname == 'Footprint' and ref in self.footprints:
                pb = pb.replace('"%s"' % pval, '"%s"' % self.footprints[ref], 1)
            at = re.search(r'\(at ([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\)', pb)
            if at:
                pb = re.sub(r'\(at [-\d.e]+ [-\d.e]+ [-\d.e]+\)',
                            '(at %s %s %s)' % (fmt(x), fmt(y), at.group(3)), pb, count=1)
                pieces.append((a, b, pb))
        for (a, b, nb) in sorted(pieces, key=lambda t: -t[0]):
            block = block[:a] + nb + block[b:]
        if ref in self.parts:
            extra = prop_text('LCSC', self.parts[ref][0], x, y, 0, True) + prop_text('MPN', self.parts[ref][1], x, y, 0, True)
            i = block.index('\n\t\t(pin "') + 1
            block = block[:i] + extra + block[i:]
        if ref in self.dnp:
            block = block.replace('(dnp no)', '(dnp yes)', 1)
        return block

    def build(self):
        """Return the new file text."""
        text, sh = self.text, self.sh
        missing = [s['ref'] for s in sh.symbols if s['ref'] not in self.pl]
        if missing:
            raise ValueError('symbols without a place() call: %s' % missing)
        self._split_and_junction()
        # rebuild from the original text: drop old wires/labels, rewrite symbols
        removed = [sp for sp in sh.top if block_head(text, sp) in REMOVED]
        segments = sorted([(sp, None) for sp in removed] + [(s['span'], s) for s in sh.symbols], key=lambda t: t[0][0])
        buf, pos = [], 0
        for (sp, s) in segments:
            a = sp[0]
            while a > pos and text[a - 1] in '\t':
                a -= 1
            if a > pos and text[a - 1] == '\n':
                a -= 1
            buf.append(text[pos:a])
            if s is not None:
                buf.append('\n\t' + self._retext(text[sp[0]:sp[1]], s['ref'], s['lib_id']))
            pos = sp[1]
        buf.append(text[pos:])
        out = ''.join(buf)

        # power symbols: embed the library entries, then add instances
        plib_path = self.power_lib or find_power_lib()
        plib = open(plib_path).read()
        pl_top = children(plib, (0, len(plib)))

        def entry(name):
            for sp in pl_top:
                if block_head(plib, sp) == 'symbol' and plib[sp[0]:sp[1]].startswith('(symbol "%s"' % name):
                    return plib[sp[0]:sp[1]]
            raise KeyError('power symbol %s not in %s' % (name, plib_path))

        embedded = set(re.findall(r'\(symbol "(power:[^"]+)"', out))
        lib_span = [s for s in children(out, (0, len(out))) if block_head(out, s) == 'lib_symbols'][0]
        add = ''
        for n in sorted({n for (n, p) in self.PW}):
            if 'power:%s' % n not in embedded:
                b = entry(n).replace('(symbol "%s"' % n, '(symbol "power:%s"' % n, 1)
                add += '\n' + '\n'.join('\t' + ln if ln else ln for ln in b.split('\n'))
        out = out[:lib_span[1] - 1] + add + '\n\t' + out[lib_span[1] - 1:]

        def value_off(name):
            m = re.search(r'\(property "Value" "[^"]*"\s*\(at ([-\d.e]+) ([-\d.e]+)', entry(name))
            return float(m.group(1)), float(m.group(2))

        def power_instance(name, pt, n):
            vx, vy = value_off(name)
            ref = '#PWR%03d' % n
            desc = 'Power symbol creates a global label with name \\"%s\\"' % name
            x, y = pt
            return ('\t(symbol\n\t\t(lib_id "power:%s")\n\t\t(at %s %s 0)\n\t\t(unit 1)\n\t\t(body_style 1)\n\t\t(exclude_from_sim no)\n'
                    '\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(in_pos_files yes)\n\t\t(dnp no)\n\t\t(uuid "%s")\n'
                    % (name, fmt(x), fmt(y), uid())
                    + prop_text('Reference', ref, x, y, 0, True)
                    + prop_text('Value', name, x + vx, y - vy, 0, key(x, y) in self.hide_value)
                    + prop_text('Footprint', '', x, y, 0, True)
                    + prop_text('Datasheet', '', x, y, 0, True)
                    + prop_text('Description', desc, x, y, 0, True)
                    + '\t\t(pin "1"\n\t\t\t(uuid "%s")\n\t\t)\n\t\t(instances\n\t\t\t(project "%s"\n\t\t\t\t(path "/%s"\n'
                      '\t\t\t\t\t(reference "%s")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n'
                    % (uid(), self.project, self.sheet_uuid, ref))

        new = ''.join(power_instance(n, pt, i) for i, (n, pt) in enumerate(self.PW, 1))
        new += ''.join(wire_el(a, b) for (a, b) in self.W)
        new += ''.join(junction_el(p) for p in self.J)
        new += ''.join(nc_el(p) for p in self.NC)
        for (n, p, a) in self.L:
            new += global_label_el(n, p, a) if n in self.global_label_names else label_el(n, p, a)
        new += ''.join(text_el(s, p) for (s, p) in self.TX)
        ins = out.index('\t(sheet_instances')
        out = out[:ins] + new + out[ins:]
        if self.paper:
            out = re.sub(r'\(paper "[^"]*"\)', '(paper "%s")' % self.paper, out, count=1)
        if self.title_block and '(title_block' not in out:
            t, d, r, c = self.title_block
            tb = '\n\t(title_block\n\t\t(title "%s")\n\t\t(date "%s")\n\t\t(rev "%s")\n\t\t(comment 1 "%s")\n\t)' % (t, d, r, c)
            out = re.sub(r'(\(paper "[^"]*"\))', lambda m: m.group(1) + tb, out, count=1)
        return out

    def write(self, dst):
        """Atomic write: temp file in the destination folder, then rename."""
        out = self.build()
        tmp = os.path.join(os.path.dirname(os.path.abspath(dst)), '.' + os.path.basename(dst) + '.tmp')
        with open(tmp, 'w') as f:
            f.write(out)
        os.replace(tmp, dst)
        return len(out)


def run(layout, argv=None):
    """CLI wrapper for a spec: `spec.py SRC DST [--expect nets.json] [--force]`."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--expect', help='nets snapshot JSON (python3 -m kicad_sch nets FILE --json OUT)')
    ap.add_argument('--force', action='store_true', help='write even if nets differ')
    a = ap.parse_args(argv)
    r = Relayout(a.src)
    layout(r)
    r._split_and_junction()
    bad = r.verify(a.expect) if a.expect else 0
    if bad and not a.force:
        print('not writing: nets differ from', a.expect)
        sys.exit(1)
    n = r.write(a.dst)
    print('wrote %s (%d bytes): %d power symbols, %d wires, %d labels, %d junctions'
          % (a.dst, n, len(r.PW), len(r.W), len(r.L), len(r.J)))
