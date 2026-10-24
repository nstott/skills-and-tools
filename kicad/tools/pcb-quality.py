#!/usr/bin/env python3
"""Measure a routed .kicad_pcb against general layout-quality signals and flag where it deviates.

    pcb-quality.py FILE.kicad_pcb [--sch FILE.kicad_sch] [--fab jlc-standard|jlc-advanced] [--json OUT]

Each finding names the part / net and board coordinates (mm). Checks (thresholds in CFG below):
  decoupling     every IC power pin has a cap (one pad on that net, the other on GND) within 2 mm; >5 mm is bad
  switching      regulator loop: inductor and the bulk caps (>=1 uF) on the regulator's power pins sit close to it
  crystal        crystal load caps close to the crystal; no foreign-net copper inside the crystal's footprint area
  diff-pairs     P/N nets (D+/D-, _P/_N, DP/DM): length mismatch, vias, layer changes
  critical-vias  vias on clock / crystal / diff-pair nets
  power-width    narrowest track on each power net against a width floor (0.25 mm, or --power-width)
  fab-margin     vias, drills and tracks below the fab tier's standard floor (cost or yield risk)
  via-in-pad     vias inside an SMD pad
  geometry       acute (<90 deg) corners and dangling track ends
  stitching      GND via density over the board area and near the edge
  planes         each signal layer has a GND plane on an adjacent layer
  plane-cuts     tracks of other nets routed on a plane layer (they slot the plane)
  assembly       fiducials and test points present
Pin types come from the schematic netlist (`kicad-cli`, run in the schematic's folder) when --sch is given or a
sibling .kicad_sch exists; otherwise power nets are recognised by name. KiCad DRC is NOT run here: run
`kicad-cli pcb drc --refill-zones` alongside (see the kicad-pcb-review skill).
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch.sx import block_head, children  # noqa: E402

KICAD_CLI = os.environ.get('KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')

CFG = dict(
    decap_warn=2.0, decap_bad=5.0,          # mm, power pad to nearest decoupling cap pad
    loop_warn=3.0, loop_bad=6.0,            # mm, regulator pad to its inductor / bulk caps
    xtal_cap_warn=2.5,                      # mm, crystal pad to its load cap
    dp_mismatch_warn=1.0, dp_mismatch_bad=3.0,  # mm, diff pair length difference
    dp_vias_warn=2,                         # vias per diff-pair member
    crit_vias_warn=2,                       # vias on a crystal / clock net
    power_width=0.25,                       # mm, minimum track width on power nets
    stitch_per_cm2=1.0,                     # GND vias per cm^2 below this is flagged
)
FABS = {
    'jlc-standard': dict(via_diameter=0.45, via_drill=0.2, track=0.127, name='JLCPCB standard'),
    'jlc-advanced': dict(via_diameter=0.25, via_drill=0.15, track=0.09, name='JLCPCB advanced'),
}
POWER_NAME = re.compile(r'^[+-]|VCC|VDD|VBUS|VSYS|VIN|VOUT|VBAT|AVDD|DVDD|IOVDD|^\d+V\d*$|\dV\d', re.I)
GND_NAME = re.compile(r'^(GND|AGND|DGND|PGND|GNDA|GNDD|VSS|0V)$', re.I)
CLOCK_NAME = re.compile(r'XIN|XOUT|XTAL|OSC|CLK|SCLK|SCK', re.I)
NOT_CLOCK = re.compile(r'SWCLK|TCK', re.I)               # slow debug clocks
SIGNAL_LIKE = re.compile(r'SENSE|_EN$|^EN|DET|_FB|ADC|REF|PGOOD|PG$', re.I)  # named like a rail, carries no current
REG_PIN = re.compile(r'VIN|VOUT|PVIN|VREG_VIN|VCC_?IN|^IN$|^OUT$', re.I)   # regulator input/output pins


def num(s):
    return float(s)


def rot(lx, ly, deg):
    th = math.radians(deg)
    return lx * math.cos(th) + ly * math.sin(th), -lx * math.sin(th) + ly * math.cos(th)


def load(pcb):
    t = open(pcb).read()
    fps, segs, vias, zones, layers = [], [], [], [], []
    m = re.search(r'\(layers\s*\n(.*?)\n\t\)', t, re.S)
    if m:
        layers = re.findall(r'\(\d+ "([^"]+\.Cu)" (\w+)', m.group(1))
    outline = None
    for sp in children(t, (0, len(t))):
        h, b = block_head(t, sp), t[sp[0]:sp[1]]
        if h == 'footprint':
            ref = re.search(r'\(property "Reference" "([^"]*)"', b).group(1)
            val = (re.search(r'\(property "Value" "([^"]*)"', b) or [None, ''])[1]
            at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', b)
            x0, y0, r0 = num(at.group(1)), num(at.group(2)), num(at.group(3) or 0)
            layer = re.search(r'\(layer "([^"]+)"\)', b).group(1)
            name = re.match(r'\(footprint "([^"]+)"', b).group(1)
            pads = []
            for p in children(b, (0, len(b))):
                if block_head(b, p) != 'pad':
                    continue
                pb = b[p[0]:p[1]]
                pm = re.match(r'\(pad "([^"]*)" (\w+)', pb)
                pa = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', pb)
                sz = re.search(r'\(size ([-\d.]+) ([-\d.]+)\)', pb)
                net = re.search(r'\(net (?:\d+ )?"([^"]*)"\)', pb)
                dx, dy = rot(num(pa.group(1)), num(pa.group(2)), r0)
                pads.append(dict(num=pm.group(1), kind=pm.group(2), x=x0 + dx, y=y0 + dy,
                                 w=num(sz.group(1)), h=num(sz.group(2)), ang=num(pa.group(3) or 0),
                                 net=net.group(1) if net else None,
                                 layers=re.findall(r'"([^"]+)"', re.search(r'\(layers ([^)]*)\)', pb).group(1))))
            fps.append(dict(ref=ref, value=val, x=x0, y=y0, rot=r0, layer=layer, name=name, pads=pads, body=b))
        elif h == 'segment':
            s = re.search(r'\(start ([-\d.]+) ([-\d.]+)\)', b)
            e = re.search(r'\(end ([-\d.]+) ([-\d.]+)\)', b)
            segs.append(dict(x1=num(s.group(1)), y1=num(s.group(2)), x2=num(e.group(1)), y2=num(e.group(2)),
                             w=num(re.search(r'\(width ([-\d.]+)\)', b).group(1)),
                             layer=re.search(r'\(layer "([^"]+)"\)', b).group(1),
                             net=re.search(r'\(net (?:\d+ )?"([^"]*)"\)', b).group(1)))
        elif h == 'via':
            a = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', b)
            vias.append(dict(x=num(a.group(1)), y=num(a.group(2)), size=num(re.search(r'\(size ([-\d.]+)\)', b).group(1)),
                             drill=num(re.search(r'\(drill ([-\d.]+)\)', b).group(1)),
                             net=re.search(r'\(net (?:\d+ )?"([^"]*)"\)', b).group(1)))
        elif h == 'zone':
            n = re.search(r'\(net (?:\d+ )?"([^"]*)"\)', b)
            ly = re.search(r'\(layers? ([^)]*)\)', b).group(1)
            poly = re.search(r'\(polygon\s*\(pts(.*?)\)\s*\)', b, re.S)
            pts = [(num(a_), num(c_)) for a_, c_ in re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)', poly.group(1))] if poly else []
            area = abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))) / 2 if pts else 0
            zones.append(dict(net=n.group(1) if n else '', layers=re.findall(r'"([^"]+)"', ly), area=area, keepout='(keepout' in b))
        elif h == 'gr_rect' and 'Edge.Cuts' in b:
            s = re.search(r'\(start ([-\d.]+) ([-\d.]+)\)', b)
            e = re.search(r'\(end ([-\d.]+) ([-\d.]+)\)', b)
            outline = (num(s.group(1)), num(s.group(2)), num(e.group(1)), num(e.group(2)))
    if outline is None:  # fall back to the extents of Edge.Cuts lines
        pts = [(num(a), num(b)) for a, b in re.findall(r'\((?:start|end) ([-\d.]+) ([-\d.]+)\)\s*(?:\([^)]*\)\s*)*\(layer "Edge\.Cuts"\)', t)]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            outline = (min(xs), min(ys), max(xs), max(ys))
    return dict(fps=fps, segs=segs, vias=vias, zones=zones, layers=layers, outline=outline)


def pin_types(sch):
    d, f = os.path.split(os.path.abspath(sch))
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'n.xml')
        r = subprocess.run([KICAD_CLI, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', out, f],
                           cwd=d, capture_output=True, text=True)
        if r.returncode or not os.path.exists(out):
            return None
        root = ET.parse(out).getroot()
    types = {}
    for n in root.find('nets'):
        for nd in n:
            types[(nd.get('ref'), nd.get('pin'))] = (nd.get('pintype') or '', nd.get('pinfunction') or '')
    return types


def dist(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def pad_edge_dist(a, b):
    """Rough edge-to-edge distance between two pads (circle approximation of each pad)."""
    return max(0.0, dist(a, b) - min(a['w'], a['h']) / 2 - min(b['w'], b['h']) / 2)


def cap_farads(v):
    m = re.match(r'([\d.]+)\s*([pnuµm]?)F?', (v or '').replace(',', '.'))
    if not m:
        return None
    return float(m.group(1)) * {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'm': 1e-3, '': 1}[m.group(2)]


def main():
    a = sys.argv[1:]
    opt = {}
    for k in ('--sch', '--fab', '--json', '--power-width'):
        if k in a:
            i = a.index(k)
            opt[k] = a[i + 1]
            del a[i:i + 2]
    if len(a) != 1:
        sys.exit(__doc__)
    pcb = a[0]
    fab = FABS[opt.get('--fab', 'jlc-standard')]
    if '--power-width' in opt:
        CFG['power_width'] = float(opt['--power-width'])
    sch = opt.get('--sch') or (pcb[:-len('.kicad_pcb')] + '.kicad_sch' if os.path.exists(pcb[:-len('.kicad_pcb')] + '.kicad_sch') else None)
    B = load(pcb)
    types = pin_types(sch) if sch else None
    fps = B['fps']
    by_ref = {f['ref']: f for f in fps}
    gnd_nets = {p['net'] for f in fps for p in f['pads'] if p['net'] and GND_NAME.match(p['net'])}
    findings = []

    def flag(check, sev, msg, where=None):
        findings.append(dict(check=check, severity=sev, msg=msg,
                             at=None if where is None else (round(where[0], 2), round(where[1], 2))))

    def is_power_pad(f, p):
        if not p['net'] or p['net'] in gnd_nets:
            return False
        if types is not None:
            return types.get((f['ref'], p['num']), ('',))[0] == 'power_in'
        return bool(POWER_NAME.search(p['net']))

    caps = [f for f in fps if re.match(r'^C\d', f['ref'])]

    def decaps_for(net):
        out = []
        for c in caps:
            nets = {p['net'] for p in c['pads']}
            if net in nets and nets & gnd_nets:
                out.append(c)
        return out

    # --- decoupling: every IC power pin
    for f in fps:
        if not re.match(r'^(U|IC)\d', f['ref']):
            continue
        for p in f['pads']:
            if not is_power_pad(f, p):
                continue
            cands = [(pad_edge_dist(p, cp), c['ref']) for c in decaps_for(p['net']) for cp in c['pads'] if cp['net'] == p['net']]
            if not cands:
                flag('decoupling', 'bad', '%s pin %s (%s): no decoupling cap on this net' % (f['ref'], p['num'], p['net']), (p['x'], p['y']))
                continue
            d, cref = min(cands)
            if d > CFG['decap_bad']:
                flag('decoupling', 'bad', '%s pin %s (%s): nearest cap %s is %.1f mm away' % (f['ref'], p['num'], p['net'], cref, d), (p['x'], p['y']))
            elif d > CFG['decap_warn']:
                flag('decoupling', 'warn', '%s pin %s (%s): nearest cap %s is %.1f mm away' % (f['ref'], p['num'], p['net'], cref, d), (p['x'], p['y']))

    # --- switching regulator loops: an IC that shares nets with both pads of an inductor
    for L in [f for f in fps if re.match(r'^L\d', f['ref'])]:
        lnets = {p['net'] for p in L['pads'] if p['net']}
        for f in fps:
            if not re.match(r'^(U|IC)\d', f['ref']):
                continue
            sw = [p for p in f['pads'] if p['net'] in lnets]
            if not sw:
                continue
            dL = min(pad_edge_dist(p, lp) for p in sw for lp in L['pads'] if lp['net'] == p['net'])
            if dL > CFG['loop_warn']:
                flag('switching', 'bad' if dL > CFG['loop_bad'] else 'warn',
                     '%s switch node to inductor %s: %.1f mm of trace path (keep the LX loop tiny)' % (f['ref'], L['ref'], dL), (L['x'], L['y']))
            # bulk caps on the regulator's power pins (VIN / VOUT side)
            for p in f['pads']:
                if p['net'] in lnets or p['net'] in gnd_nets or not p['net']:
                    continue
                if types is not None:
                    ptype, pfun = types.get((f['ref'], p['num']), ('', ''))
                    if ptype not in ('power_in', 'power_out') or not REG_PIN.search(pfun):
                        continue
                elif not is_power_pad(f, p):
                    continue
                bulk = [(pad_edge_dist(p, cp), c['ref']) for c in decaps_for(p['net']) if (cap_farads(c['value']) or 0) >= 1e-6
                        for cp in c['pads'] if cp['net'] == p['net']]
                if bulk:
                    d, cref = min(bulk)
                    if d > CFG['loop_warn']:
                        flag('switching', 'bad' if d > CFG['loop_bad'] else 'warn',
                             '%s pin %s (%s): nearest bulk cap %s is %.1f mm away (input/output loop)' % (f['ref'], p['num'], p['net'], cref, d), (p['x'], p['y']))

    # --- crystals
    for X in [f for f in fps if re.match(r'^(X|Y)\d', f['ref'])]:
        xnets = {p['net'] for p in X['pads'] if p['net'] and p['net'] not in gnd_nets}
        for p in X['pads']:
            if p['net'] not in xnets:
                continue
            lc = [(pad_edge_dist(p, cp), c['ref']) for c in decaps_for(p['net']) for cp in c['pads'] if cp['net'] == p['net']]
            if lc and min(lc)[0] > CFG['xtal_cap_warn']:
                flag('crystal', 'warn', '%s pad %s: load cap %s is %.1f mm away' % (X['ref'], p['num'], min(lc)[1], min(lc)[0]), (p['x'], p['y']))
        xs = [q['x'] for q in X['pads']]
        ys = [q['y'] for q in X['pads']]
        box = (min(xs) - 0.5, min(ys) - 0.5, max(xs) + 0.5, max(ys) + 0.5)
        own = xnets | gnd_nets
        # nets of resistors/caps directly on the crystal nets count as "own" (series R on XOUT)
        for f in fps:
            if any(q['net'] in xnets for q in f['pads']):
                own |= {q['net'] for q in f['pads'] if q['net']}
        foreign = defaultdict(set)
        for s in B['segs']:
            if s['net'] in own:
                continue
            for t in (0, 0.5, 1):
                x, y = s['x1'] + t * (s['x2'] - s['x1']), s['y1'] + t * (s['y2'] - s['y1'])
                if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                    foreign[s['net']].add(s['layer'])
        for v in B['vias']:
            if v['net'] not in own and box[0] <= v['x'] <= box[2] and box[1] <= v['y'] <= box[3]:
                foreign[v['net']].add('via')
        for n, ls in sorted(foreign.items()):
            flag('crystal', 'warn', '%s: foreign net %s runs inside the crystal area (%s)' % (X['ref'], n, ','.join(sorted(ls))), (X['x'], X['y']))

    # --- per-net copper
    length = defaultdict(float)
    nlayers = defaultdict(set)
    minw = {}
    for s in B['segs']:
        length[s['net']] += math.hypot(s['x2'] - s['x1'], s['y2'] - s['y1'])
        nlayers[s['net']].add(s['layer'])
        minw[s['net']] = min(minw.get(s['net'], 9), s['w'])
    nvias = defaultdict(int)
    for v in B['vias']:
        nvias[v['net']] += 1

    # --- differential pairs
    nets = set(length) | {p['net'] for f in fps for p in f['pads'] if p['net']}
    pairs = set()
    for n in nets:
        for a_, b_ in (('+', '-'), ('_P', '_N'), ('DP', 'DM'), ('_p', '_n')):
            if n and n.endswith(a_) and n[:-len(a_)] + b_ in nets:
                pairs.add((n, n[:-len(a_)] + b_))
            if n and a_ in ('DP',) and 'DP' in n and n.replace('DP', 'DM') in nets:
                pairs.add((n, n.replace('DP', 'DM')))
    # a pair broken by series resistors (USB 27 ohm) is one pair: join its segments into chains
    member = {n for pr in pairs for n in pr}
    link = {}
    for f in fps:
        pn = [p['net'] for p in f['pads'] if p['net']]
        if len(f['pads']) == 2 and len(set(pn)) == 2 and set(pn) <= member:
            a1, b1 = pn
            link.setdefault(a1, set()).add(b1)
            link.setdefault(b1, set()).add(a1)
    def chain(n):
        seen, todo = set(), [n]
        while todo:
            x = todo.pop()
            if x not in seen:
                seen.add(x)
                todo += list(link.get(x, ()))
        return frozenset(seen)
    merged = {}
    for p_, n_ in pairs:
        cp, cn = chain(p_), chain(n_)
        merged[(cp, cn)] = (p_, n_)
    for (cp, cn), (p_, n_) in sorted(merged.items(), key=lambda kv: kv[1]):
        if len(cp) > 1:
            lp, ln = sum(length[x] for x in cp), sum(length[x] for x in cn)
            dl = abs(lp - ln)
            if dl > CFG['dp_mismatch_warn']:
                flag('diff-pairs', 'bad' if dl > CFG['dp_mismatch_bad'] else 'warn',
                     'pair %s / %s (through series parts): length %.1f vs %.1f mm (mismatch %.1f mm)' % (
                         '+'.join(sorted(cp)), '+'.join(sorted(cn)), lp, ln, dl))
    for p_, n_ in sorted(pairs):
        if any(p_ in c[0] and len(c[0]) > 1 for c in merged):
            dl = 0  # length judged on the whole chain above
        else:
            dl = abs(length[p_] - length[n_])
        if dl > CFG['dp_mismatch_warn']:
            flag('diff-pairs', 'bad' if dl > CFG['dp_mismatch_bad'] else 'warn',
                 'pair %s / %s: length %.1f vs %.1f mm (mismatch %.1f mm)' % (p_, n_, length[p_], length[n_], dl))
        for n in (p_, n_):
            if nvias[n] > CFG['dp_vias_warn']:
                flag('diff-pairs', 'warn', '%s: %d vias (keep a pair on one layer, add GND vias beside any layer change)' % (n, nvias[n]))
        if nlayers[p_] != nlayers[n_]:
            flag('diff-pairs', 'warn', 'pair %s / %s uses different layers (%s vs %s)' % (p_, n_, sorted(nlayers[p_]), sorted(nlayers[n_])))

    # --- vias on clock / crystal nets
    xnet_all = {p['net'] for f in fps if re.match(r'^(X|Y)\d', f['ref']) for p in f['pads'] if p['net'] and p['net'] not in gnd_nets}
    for n in sorted(xnet_all | {n for n in nets if n and CLOCK_NAME.search(n) and not NOT_CLOCK.search(n)}):
        if nvias[n] > (0 if n in xnet_all else CFG['crit_vias_warn']):
            flag('critical-vias', 'warn', '%s: %d via(s) on a clock/crystal net' % (n, nvias[n]))

    # --- power widths
    # supply rails: driven by a power_out pin, an inductor, a diode or a fuse, or named like a rail; nets fed only
    # through a resistor (RC filters such as ADC_AVDD) and sense/enable nets carry no real current
    def parts_on(n):
        return {f['ref'] for f in fps for p in f['pads'] if p['net'] == n}
    pnets = set()
    for n in nets:
        if not n or n in gnd_nets or SIGNAL_LIKE.search(n):
            continue
        refs = parts_on(n)
        driven = any(re.match(r'^(L|D|F)\d', r) for r in refs) or (types is not None and any(
            types.get((f['ref'], p['num']), ('',))[0] == 'power_out' for f in fps for p in f['pads'] if p['net'] == n))
        filtered = any(re.match(r'^R\d', r) for r in refs) and not driven
        if (driven or POWER_NAME.search(n)) and not filtered:
            pnets.add(n)
    zoned = {z['net'] for z in B['zones']}
    for n in sorted(pnets):
        if n in minw and minw[n] < CFG['power_width'] - 1e-6:
            flag('power-width', 'info' if n in zoned else 'warn',
                 '%s: narrowest track %.3f mm (< %.2f mm)%s' % (n, minw[n], CFG['power_width'], '; net also has a plane' if n in zoned else ''))

    # --- fab margin
    small_v = [v for v in B['vias'] if v['size'] < fab['via_diameter'] - 1e-6 or v['drill'] < fab['via_drill'] - 1e-6]
    if small_v:
        sizes = defaultdict(int)
        for v in small_v:
            sizes['%.2f/%.2f' % (v['size'], v['drill'])] += 1
        flag('fab-margin', 'warn', '%d via(s) below the %s floor %.2f/%.2f mm: %s' % (
            len(small_v), fab['name'], fab['via_diameter'], fab['via_drill'], ', '.join('%s x%d' % kv for kv in sorted(sizes.items()))),
            (small_v[0]['x'], small_v[0]['y']))
    thin = [s for s in B['segs'] if s['w'] < fab['track'] - 1e-6]
    if thin:
        flag('fab-margin', 'warn', '%d track segment(s) narrower than %.3f mm (min %.4f) on %s' % (
            len(thin), fab['track'], min(s['w'] for s in thin), ', '.join(sorted({s['net'] for s in thin})[:8])),
            (thin[0]['x1'], thin[0]['y1']))

    # --- via in pad
    for v in B['vias']:
        for f in fps:
            for p in f['pads']:
                if p['kind'] != 'smd':
                    continue
                w, h = (p['w'], p['h']) if int(round((p['ang'] - f['rot']) / 90)) % 2 == 0 else (p['h'], p['w'])
                if abs(v['x'] - p['x']) < w / 2 and abs(v['y'] - p['y']) < h / 2:
                    flag('via-in-pad', 'warn', 'via (%s) inside %s pad %s: needs filled+capped vias (IPC-4761 VII) or solder wicks' % (v['net'], f['ref'], p['num']), (v['x'], v['y']))

    # --- geometry: acute corners and dangling ends
    ends = defaultdict(list)
    for s in B['segs']:
        for (x, y), (ox, oy) in (((s['x1'], s['y1']), (s['x2'], s['y2'])), ((s['x2'], s['y2']), (s['x1'], s['y1']))):
            ends[(s['net'], s['layer'], round(x, 3), round(y, 3))].append((ox - x, oy - y))
    plane_of = {(z['net'], ly) for z in B['zones'] for ly in z['layers']}
    acute = []
    for (n, ly, x, y), vs in ends.items():
        if (n, ly) in plane_of:
            continue  # short tap stubs inside the net's own pour
        if len(vs) == 2:
            (ax, ay), (bx, by) = vs
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            if la > 1e-6 and lb > 1e-6:
                ang = math.degrees(math.acos(max(-1, min(1, (ax * bx + ay * by) / (la * lb)))))
                if ang < 89.5:
                    acute.append((n, ly, x, y, ang))
    for n, ly, x, y, ang in acute[:15]:
        flag('geometry', 'warn', 'acute %.0f deg corner on %s (%s)' % (ang, n, ly), (x, y))
    if len(acute) > 15:
        flag('geometry', 'warn', '... %d more acute corners' % (len(acute) - 15))
    padpts = [(p['x'], p['y'], max(p['w'], p['h']) / 2 + 0.05, p['net']) for f in fps for p in f['pads']]
    for (n, ly, x, y), vs in ends.items():
        if len(vs) != 1 or (n, ly) in plane_of:
            continue
        if any(v['net'] == n and math.hypot(v['x'] - x, v['y'] - y) <= v['size'] / 2 + 0.01 for v in B['vias']):
            continue
        if any(pn == n and math.hypot(px - x, py - y) <= r for px, py, r, pn in padpts):
            continue
        if any((s2['net'] == n and s2['layer'] == ly) and _on_seg(x, y, s2) for s2 in B['segs']):
            continue  # T-junction into another segment
        flag('geometry', 'warn', 'dangling track end on %s (%s)' % (n, ly), (x, y))

    # --- stitching / planes
    if B['outline']:
        x0, y0, x1, y1 = B['outline']
        area_cm2 = abs(x1 - x0) * abs(y1 - y0) / 100
        g = [v for v in B['vias'] if v['net'] in gnd_nets]
        dens = len(g) / area_cm2 if area_cm2 else 0
        if dens < CFG['stitch_per_cm2']:
            flag('stitching', 'warn', '%d GND vias over %.1f cm^2 (%.2f/cm^2 < %.1f): add stitching vias' % (len(g), area_cm2, dens, CFG['stitch_per_cm2']))
        edge = [v for v in g if min(v['x'] - x0, x1 - v['x'], v['y'] - y0, y1 - v['y']) < 1.5]
        flag('stitching', 'info', '%d GND vias (%.2f/cm^2), %d within 1.5 mm of the board edge' % (len(g), dens, len(edge)))
    cu = [l for l, _ in B['layers']]
    # a plane is a zone covering a real share of the board; small local pours (regulator islands) are not planes
    board_area = abs((B['outline'][2] - B['outline'][0]) * (B['outline'][3] - B['outline'][1])) if B['outline'] else 0
    planes = [z for z in B['zones'] if not z['keepout'] and z['net'] and (not board_area or z['area'] >= 0.3 * board_area)]
    gplanes = {ly for z in planes if z['net'] in gnd_nets for ly in z['layers']}
    # tracks of other nets on a plane layer cut the plane (return paths of signals above it detour)
    plane_layers = {}
    for z in planes:
        for ly in z['layers']:
            plane_layers.setdefault(ly, set()).add(z['net'])
    cuts = defaultdict(float)
    for s in B['segs']:
        owners = plane_layers.get(s['layer'])
        if owners and s['net'] not in owners:
            cuts[(s['layer'], s['net'])] += math.hypot(s['x2'] - s['x1'], s['y2'] - s['y1'])
    for (ly, n), L_ in sorted(cuts.items(), key=lambda kv: -kv[1]):
        sev = 'bad' if L_ > 3 and plane_layers[ly] & gnd_nets else 'warn'
        flag('plane-cuts', sev, '%s routed %.1f mm on the %s plane layer %s: it slots the plane' % (n, L_, '/'.join(sorted(plane_layers[ly])), ly))
    order = sorted(cu, key=lambda l: (0 if l == 'F.Cu' else 2 if l == 'B.Cu' else 1, int(re.sub(r'\D', '', l) or 0)))
    for i, ly in enumerate(order):
        if not any(s['layer'] == ly for s in B['segs']):
            continue
        adj = [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order)]
        if not any(a_ in gplanes for a_ in adj) and ly not in gplanes:
            pplanes = {z['net'] for z in planes for a_ in adj if a_ in z['layers']}
            if pplanes:
                flag('planes', 'info', 'signals on %s reference a power plane (%s) on %s, not GND: fine if it is well decoupled; keep fast signals on the GND-referenced layer' % (ly, ', '.join(sorted(pplanes)), ', '.join(adj)))
            else:
                flag('planes', 'warn', 'signals on %s have no plane on an adjacent layer (%s): return paths will wander' % (ly, ', '.join(adj)))

    # --- assembly aids
    if not any('fiducial' in f['name'].lower() for f in fps):
        flag('assembly', 'info', 'no fiducials (fine for JLC SMT on small boards; add 2-3 for other assemblers)')
    if not any('testpoint' in f['name'].lower() or re.match(r'^TP\d', f['ref']) for f in fps):
        flag('assembly', 'info', 'no test points: consider pads on the power rails and key signals')

    order_sev = {'bad': 0, 'warn': 1, 'info': 2}
    findings.sort(key=lambda f: (order_sev[f['severity']], f['check']))
    counts = defaultdict(int)
    for f in findings:
        counts[f['severity']] += 1
        print('%-5s %-13s %s%s' % (f['severity'].upper(), f['check'], f['msg'], '  @ (%.2f, %.2f)' % f['at'] if f['at'] else ''))
    print('\n%d bad, %d warn, %d info  (%d footprints, %d tracks, %d vias; pin types from %s)' % (
        counts['bad'], counts['warn'], counts['info'], len(fps), len(B['segs']), len(B['vias']),
        'schematic' if types is not None else 'net names'))
    if '--json' in opt:
        json.dump(findings, open(opt['--json'], 'w'), indent=1)


def _on_seg(x, y, s, tol=0.01):
    dx, dy = s['x2'] - s['x1'], s['y2'] - s['y1']
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return False
    t = ((x - s['x1']) * dx + (y - s['y1']) * dy) / L2
    if t <= 1e-6 or t >= 1 - 1e-6:
        return False
    px, py = s['x1'] + t * dx, s['y1'] + t * dy
    return math.hypot(px - x, py - y) <= tol + s['w'] / 2


if __name__ == '__main__':
    main()
