#!/usr/bin/env python3
"""Check a .kicad_sch against schematic-quality signals and flag where it deviates (readability, information, correctness).

    sch-quality.py FILE.kicad_sch [--json OUT] [--no-erc]

Checks (each finding names the part / net / pin and the sheet position in mm):
  erc          kicad-cli ERC counts by type (run from the schematic folder so project libraries resolve)
  title-block  title, date and revision filled in
  notes        explanatory notes present (not just block headings)
  net-names    unnamed nets (`Net-(U1-XOUT)`) on IC / connector pins: name what you would probe
  wiring       parts drawn with no wires at all (labels straight on pins), 4-way wire junctions
  decoupling   every IC power pin net has at least one cap to GND; caps per IC vs supply pins
  ratings      bulk caps (>=1 uF): voltage rating vs the rail (DC-bias derating), rating shown in the value
  fields       footprint, LCSC and MPN on every assembled part; unannotated or duplicate references
  overlap      visible texts (reference, value, labels) that overlap (approximate boxes)
  datasheets   active parts to check against their datasheet's application circuit (see kicad-schematic-review)
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch import netlist  # noqa: E402
from kicad_sch.core import all_pins, key  # noqa: E402
from kicad_sch.sx import block_head, children  # noqa: E402

KICAD_CLI = os.environ.get('KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')
JLC_DB = os.environ.get('JLC_DB', os.path.expanduser('~/Library/Application Support/kicad-mcp/jlcpcb_parts.db'))
GND_NAME = re.compile(r'^/?(GND|AGND|DGND|PGND|GNDA|GNDD|VSS|0V)$', re.I)
ACTIVE = re.compile(r'^(U|IC|Q|X|Y|D|LED|L|F|J|SW)\d')


def rail_volts(net):
    """Best guess at a rail's voltage from its name (+3V3 -> 3.3, VBUS -> 5)."""
    n = net.lstrip('/+').upper()
    m = re.match(r'^(\d+)V(\d*)', n)
    if m:
        return float(m.group(1) + ('.' + m.group(2) if m.group(2) else ''))
    if re.search(r'VBUS|USB_5V|^5V|VSYS', n):
        return 5.0
    return None


def farads(v):
    m = re.match(r'\s*([\d.]+)\s*([pnuµm]?)', (v or '').replace(',', '.'))
    if not m:
        return None
    return float(m.group(1)) * {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'm': 1e-3, '': 1}[m.group(2)]


def kicad_netlist(sch):
    d, f = os.path.split(os.path.abspath(sch))
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, 'n.xml')
        r = subprocess.run([KICAD_CLI, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', out, f],
                           cwd=d, capture_output=True, text=True)
        if r.returncode or not os.path.exists(out):
            sys.exit('error: kicad-cli netlist export failed:\n' + r.stdout + r.stderr)
        root = ET.parse(out).getroot()
    comps = {}
    for c in root.find('components'):
        fields = {f.get('name'): (f.text or '') for f in c.iter('field')}
        lib = c.find('libsource')
        comps[c.get('ref')] = dict(value=c.findtext('value') or '', footprint=c.findtext('footprint') or '',
                                   datasheet=c.findtext('datasheet') or '', fields=fields,
                                   lib=(lib.get('lib') + ':' + lib.get('part')) if lib is not None else '',
                                   dnp=any(p.get('name') == 'dnp' for p in c.iter('property')),
                                   in_bom=not any(p.get('name') == 'exclude_from_bom' for p in c.iter('property')))
    nets = {}
    for n in root.find('nets'):
        nets[n.get('name')] = [(nd.get('ref'), nd.get('pin'), nd.get('pinfunction') or '', nd.get('pintype') or '') for nd in n]
    return comps, nets


def run_erc(sch):
    d, f = os.path.split(os.path.abspath(sch))
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, 'erc.json')
        subprocess.run([KICAD_CLI, 'sch', 'erc', '--severity-all', '--format', 'json', '-o', out, f], cwd=d,
                       capture_output=True, text=True)
        if not os.path.exists(out):
            return None
        data = json.load(open(out))
    vs = [v for s in data.get('sheets', []) for v in s.get('violations', [])]
    return vs


