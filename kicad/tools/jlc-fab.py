#!/usr/bin/env python3
"""JLCPCB assembly files: BOM and CPL from a KiCad project, checked against each other.

    jlc-fab.py FILE.kicad_pcb OUTDIR [--sch FILE.kicad_sch]   write OUTDIR/bom-jlc.csv and OUTDIR/cpl-jlc.csv, then check
    jlc-fab.py --check BOM.csv CPL.csv                          check existing files only
Exit 0 when the pair is clean, 1 on any problem.

BOM: `kicad-cli sch export bom` (DNP excluded, run from the schematic's folder so project libraries resolve), one row
per Value + Footprint + LCSC, every designator spelled out. JLC does not expand ranges: "C1-C4" is read as one
designator that matches nothing, and those parts are silently dropped from assembly. Parts with no LCSC number
(test points, mounting holes, anything you place yourself) are left out and listed.
CPL: `kicad-cli pcb export pos` (both sides, DNP excluded), JLC columns, mm, KiCad's rotation (check it in JLC's
preview). Both files CRLF, as JLC's own templates.

Check: no designator ranges or duplicates, every BOM row has an LCSC number, and the BOM and CPL list the same
designators (a part in one and not the other is not assembled).
"""
import csv
import os
import re
import subprocess
import sys
import tempfile

KICAD_CLI = os.environ.get('KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')


def ref_key(r):
    m = re.match(r'([A-Za-z_#]*)(\d*)(.*)', r)
    return (m.group(1), int(m.group(2) or 0), m.group(3))


def kcli(*args, cwd=None):
    p = subprocess.run([KICAD_CLI, *args], capture_output=True, text=True, cwd=cwd)
    if p.returncode:
        sys.exit('kicad-cli %s failed:\n%s' % (' '.join(args[:3]), p.stderr))


def bom_rows(sch):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'bom.csv')
        kcli('sch', 'export', 'bom', '--fields', 'Reference,Value,Footprint,LCSC', '--group-by', '', '--exclude-dnp',
             '-o', out, os.path.basename(sch), cwd=os.path.dirname(os.path.abspath(sch)))
        rows = list(csv.reader(open(out, newline='')))[1:]
    groups, skipped = {}, []
    for ref, val, fp, lcsc in rows:
        if ref.startswith('#'):
            continue  # power symbols, PWR_FLAG
        if not lcsc.strip():
            skipped.append(ref)
            continue
        groups.setdefault((val, fp, lcsc.strip()), []).append(ref)
    out = [(val, sorted(refs, key=ref_key), fp, lcsc) for (val, fp, lcsc), refs in groups.items()]
    return sorted(out, key=lambda r: ref_key(r[1][0])), sorted(skipped, key=ref_key)


def cpl_rows(pcb):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'pos.csv')
        kcli('pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'both', '--exclude-dnp', '-o', out, pcb)
        rows = list(csv.DictReader(open(out, newline='')))
    return [(r['Ref'], r['PosX'], r['PosY'], r['Side'].capitalize(), r['Rot']) for r in rows]


def write(outdir, bom, cpl, in_bom):
    os.makedirs(outdir, exist_ok=True)
    bp, cp = os.path.join(outdir, 'bom-jlc.csv'), os.path.join(outdir, 'cpl-jlc.csv')
    with open(bp, 'w', newline='') as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
        w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC'])
        for val, refs, fp, lcsc in bom:
            w.writerow([val, ','.join(refs), fp, lcsc])
    with open(cp, 'w', newline='') as f:
        f.write('Designator,Mid X,Mid Y,Layer,Rotation\r\n')
        for ref, x, y, side, rot in cpl:
            if ref in in_bom:  # placements JLC has no part for only raise its "missing data" dialog
                f.write('%s,%smm,%smm,%s,%s\r\n' % (ref, x, y, side, rot))
    return bp, cp


def check(bp, cp):
    problems = []
    bom = list(csv.reader(open(bp, newline='', encoding='utf-8-sig')))
    cpl = list(csv.reader(open(cp, newline='', encoding='utf-8-sig')))
    bh, ch = [h.strip().lower() for h in bom[0]], [h.strip().lower() for h in cpl[0]]
    try:
        di, li, cd = bh.index('designator'), bh.index('lcsc'), ch.index('designator')
    except ValueError as e:
        sys.exit('header not as JLC expects (%s): %s / %s' % (e, bom[0], cpl[0]))
    b = []
    for r in bom[1:]:
        refs = [d.strip() for d in r[di].split(',') if d.strip()]
        b += refs
        if not r[li].strip():
            problems.append('BOM row %s has no LCSC number' % r[di])
        problems += ['BOM designator "%s" is a range: JLC reads it as one name and assembles none of them' % d
                     for d in refs if re.search(r'\d\s*-\s*[A-Za-z]*\d', d)]
    c = [r[cd].strip() for r in cpl[1:] if r]
    for name, lst in (('BOM', b), ('CPL', c)):
        dup = sorted({d for d in lst if lst.count(d) > 1}, key=ref_key)
        if dup:
            problems.append('%s lists %s more than once' % (name, ', '.join(dup)))
    only_b, only_c = sorted(set(b) - set(c), key=ref_key), sorted(set(c) - set(b), key=ref_key)
    if only_b:
        problems.append('in the BOM, not the CPL (no placement, not assembled): ' + ', '.join(only_b))
    if only_c:
        problems.append('in the CPL, not the BOM (no part, not assembled): ' + ', '.join(only_c))
    print('%s: %d rows, %d designators; %s: %d placements' % (bp, len(bom) - 1, len(b), cp, len(c)))
    for p in problems:
        print('PROBLEM  ' + p)
    print('ok: BOM and CPL agree' if not problems else '%d problem(s)' % len(problems))
    return not problems


def main():
    a = sys.argv[1:]
    if a[:1] == ['--check'] and len(a) == 3:
        sys.exit(0 if check(a[1], a[2]) else 1)
    if len(a) not in (2, 4) or not a[0].endswith('.kicad_pcb') or (len(a) == 4 and a[2] != '--sch'):
        sys.exit(__doc__)
    pcb, outdir = a[0], a[1]
    sch = a[3] if len(a) == 4 else pcb[:-len('.kicad_pcb')] + '.kicad_sch'
    if not os.path.exists(sch):
        sys.exit('schematic not found: %s (pass --sch)' % sch)
    bom, skipped = bom_rows(sch)
    in_bom = {r for row in bom for r in row[1]}
    bp, cp = write(outdir, bom, cpl_rows(pcb), in_bom)
    if skipped:
        print('no LCSC number, not in the BOM (not assembled): ' + ', '.join(skipped))
    sys.exit(0 if check(bp, cp) else 1)


if __name__ == '__main__':
    main()
