#!/usr/bin/env python3
"""Update a .kicad_pcb from its schematic at file level, like KiCad's "Update PCB from Schematic" (F8).

    pcb-sync-from-sch.py FILE.kicad_sch FILE.kicad_pcb [--place REF=X,Y[,ROT] ...] [--dry-run] [--force]

For every schematic part (matched to a footprint by the symbol uuid in the footprint's `(path ...)`, else by
reference): updates the Value, sets the net on every pad, and carries over what KiCad's own update does: the
footprint's library id (`lib:name` as the symbol gives it), every other symbol field (LCSC, MPN, Voltage, ...) as a
footprint property, and the DNP / exclude-from-BOM attributes (so `kcli pcb drc --schematic-parity` is clean and
`pcb export pos --exclude-dnp` leaves DNP parts out). A part with no footprint on the board is added from
its footprint library (project fp-lib-table first, then KiCad's own) at --place X,Y,ROT (default: just left of
the board outline). Net names come from `kicad-cli sch export netlist`, so they match what KiCad itself writes
(`/GP0`, `Net-(U1-XOUT)`, `unconnected-(J1-SBU1-PadA8)`). Copper follows the pads: when every pad of an old net
moves to one new net (a net renamed or newly labelled in the schematic) its tracks, vias and zones are renamed too;
when a pad leaves a net that still exists (e.g. FB moved off +3V3 onto a divider) the old net's tracks ending on that
pad, and the dead-end chain behind them, are deleted so the router can re-route it. A footprint changed in the
schematic is rebuilt from the library in place (same position, rotation and schematic link; front side only).
Footprints are never removed: board-only footprints (mounting
holes) and footprints whose symbol is gone are listed. Refuses while KiCad has the board open (`~NAME.kicad_pcb.lck`)
unless --force. Timestamped backup in `.backups/`, atomic write.

Why: the KiCad MCP `sync_schematic_to_board` renames nets (`Net-(U1-Pad58)`) and leaves pads without a net.
"""
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid as uuidlib
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch.sx import block_head, children  # noqa: E402

KICAD_APP = os.environ.get('KICAD_APP', '/Applications/KiCad/KiCad.app')
KICAD_CLI = os.environ.get('KICAD_CLI', KICAD_APP + '/Contents/MacOS/kicad-cli')
STD_FP = KICAD_APP + '/Contents/SharedSupport/footprints'


def new_uuid():
    return str(uuidlib.uuid4())


