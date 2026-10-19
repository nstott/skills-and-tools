#!/usr/bin/env python3
"""Update a .kicad_pcb from its schematic at file level, like KiCad's "Update PCB from Schematic" (F8).

    pcb-sync-from-sch.py FILE.kicad_sch FILE.kicad_pcb [--place REF=X,Y[,ROT] ...] [--dry-run] [--force]

For every schematic part (matched to a footprint by the symbol uuid in the footprint's `(path ...)`, else by
reference): updates the Value, and sets the net on every pad. A part with no footprint on the board is added from
its footprint library (project fp-lib-table first, then KiCad's own) at --place X,Y,ROT (default: just left of
the board outline). Net names come from `kicad-cli sch export netlist`, so they match what KiCad itself writes
(`/GP0`, `Net-(U1-XOUT)`, `unconnected-(J1-SBU1-PadA8)`). Nothing is removed: board-only footprints (mounting
holes) and footprints whose symbol is gone are listed. Refuses while KiCad has the board open (`~NAME.kicad_pcb.lck`)
unless --force. Timestamped backup in `.backups/`, atomic write.

Why: the KiCad MCP `sync_schematic_to_board` renames nets (`Net-(U1-Pad58)`) and leaves pads without a net.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid as uuidlib
import xml.etree.ElementTree as ET

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
        out.append((sp, pb2))
    for (a, b), pb2 in reversed(out):
        block = block[:a] + pb2 + block[b:]
    return block, changes


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
    if dry or not edits:
        return
    new = text
    for e in sorted([e for e in edits if e[0] == 'rep'], key=lambda e: -e[1][0]):
        new = new[:e[1][0]] + e[2] + new[e[1][1]:]
    adds = ''.join('\n' + e[1] for e in edits if e[0] == 'add')
    if adds:
        i = new.index('\n\t(embedded_fonts') if '\n\t(embedded_fonts' in new else new.rindex('\n)')
        new = new[:i] + adds + new[i:]
    if len(children(new, (0, len(new)))) < len(top):
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