def jlc_desc(lcsc):
    if not lcsc or not os.path.exists(JLC_DB):
        return None
    try:
        con = sqlite3.connect('file:%s?mode=ro' % JLC_DB, uri=True)
        r = con.execute('select description, datasheet from components where lcsc = ?', (lcsc,)).fetchone()
        con.close()
        return r
    except sqlite3.Error:
        return None


def text_boxes(text):
    """Approximate boxes of visible symbol fields and labels: (owner, kind, x0, y0, x1, y1, string)."""
    boxes = []
    for sp in children(text, (0, len(text))):
        h, b = block_head(text, sp), text[sp[0]:sp[1]]
        if h == 'symbol' and '(lib_id' in b.split('(property')[0]:
            ref = re.search(r'\(property "Reference" "([^"]*)"', b).group(1)
            if ref.startswith('#'):
                continue
            for m in re.finditer(r'\(property "(Reference|Value)" "([^"]*)"\s*\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)(.*?)\n\t\t\)', b, re.S):
                if '(hide yes)' in m.group(6):
                    continue
                s, x, y, ang = m.group(2), float(m.group(3)), float(m.group(4)), float(m.group(5))
                size = float((re.search(r'\(size ([\d.]+)', m.group(6)) or [0, 1.27])[1])
                w, hgt = 0.6 * size * len(s), size
                if int(round(ang)) % 180 == 90:
                    w, hgt = hgt, w
                just = re.search(r'\(justify ([^)]*)\)', m.group(6))
                j = just.group(1) if just else ''
                x0 = x if 'left' in j else x - w if 'right' in j else x - w / 2
                if int(round(ang)) % 180 == 90:
                    x0 = x - w / 2
                    y0 = y - hgt if 'left' in j else y if 'right' in j else y - hgt / 2
                else:
                    y0 = y - hgt / 2
                boxes.append((ref, m.group(1), x0, y0, x0 + w, y0 + hgt, s))
        elif h in ('label', 'global_label'):
            m = re.match(r'\(%s "([^"]*)"\s*(?:\(shape [^)]*\)\s*)?\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)' % h, b)
            s, x, y, ang = m.group(1), float(m.group(2)), float(m.group(3)), int(float(m.group(4)))
            w, hgt = 0.6 * 1.27 * len(s), 1.27
            if ang == 0:
                bx = (x, y - hgt, x + w, y)
            elif ang == 180:
                bx = (x - w, y - hgt, x, y)
            elif ang == 90:
                bx = (x - hgt, y - w, x, y)
            else:
                bx = (x - hgt, y, x, y + w)
            boxes.append(('label@%g,%g' % (x, y), 'label', bx[0], bx[1], bx[2], bx[3], s))
    return boxes