def netlist(sch):
    d, f = os.path.split(os.path.abspath(sch))
    with tempfile.TemporaryDirectory() as t:
        xml = os.path.join(t, 'n.xml')
        r = subprocess.run([KICAD_CLI, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', xml, f],
                           cwd=d, capture_output=True, text=True)
        if r.returncode or not os.path.exists(xml):
            sys.exit('error: kicad-cli netlist export failed:\n' + r.stdout + r.stderr)
        root = ET.parse(xml).getroot()
    parts = {}
    for c in root.find('components'):
        ref = c.get('ref')
        lib = c.find('libsource')
        if ref.startswith('#') or (lib is not None and lib.get('part') == 'PWR_FLAG'):
            continue
        fields = {f.get('name'): f.text or '' for f in (c.find('fields') if c.find('fields') is not None else [])}
        props = {q.get('name') for q in c.findall('property')}
        parts[ref] = dict(value=c.findtext('value') or '', footprint=c.findtext('footprint') or '',
                          uuid=(c.findtext('tstamps') or '').split('/')[-1], pads={}, fields=fields,
                          dnp='dnp' in props, exclude_bom='exclude_from_bom' in props)
    for n in root.find('nets'):
        for nd in n:
            if nd.get('ref') in parts:
                parts[nd.get('ref')]['pads'][nd.get('pin')] = n.get('name')
    return parts


def fp_libs(project_dir):
    """{libname: folder} from the project fp-lib-table (${KIPRJMOD} expanded)."""
    out = {}
    tbl = os.path.join(project_dir, 'fp-lib-table')
    if os.path.exists(tbl):
        for m in re.finditer(r'\(name "([^"]+)"\)\s*\(type "[^"]*"\)\s*\(uri "([^"]+)"\)', open(tbl).read()):
            out[m.group(1)] = m.group(2).replace('${KIPRJMOD}', project_dir)
    return out


def find_mod(fp, project_dir):
    lib, _, name = fp.partition(':')
    libs = fp_libs(project_dir)
    for folder in (libs.get(lib), os.path.join(STD_FP, lib + '.pretty')):
        if folder and os.path.exists(os.path.join(folder, name + '.kicad_mod')):
            return os.path.join(folder, name + '.kicad_mod')
    sys.exit('error: footprint %s not found in the project or KiCad libraries' % fp)


def footprint_from_mod(fp, ref, value, sym_uuid, at, project_dir, pads):
    text = open(find_mod(fp, project_dir)).read()
    body = text.strip()
    body = re.sub(r'\n\t\(version [^)]*\)', '', body, count=1)
    body = re.sub(r'\n\t\(generator [^)]*\)', '', body)
    body = re.sub(r'\n\t\(generator_version [^)]*\)', '', body)
    body = re.sub(r'\(footprint "[^"]*"', '(footprint "%s"' % fp, body, count=1)
    body = re.sub(r'\(uuid "[^"]*"\)', lambda m: '(uuid "%s")' % new_uuid(), body)
    # position after the footprint's own (layer ...) and uuid
    x, y, rot = at
    at_s = '(at %s %s%s)' % (x, y, (' %s' % rot) if rot else '')
    body = re.sub(r'(\n\t\(layer "F\.Cu"\))', r'\1\n\t(uuid "%s")\n\t%s' % (new_uuid(), at_s), body, count=1)
    body = re.sub(r'(\(property "Reference" ")[^"]*', lambda m: m.group(1) + ref, body, count=1)
    body = re.sub(r'(\(property "Value" ")[^"]*', lambda m: m.group(1) + value, body, count=1)
    # link to the schematic symbol so a later F8 matches it
    body = re.sub(r'(\n\t\(attr )', r'\n\t(path "/%s")\1' % sym_uuid, body, count=1)
    if '(path "/' not in body:
        body = body.replace('\n\t(descr', '\n\t(path "/%s")\n\t(descr' % sym_uuid, 1)
    # indent one level: the board holds footprints at depth 1, the library file at depth 0 -> add a tab
    body = '\n'.join('\t' + l if l else l for l in body.split('\n'))
    def pad_uuid(m):
        pb = m.group(0)
        return pb if '(uuid ' in pb else pb[:-len('\n\t\t)')] + '\n\t\t\t(uuid "%s")\n\t\t)' % new_uuid()
    body = re.sub(r'\n\t\t\(pad "[^"]*".*?\n\t\t\)', pad_uuid, body, flags=re.S)
    return body


MOVES = []  # (footprint block before the edit, pad number, old net, new net) for every pad whose net changed


def set_pad_nets(block, pads):
    """Rewrite the (net ...) of every pad in a footprint block; returns (new_block, changes)."""
    changes = []
    out, last = [], 0
    for sp in children(block, (0, len(block))):
        if block_head(block, sp) != 'pad':
            continue
        pb = block[sp[0]:sp[1]]
        num = re.match(r'\(pad "([^"]*)"', pb).group(1)
        if not num:
            continue
        want = pads.get(num)
        have = re.search(r'\n\t\t\t\(net "((?:[^"\\]|\\.)*)"\)', pb)
        have = have.group(1) if have else None
        if want == have:
            continue
        if have is not None:
            pb2 = re.sub(r'\n\t\t\t\(net "(?:[^"\\]|\\.)*"\)', ('\n\t\t\t(net "%s")' % want) if want else '', pb, count=1)
        else:
            pb2 = re.sub(r'(\n\t\t\t\(uuid )', lambda m: '\n\t\t\t(net "%s")' % want + m.group(1), pb, count=1)
        changes.append('pad %s: %s -> %s' % (num, have, want))
        MOVES.append((block, num, have, want))
        out.append((sp, pb2))
    for (a, b), pb2 in reversed(out):
        block = block[:a] + pb2 + block[b:]
    return block, changes


def pad_boxes(block, num):
    """Absolute (x, y, half_w, half_h) of every pad `num` of a board footprint block (rotation folded into a bbox)."""
    at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', block)
    x0, y0, r0 = float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)
    th = math.radians(r0)
    out = []
    for sp in children(block, (0, len(block))):
        if block_head(block, sp) != 'pad':
            continue
        pb = block[sp[0]:sp[1]]
        if re.match(r'\(pad "([^"]*)"', pb).group(1) != num:
            continue
        m = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', pb)
        sz = re.search(r'\(size ([-\d.]+) ([-\d.]+)\)', pb)
        lx, ly = float(m.group(1)), float(m.group(2))
        w, h = float(sz.group(1)) / 2, float(sz.group(2)) / 2
        if int(round(float(m.group(3) or 0) / 90)) % 2:
            w, h = h, w
        out.append((x0 + lx * math.cos(th) + ly * math.sin(th), y0 - lx * math.sin(th) + ly * math.cos(th), w, h))
    return out


