#!/usr/bin/env python3
"""Update a .kicad_pcb from its schematic at file level, like KiCad's "Update PCB from Schematic" (F8).

    pcb-sync-from-sch.py FILE.kicad_sch FILE.kicad_pcb [--place REF=X,Y[,ROT] ...] [--dry-run] [--force]

For every schematic part (matched to a footprint by the symbol uuid in the footprint's `(path ...)`, else by
reference): updates the Value, and sets the net on every pad. A part with no footprint on the board is added from
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
        parts[ref] = dict(value=c.findtext('value') or '', footprint=c.findtext('footprint') or '',
                          uuid=(c.findtext('tstamps') or '').split('/')[-1], pads={})
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
    for i in sorted(kill, key=lambda i: -cu[i]['span'][0]):
        a, b = cu[i]['span']
        text = text[:a] + text[b:]
    text = re.sub(r'\n\t\n', '\n', text)
    return text


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
            blk = '\t' + blk
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
    print('\n'.join(r for r in report if r.startswith(('RENAME', 'RIP'))))
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