def main():
    a = sys.argv[1:]
    out_json = None
    if '--json' in a:
        i = a.index('--json')
        out_json = a[i + 1]
        del a[i:i + 2]
    no_erc = '--no-erc' in a
    a = [x for x in a if x != '--no-erc']
    if len(a) != 1:
        sys.exit(__doc__)
    sch = a[0]
    text = open(sch).read()
    comps, nets = kicad_netlist(sch)
    sh, scomps, wires, labels, powers = netlist.load(sch)
    pos = {s['ref']: (s['x'], s['y']) for s in sh.symbols}
    findings = []

    def flag(check, sev, msg, ref=None):
        at = pos.get(ref) if ref else None
        findings.append(dict(check=check, severity=sev, msg=msg, at=at))

    # --- ERC
    if not no_erc:
        vs = run_erc(sch)
        if vs is None:
            flag('erc', 'warn', 'ERC did not run')
        else:
            c = Counter(v['type'] for v in vs)
            for t, n in c.most_common():
                sev = 'bad' if any(v['type'] == t and v.get('severity') == 'error' for v in vs) else 'warn'
                flag('erc', sev, '%d x %s: %s' % (n, t, next(v['description'] for v in vs if v['type'] == t)))

    # --- title block
    tb = re.search(r'\(title_block(.*?)\n\t\)', text, re.S)
    tbt = tb.group(1) if tb else ''
    for k in ('title', 'date', 'rev'):
        if not re.search(r'\(%s "[^"]+"' % k, tbt):
            flag('title-block', 'warn', 'title block has no %s' % k)

    # --- notes vs headings
    texts = [m.group(1) for m in re.finditer(r'\n\t\(text "((?:[^"\\]|\\.)*)"', text)]
    notes = [s for s in texts if len(s.split()) > 5 or '\\n' in s]
    if not notes:
        flag('notes', 'warn', '%d text item(s), all short headings: add notes for non-obvious choices (divider maths, strap '
             'states, DNP options, current budget, deviations from the reference design)' % len(texts))

    # --- unnamed nets on IC / connector pins
    for n, nodes in sorted(nets.items()):
        if not n.startswith('Net-('):
            continue
        ic = [(r, p, f) for r, p, f, t in nodes if re.match(r'^(U|IC|J|P)\d', r)]
        if ic:
            r, p, f = ic[0]
            fun = re.sub(r'_[A-Z]?\d+$', '', f) if f else 'pin %s' % p
            taken = fun.upper() in {x.lstrip('/').upper() for x in nets}
            flag('net-names', 'warn', '%s is unnamed but reaches %s %s: label it (e.g. %s)' % (
                n, r, fun, fun.upper() + ('_IN, since %s is taken' % fun.upper() if taken else '')), r)

    # --- wiring style
    pin_pts = defaultdict(set)
    for (ref, lid, x, y, rot, mir) in scomps:
        for (num, pt) in all_pins(sh.lib_pins, lid, x, y, rot, mir):
            pin_pts[ref].add(key(*pt))
    wire_pts = Counter()
    for (p1, p2) in wires:
        wire_pts[key(*p1)] += 1
        wire_pts[key(*p2)] += 1
    for ref, pts in sorted(pin_pts.items()):
        if ref.startswith('#') or not comps.get(ref):
            continue
        if not any(p in wire_pts for p in pts):
            flag('wiring', 'info', '%s has no wires: every pin is a label or power symbol (fine for headers; local parts read better wired)' % ref, ref)
    four = [k for k, c in wire_pts.items() if c >= 4]
    for k in four:
        flag('wiring', 'warn', '4-way wire junction at (%.2f, %.2f): offset one branch so a missing dot cannot be misread' % (k[0] / 100, k[1] / 100))

    # --- decoupling coverage
    gnd = {n for n in nets if GND_NAME.match(n)}
    cap_nets = defaultdict(set)
    for n, nodes in nets.items():
        for r, p, f, t in nodes:
            if re.match(r'^C\d', r):
                cap_nets[r].add(n)
    decap_on = defaultdict(list)
    for c, ns in cap_nets.items():
        if ns & gnd:
            for n in ns - gnd:
                decap_on[n].append(c)
    for ic in sorted(r for r in comps if re.match(r'^(U|IC)\d', r)):
        pnets = Counter(n for n, nodes in nets.items() for r, p, f, t in nodes if r == ic and t == 'power_in' and n not in gnd)
        for n, pins in pnets.items():
            caps = decap_on.get(n, [])
            if not caps:
                flag('decoupling', 'bad', '%s: %d supply pin(s) on %s and no cap to GND on that net' % (ic, pins, n), ic)
            elif len(caps) < pins and pins > 1:
                flag('decoupling', 'info', '%s: %d supply pins on %s, %d cap(s) on the net (%s): check one per pin group' % (
                    ic, pins, n, len(caps), ','.join(sorted(caps))), ic)

    # --- capacitor ratings
    def net_volts(n, seen=()):
        v = rail_volts(n)
        if v or n in seen:
            return v
        # a net fed through a 2-pin part (RC filter, fuse, inductor) from a named rail takes that rail's voltage
        for r, p, f, t in nets.get(n, []):
            if re.match(r'^(R|L|F|FB)\d', r):
                other = [m for m, nodes in nets.items() if m != n and any(x[0] == r for x in nodes)]
                for m in other:
                    v = net_volts(m, seen + (n,))
                    if v:
                        return v
        return None
    for c in sorted(cap_nets, key=lambda r: int(re.sub(r'\D', '', r) or 0)):
        info = comps.get(c, {})
        f = farads(info.get('value'))
        if f is None or f < 1e-6 or info.get('dnp'):
            continue
        rail = [n for n in cap_nets[c] if n not in gnd]
        v = max([net_volts(n) or 0 for n in rail] or [0]) or None
        d = jlc_desc(info['fields'].get('LCSC'))
        rated = None
        if d:
            m = re.search(r'(\d+(?:\.\d+)?)\s*V\b', d[0])
            rated = float(m.group(1)) if m else None
            diel = re.search(r'\b(X5R|X7R|X6S|X7S|C0G|NP0|Y5V)\b', d[0])
        if not re.search(r'\d\s*V\b', info['value']) and 'Voltage' not in info['fields']:
            flag('ratings', 'info', '%s %s on %s: voltage rating not shown in the value (e.g. "%s/%gV")' % (
                c, info['value'], ','.join(rail), info['value'], rated or 10), c)
        if rated and v:
            if rated < v:
                flag('ratings', 'bad', '%s %s rated %gV on a %gV rail' % (c, info['value'], rated, v), c)
            elif rated < 2 * v and (not d or not diel or diel.group(1) not in ('C0G', 'NP0')):
                flag('ratings', 'warn', '%s %s %s rated %gV on %s (~%gV): class II ceramics lose roughly half their capacitance '
                     'at this ratio; check effective value against the datasheet minimum' % (
                         c, info['value'], diel.group(1) if d and diel else '', rated, ','.join(rail), v), c)

    # --- fields and references
    refs = Counter(re.sub(r'\?$', '', r) for r in comps)
    for r in comps:
        if r.endswith('?'):
            flag('fields', 'bad', '%s is not annotated' % r, r)
    for r, n in refs.items():
        if n > 1:
            flag('fields', 'bad', 'reference %s used %d times' % (r, n), r)
    for r, info in sorted(comps.items()):
        if r.startswith('#') or info['lib'].startswith('power:') or info['dnp']:
            continue
        missing = [k for k in ('LCSC', 'MPN') if not info['fields'].get(k)]
        if not info['footprint']:
            missing.insert(0, 'footprint')
        if missing and not re.match(r'^(H|TP|MH|FID)\d', r):
            flag('fields', 'warn', '%s (%s): missing %s' % (r, info['value'], ', '.join(missing)), r)

    # --- overlapping texts
    boxes = text_boxes(text)
    hits = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            A, B = boxes[i], boxes[j]
            if A[0] == B[0]:
                continue
            if A[2] < B[4] - 0.05 and B[2] < A[4] - 0.05 and A[3] < B[5] - 0.05 and B[3] < A[5] - 0.05:
                hits += 1
                if hits <= 12:
                    findings.append(dict(check='overlap', severity='warn', at=(round(A[2], 1), round(A[3], 1)),
                                         msg='"%s" (%s %s) overlaps "%s" (%s %s) [approx]' % (A[6], A[0], A[1], B[6], B[0], B[1])))
    if hits > 12:
        flag('overlap', 'warn', '... %d more overlapping text pairs' % (hits - 12))

    # --- datasheet checklist
    for r, info in sorted(comps.items()):
        if not ACTIVE.match(r) or info['dnp'] or re.match(r'^(J|SW)\d', r) and not info['datasheet']:
            continue
        lc = info['fields'].get('LCSC', '')
        ds = info['datasheet'] if info['datasheet'] not in ('', '~') else ((jlc_desc(lc) or ['', ''])[1] or '')
        flag('datasheets', 'info', '%s %s (%s, LCSC %s): compare with the datasheet application circuit%s' % (
            r, info['value'], info['fields'].get('MPN', ''), lc or '-', (': ' + ds) if ds else ' (no datasheet link)'), r)

    sev_order = {'bad': 0, 'warn': 1, 'info': 2}
    findings.sort(key=lambda f: (sev_order[f['severity']], f['check']))
    cnt = Counter(f['severity'] for f in findings)
    for f in findings:
        at = '  @ (%.1f, %.1f)' % tuple(f['at']) if f.get('at') else ''
        print('%-5s %-12s %s%s' % (f['severity'].upper(), f['check'], f['msg'], at))
    print('\n%d bad, %d warn, %d info  (%d parts, %d nets)' % (cnt['bad'], cnt['warn'], cnt['info'], len(comps), len(nets)))
    if out_json:
        json.dump(findings, open(out_json, 'w'), indent=1)


if __name__ == '__main__':
    main()