def copper(text):
    """Top-level segments/arcs/vias: list of dict(kind, span, net, pts)."""
    out = []
    for sp in children(text, (0, len(text))):
        h = block_head(text, sp)
        if h not in ('segment', 'arc', 'via'):
            continue
        b = text[sp[0]:sp[1]]
        n = re.search(r'\(net (?:\d+ )?"((?:[^"\\]|\\.)*)"\)', b)
        if h == 'via':
            m = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', b)
            pts = [(float(m.group(1)), float(m.group(2)))]
        else:
            pts = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(r'\((?:start|end) ([-\d.]+) ([-\d.]+)\)', b)]
        out.append(dict(kind=h, span=sp, net=n.group(1) if n else '', pts=pts))
    return out


def rename_and_rip(text, report):
    """Carry pad net changes over to the copper: rename an old net's tracks/vias/zones when all its pads moved to
    one new net; otherwise delete the old net's tracks that end on a pad that left it, and the dead-end chain behind
    them (the router re-routes the pad on its new net)."""
    if not MOVES:
        return text
    remaining = set(re.findall(r'\n\t\t\t\(net "((?:[^"\\]|\\.)*)"\)', ''.join(
        text[s[0]:s[1]] for s in children(text, (0, len(text))) if block_head(text, s) == 'footprint')))
    targets = defaultdict(set)
    for _, _, old, new in MOVES:
        if old:
            targets[old].add(new)
    renames = {o: next(iter(ns)) for o, ns in targets.items() if o not in remaining and len(ns) == 1 and next(iter(ns))}
    for o, n in sorted(renames.items()):
        cnt = len(re.findall(r'\(net "%s"\)' % re.escape(o), text))
        text = re.sub(r'\(net "%s"\)' % re.escape(o), '(net "%s")' % n.replace('\\', '\\\\'), text)
        report.append('RENAME copper %s -> %s (%d items)' % (o, n, cnt))
    # rip copper of an old net that still exists, where it lands on a pad that moved away
    cu = copper(text)
    kill = set()
    for blk, num, old, new in MOVES:
        if not old or old in renames:
            continue
        before = len(kill)
        boxes = pad_boxes(blk, num)
        def on_pad(p):
            return any(abs(p[0] - x) <= w + 0.01 and abs(p[1] - y) <= h + 0.01 for x, y, w, h in boxes)
        frontier = []
        for i, c in enumerate(cu):
            if c['net'] == old and any(on_pad(p) for p in c['pts']):
                kill.add(i)
                frontier += [p for p in c['pts'] if not on_pad(p)]
        # follow the dead-end chain until it reaches a branch, another pad of the net or a plane via
        k = lambda p: (round(p[0], 3), round(p[1], 3))
        while frontier:
            p = frontier.pop()
            touching = [i for i, c in enumerate(cu) if i not in kill and c['net'] == old and any(k(q) == k(p) for q in c['pts'])]
            if len(touching) != 1:
                continue
            i = touching[0]
            if cu[i]['kind'] == 'via':
                # a via that only carried this stub: drop it and keep walking on the other layers
                kill.add(i)
                frontier.append(p)
                continue
            kill.add(i)
            frontier += [q for q in cu[i]['pts'] if k(q) != k(p)]
        report.append('RIP %s copper on %s pad %s (pad moved to %s): %d item(s)' % (old, re.search(r'\(property "Reference" "([^"]*)"', blk).group(1), num, new, len(kill) - before))
    ripped = {cu[i]['net'] for i in kill}
    for i in sorted(kill, key=lambda i: -cu[i]['span'][0]):
        a, b = cu[i]['span']
        text = text[:a] + text[b:]
    text = re.sub(r'\n\t\n', '\n', text)
    for net in sorted(ripped):
        text = prune_dangling(text, net, report)
    return text


def net_anchors(text, net):
    """Pad boxes (x, y, half_w, half_h, layers) of every pad on `net`, and the copper layers with a zone of `net`."""
    pads = []
    for sp in children(text, (0, len(text))):
        if block_head(text, sp) != 'footprint':
            continue
        b = text[sp[0]:sp[1]]
        for p in children(b, (0, len(b))):
            if block_head(b, p) != 'pad':
                continue
            pb = b[p[0]:p[1]]
            if '(net "%s")' % net not in pb:
                continue
            num = re.match(r'\(pad "([^"]*)"', pb).group(1)
            layers = re.findall(r'"([^"]+\.Cu|\*\.Cu)"', re.search(r'\(layers ([^)]*)\)', pb).group(1))
            pads += [bx + (layers,) for bx in pad_boxes(b, num)]
    zone_layers = set()
    for sp in children(text, (0, len(text))):
        if block_head(text, sp) == 'zone' and '(net "%s")' % net in text[sp[0]:sp[1]]:
            zone_layers |= set(re.findall(r'"([^"]+\.Cu)"', re.search(r'\(layers? ([^)]*)\)', text[sp[0]:sp[1]]).group(1)))
    return pads, zone_layers


def prune_dangling(text, net, report):
    """After a rip, remove what it left hanging on `net`: vias that no longer join two things, and track ends that
    touch nothing (a segment is trimmed back to the last point where other copper joins it, or deleted). Copper ending
    in the net's own zone is a plane tap and stays."""
    pads, zone_layers = net_anchors(text, net)
    def on_pad(p, layer):
        return any(abs(p[0] - x) <= w + 0.01 and abs(p[1] - y) <= h + 0.01 and (layer in ls or '*.Cu' in ls)
                   for x, y, w, h, ls in pads)
    def near(a, b):
        return abs(a[0] - b[0]) < 0.005 and abs(a[1] - b[1]) < 0.005
    def on_interior(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 < 1e-12:
            return False
        u = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
        if u <= 1e-6 or u >= 1 - 1e-6:
            return False
        return math.hypot(a[0] + u * dx - p[0], a[1] + u * dy - p[1]) < 0.005
    removed = trimmed = 0
    for _ in range(50):
        segs, vias = [], []
        for sp in children(text, (0, len(text))):
            h = block_head(text, sp)
            b = text[sp[0]:sp[1]]
            if h not in ('segment', 'via') or '(net "%s")' % net not in b:
                continue
            if h == 'via':
                m = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', b)
                vias.append((sp, (float(m.group(1)), float(m.group(2)))))
            else:
                pts = [(float(a), float(c)) for a, c in re.findall(r'\((?:start|end) ([-\d.]+) ([-\d.]+)\)', b)]
                segs.append((sp, pts[0], pts[1], re.search(r'\(layer "([^"]+)"\)', b).group(1)))
        change = None
        for sp, v in vias:
            joins = sum(1 for _, a, b, _ in segs if near(v, a) or near(v, b) or on_interior(v, a, b))
            joins += 1 if any(abs(v[0] - x) <= w and abs(v[1] - y) <= h for x, y, w, h, _ in pads) else 0
            if joins <= 1 and not zone_layers:
                change = ('del', sp)
                break
        if change is None:
            for idx, (sp, a, b, ly) in enumerate(segs):
                if ly in zone_layers:
                    continue
                for e, f in ((a, b), (b, a)):
                    others = [s for j, s in enumerate(segs) if j != idx]
                    if (any(near(e, s[1]) or near(e, s[2]) or on_interior(e, s[1], s[2]) for s in others if s[3] == ly)
                            or any(near(e, v) for _, v in vias) or on_pad(e, ly)):
                        continue
                    # e is a free end: keep the segment up to the touching point closest to e, if any
                    touches = [p for s in others if s[3] == ly for p in (s[1], s[2]) if on_interior(p, a, b)]
                    touches += [v for _, v in vias if on_interior(v, a, b)]
                    if touches:
                        cut = min(touches, key=lambda p: math.hypot(p[0] - e[0], p[1] - e[1]))
                        change = ('trim', sp, e, cut)
                    else:
                        change = ('del', sp)
                    break
                if change:
                    break
        if change is None:
            break
        sp = change[1]
        if change[0] == 'del':
            text = text[:sp[0]] + text[sp[1]:]
            removed += 1
        else:
            b = text[sp[0]:sp[1]]
            e, cut = change[2], change[3]
            for tag in ('start', 'end'):
                m = re.search(r'\(%s ([-\d.]+) ([-\d.]+)\)' % tag, b)
                if near((float(m.group(1)), float(m.group(2))), e):
                    b = b[:m.start()] + '(%s %.6f %.6f)' % (tag, cut[0], cut[1]) + b[m.end():]
                    break
            text = text[:sp[0]] + b + text[sp[1]:]
            trimmed += 1
        text = re.sub(r'\n\t\n', '\n', text)
    if removed or trimmed:
        report.append('CLEAN %s: removed %d dangling item(s), trimmed %d stub(s)' % (net, removed, trimmed))
    return text


SKIP_FIELDS = {'Reference', 'Value', 'Footprint'}


def sync_meta(block, p, report, ref):
    """What KiCad's Update PCB also carries over besides nets and value: the footprint's library id (lib:name, as
    the symbol gives it), every other symbol field as a footprint property (hidden on Fab when new), and the
    DNP / exclude-from-BOM attributes."""
    out = block
    m = re.match(r'\(footprint "([^"]+)"', out)
    if m and p['footprint'] and m.group(1) != p['footprint'] and m.group(1).split(':')[-1] == p['footprint'].split(':')[-1]:
        out = out[:m.start(1)] + p['footprint'] + out[m.end(1):]
        report.append('%s footprint id: %s -> %s' % (ref, m.group(1), p['footprint']))
    back = '(layer "B.Cu")' in out.split('\n')[1] if '\n' in out else False
    for name, val in sorted(p['fields'].items()):
        if name in SKIP_FIELDS:
            continue
        esc = val.replace('\\', '\\\\').replace('"', '\\"')
        pm = re.search(r'\(property "%s" "((?:[^"\\]|\\.)*)"' % re.escape(name), out)
        if pm:
            if pm.group(1) != esc:
                out = out[:pm.start(1)] + esc + out[pm.end(1):]
                report.append('%s field %s: %r -> %r' % (ref, name, pm.group(1), val))
            continue
        vi = out.find('\t\t(property "Value"')
        if vi < 0:
            continue
        depth, j = 0, vi + 2
        while j < len(out):
            if out[j] == '(':
                depth += 1
            elif out[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        prop = ('\n\t\t(property "%s" "%s"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "%s")\n\t\t\t(hide yes)\n'
                '\t\t\t(uuid "%s")\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)\n'
                '\t\t\t\t)%s\n\t\t\t)\n\t\t)') % (name, esc, 'B.Fab' if back else 'F.Fab', new_uuid(),
                                         '\n\t\t\t\t(justify mirror)' if back else '')
        out = out[:j + 1] + prop + out[j + 1:]
        report.append('%s field %s added: %r' % (ref, name, val))
    am = re.search(r'\n\t\t\(attr([^)]*)\)', out)
    want = []
    if p['exclude_bom']:            # KiCad compares each flag on its own; DNP does not imply it
        want.append('exclude_from_bom')
    if p['dnp']:
        want.append('dnp')
    if am:
        toks = [t for t in am.group(1).split() if t not in ('exclude_from_bom', 'dnp')] + want
        new = '\n\t\t(attr %s)' % ' '.join(toks)
        if new != am.group(0):
            out = out[:am.start()] + new + out[am.end():]
            report.append('%s attributes: %s' % (ref, ' '.join(toks)))
    return out


def main():
    a = sys.argv[1:]
    dry, force = '--dry-run' in a, '--force' in a
    place = {}
    while '--place' in a:
        i = a.index('--place')
        ref, _, xyz = a[i + 1].partition('=')
        v = [float(s) for s in xyz.split(',')]
        place[ref] = (v[0], v[1], v[2] if len(v) > 2 else 0)
        del a[i:i + 2]
    a = [x for x in a if x not in ('--dry-run', '--force')]
    if len(a) != 2:
        sys.exit(__doc__)
    sch, pcb = a
    d, base = os.path.split(os.path.abspath(pcb))
    lock = os.path.join(d, '~' + base + '.lck')
    if os.path.exists(lock) and not force and not dry:
        sys.exit('error: KiCad has %s open (%s). Close the PCB editor first; --force to edit anyway.' % (base, os.path.basename(lock)))
    parts = netlist(sch)
    text = open(pcb).read()
    top = children(text, (0, len(text)))
    by_uuid, by_ref, spans = {}, {}, []
    for sp in top:
        if block_head(text, sp) != 'footprint':
            continue
        b = text[sp[0]:sp[1]]
        ref = re.search(r'\(property "Reference" "([^"]*)"', b).group(1)
        m = re.search(r'\(path "/([^"]+)"\)', b)
        rec = dict(span=sp, ref=ref)
        spans.append(rec)
        by_ref[ref] = rec
        if m:
            by_uuid[m.group(1).split('/')[-1]] = rec
    edits, added, report = [], [], []
    matched = set()
    for ref, p in sorted(parts.items()):
        rec = by_uuid.get(p['uuid']) or by_ref.get(ref)
        if rec is None:
            if not p['footprint']:
                report.append('%s has no footprint assigned in the schematic: skipped' % ref)
                continue
            x, y, rot = place.get(ref, (95.0, 105.0 + 6 * len(added), 0))
            blk = footprint_from_mod(p['footprint'], ref, p['value'], p['uuid'], (x, y, rot), d, p['pads'])
            blk, ch = set_pad_nets(blk.lstrip('\t'), p['pads'])
            blk = '\t' + sync_meta(blk.lstrip('\t'), p, [], ref)
            added.append((ref, p['footprint'], (x, y, rot), len(ch)))
            report.append('ADD %s %s at %s,%s rot %s (%d pads)' % (ref, p['footprint'], x, y, rot, len(ch)))
            edits.append(('add', blk))
            continue
        matched.add(rec['ref'])
        sp = rec['span']
        b = text[sp[0]:sp[1]]
        have_fp = re.match(r'\(footprint "([^"]+)"', b).group(1)
        if p['footprint'] and have_fp.split(':')[-1] != p['footprint'].split(':')[-1]:
            # footprint changed in the schematic: rebuild it from the library in place (same position, rotation and
            # schematic link); tracks that ended on the old pads are kept, DRC/the router judge whether they still fit
            at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', b)
            if '(layer "B.Cu")' in b.split('\n')[1]:
                report.append('%s: footprint %s -> %s NOT swapped: the part is on the back; flip it to the front, re-run, '
                              'then flip back' % (ref, have_fp, p['footprint']))
                continue
            x, y, rot = float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)
            blk = footprint_from_mod(p['footprint'], ref, p['value'], p['uuid'], (x, y, rot), d, p['pads'])
            blk, ch = set_pad_nets(blk.lstrip('\t'), p['pads'])
            blk = sync_meta(blk, p, [], ref)
            report.append('SWAP %s %s -> %s at %s,%s rot %s (%d pads)' % (ref, have_fp, p['footprint'], x, y, rot, len(ch)))
            edits.append(('rep', sp, blk))
            continue
        b2, ch = set_pad_nets(b, p['pads'])
        vm = re.search(r'\(property "Value" "((?:[^"\\]|\\.)*)"', b2)
        if vm and vm.group(1) != p['value']:
            report.append('%s value: %r -> %r' % (ref, vm.group(1), p['value']))
            b2 = b2[:vm.start(1)] + p['value'] + b2[vm.end(1):]
        if ch:
            report.append('%s pads: %s' % (ref, '; '.join(ch)))
        b2 = sync_meta(b2, p, report, ref)
        if b2 != b:
            edits.append(('rep', sp, b2))
    for rec in spans:
        if rec['ref'] not in matched and rec['ref'] not in parts:
            report.append('note: %s is on the board only (no schematic symbol)' % rec['ref'])
    print('\n'.join(report) or 'nothing to change')
    if dry:
        if MOVES:
            print('(dry run: copper of changed nets is renamed or ripped on a real run)')
        return
    if not edits:
        return
    new = text
    for e in sorted([e for e in edits if e[0] == 'rep'], key=lambda e: -e[1][0]):
        new = new[:e[1][0]] + e[2] + new[e[1][1]:]
    adds = ''.join('\n' + e[1] for e in edits if e[0] == 'add')
    if adds:
        i = new.index('\n\t(embedded_fonts') if '\n\t(embedded_fonts' in new else new.rindex('\n)')
        new = new[:i] + adds + new[i:]
    new = rename_and_rip(new, report)
    print('\n'.join(r for r in report if r.startswith(('RENAME', 'RIP', 'CLEAN'))))
    def count(s, kinds):
        return sum(1 for sp in children(s, (0, len(s))) if block_head(s, sp) in kinds)
    if count(new, ('footprint', 'zone')) < count(text, ('footprint', 'zone')):
        sys.exit('error: result lost top-level blocks; nothing written')
    bdir = os.path.join(d, '.backups')
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(pcb, os.path.join(bdir, '%s.%s' % (base, time.strftime('%Y%m%d-%H%M%S'))))
    tmpf = os.path.join(d, '.%s.tmp' % base)
    open(tmpf, 'w').write(new)
    os.replace(tmpf, pcb)
    print('wrote %s' % pcb)


if __name__ == '__main__':
    main()
