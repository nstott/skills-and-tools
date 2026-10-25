#!/usr/bin/env python3
"""Board edits that need KiCad's own pcbnew engine (runs itself under KiCad's bundled Python).

    pcb-kicad.py FILE.kicad_pcb fp-update [REF ...]       rebuild footprints from their library
    pcb-kicad.py FILE.kicad_pcb refs hide REF [...]       hide reference texts (mounting holes, logos)
    pcb-kicad.py FILE.kicad_pcb refs move REF=X,Y [...]   put reference texts at board mm positions
    pcb-kicad.py FILE.kicad_pcb refs auto [REF ...]       find a clear spot for reference texts DRC flags
    options: --dry-run  --force (edit while KiCad has the board open)

  fp-update  "Tools > Update Footprints from Library" for the named parts, or with no REF for every part DRC reports
             as `lib_footprint_mismatch`. The library copy is loaded through the project and global fp-lib-table,
             then given the old part's position, rotation, side, reference, value, fields, schematic link, lock and
             pad nets. The rotation is chosen so every pad lands where it was (back-side parts included), so tracks
             stay connected; a part whose pads cannot be matched is skipped.
  refs auto  for each REF (default: every reference text in a `silk_*` DRC violation) tries positions around its
             footprint, nearest first, and keeps the first one where that text has no silk violation and the total
             count drops. Hide what has no room (`refs hide`), or leave it on F.Fab.

Every result is checked with KiCad DRC (zones refilled): written only if copper errors and unconnected items did not
increase. Timestamped backup in `.backups/`, atomic write. pcbnew re-saves the whole file in KiCad's own format.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

KICAD_APP = os.environ.get('KICAD_APP', '/Applications/KiCad/KiCad.app')
KICAD_CLI = os.environ.get('KICAD_CLI', KICAD_APP + '/Contents/MacOS/kicad-cli')
KICAD_PYTHON = os.environ.get('KICAD_PYTHON', KICAD_APP + '/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3')
try:
    import pcbnew
except ImportError:
    if os.environ.get('_PCB_KICAD_REEXEC'):
        sys.exit('error: pcbnew not importable from %s' % KICAD_PYTHON)
    os.environ['_PCB_KICAD_REEXEC'] = '1'
    os.execv(KICAD_PYTHON, [KICAD_PYTHON] + sys.argv)

COPPER = ('clearance', 'shorting_items', 'tracks_crossing', 'hole_clearance', 'copper_edge_clearance',
          'solder_mask_bridge', 'hole_to_hole')


def drc(board_file, pro):
    """KiCad DRC on a scratch copy in the board's own folder (so ${KIPRJMOD} libraries and project rules resolve)."""
    d = os.path.dirname(os.path.abspath(board_file))
    stem = os.path.join(d, '.pcb-kicad-%d' % os.getpid())
    try:
        shutil.copy(board_file, stem + '.kicad_pcb')
        if os.path.exists(pro):
            shutil.copy(pro, stem + '.kicad_pro')
        subprocess.run([KICAD_CLI, 'pcb', 'drc', '--severity-all', '--refill-zones', '--format', 'json', '-o',
                        stem + '.json', stem + '.kicad_pcb'], capture_output=True, cwd=d)
        r = json.load(open(stem + '.json'))
    finally:
        for ext in ('.kicad_pcb', '.kicad_pro', '.json', '.kicad_prl'):
            if os.path.exists(stem + ext):
                os.remove(stem + ext)
    r['score'] = (len([v for v in r['violations'] if v['type'] in COPPER]), len(r['unconnected_items']))
    return r


def lib_dirs(project_dir):
    """Footprint library nickname -> .pretty directory, from the project and global fp-lib-table (nested tables too)."""
    env = dict(os.environ)
    env.setdefault('KIPRJMOD', project_dir)
    fp_dir = KICAD_APP + '/Contents/SharedSupport/footprints'
    for v in ('KICAD10_FOOTPRINT_DIR', 'KICAD9_FOOTPRINT_DIR', 'KICAD8_FOOTPRINT_DIR', 'KICAD_FOOTPRINT_DIR'):
        env.setdefault(v, fp_dir)
    out = {}

    def read(path, depth=0):
        if depth > 3 or not os.path.exists(path):
            return
        for m in re.finditer(r'\(lib \(name "([^"]+)"\)\s*\(type "([^"]+)"\)\s*\(uri "([^"]+)"\)', open(path).read()):
            uri = re.sub(r'\$\{(\w+)\}', lambda g: env.get(g.group(1), g.group(0)), m.group(3))
            if m.group(2) == 'Table':
                read(uri, depth + 1)
            else:
                out.setdefault(m.group(1), uri)
    read(os.path.join(project_dir, 'fp-lib-table'))
    for ver in ('10.0', '9.0', '8.0'):
        read(os.path.expanduser('~/Library/Preferences/kicad/%s/fp-lib-table' % ver))
        read(os.path.expanduser('~/.config/kicad/%s/fp-lib-table' % ver))
    return out


def fp_update(b, refs, project_dir, log):
    libs = lib_dirs(project_dir)
    for fp in list(b.GetFootprints()):
        ref = fp.GetReference()
        if ref not in refs:
            continue
        fpid = fp.GetFPID()
        lib, name = str(fpid.GetLibNickname()), str(fpid.GetLibItemName())
        if not lib or lib not in libs:
            log.append('%s: library %r not in any fp-lib-table; skipped' % (ref, lib))
            continue
        new = pcbnew.FootprintLoad(libs[lib], name)
        if new is None:
            log.append('%s: %s not found in %s; skipped' % (ref, name, libs[lib]))
            continue
        new.SetParent(b)
        new.SetFPID(fpid)
        new.SetPath(fp.GetPath())
        new.SetReference(ref)
        new.SetValue(fp.GetValue())
        for f in fp.GetFields():
            n = f.GetName()
            if n in ('Reference', 'Value', 'Footprint'):
                continue
            if new.HasField(n):
                new.GetField(n).SetText(f.GetText())
            else:
                g = pcbnew.PCB_FIELD(new, new.GetNextFieldOrdinal(), n)
                g.SetText(f.GetText())
                g.SetVisible(False)
                g.SetLayer(f.GetLayer())
                g.SetPosition(fp.GetPosition())
                new.Add(g)
        new.SetPosition(fp.GetPosition())
        if fp.IsFlipped():
            new.Flip(new.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        old = {}
        for p in fp.Pads():
            old.setdefault(p.GetNumber(), []).append(p.GetPosition())

        def err():
            e = 0
            for p in new.Pads():
                if p.GetNumber() in old:
                    q = p.GetPosition()
                    e += min(abs(q.x - o.x) + abs(q.y - o.y) for o in old[p.GetNumber()])
            return e
        best = None
        for deg in (0, 90, 180, 270):
            new.SetOrientationDegrees((fp.GetOrientationDegrees() + deg) % 360)
            e = err()
            if best is None or e < best[0]:
                best = (e, new.GetOrientationDegrees())
        new.SetOrientationDegrees(best[1])
        if best[0] > pcbnew.FromMM(0.01) * max(1, len(old)):
            log.append('%s: library pads do not line up with the board copy (off by %.3f mm in total); skipped'
                       % (ref, pcbnew.ToMM(best[0])))
            continue
        nets = {p.GetNumber(): p.GetNet() for p in fp.Pads()}
        for p in new.Pads():
            if p.GetNumber() in nets:
                p.SetNet(nets[p.GetNumber()])
        new.SetLocked(fp.IsLocked())
        b.Remove(fp)
        b.Add(new)
        log.append('updated %s from %s:%s%s' % (ref, lib, name, ' (back side)' if fp.IsFlipped() else ''))


def silk_refs(r):
    out = []
    for v in r['violations']:
        if v['type'].startswith('silk'):
            for i in v['items']:
                m = re.match(r'Reference field of (\S+)$', i['description'])
                if m and m.group(1) not in out:
                    out.append(m.group(1))
    return out


def silk_count(r, ref=None):
    vs = [v for v in r['violations'] if v['type'].startswith('silk')]
    if ref:
        vs = [v for v in vs if any(i['description'] == 'Reference field of %s' % ref for i in v['items'])]
    return len(vs)


def sidecars(board_file):
    """pcbnew.SaveBoard writes NAME.kicad_pro/.kicad_prl beside the board it saves; remove them for scratch copies."""
    stem = board_file[:-len('.kicad_pcb')]
    for ext in ('.kicad_pro', '.kicad_prl'):
        if os.path.exists(stem + ext):
            os.remove(stem + ext)


def summary(r):
    mism = len([v for v in r['violations'] if v['type'] == 'lib_footprint_mismatch'])
    return '%d copper errors, %d unconnected, %d silk, %d library mismatches' % (r['score'][0], r['score'][1], silk_count(r), mism)


def refs_auto(path, b, refs, pro, base, log):
    tmp = path + '.refs.kicad_pcb'
    try:
        pcbnew.SaveBoard(tmp, b)
        r = drc(tmp, pro)
        sidecars(tmp)
        refs = refs or silk_refs(r)
        total = silk_count(r)
        fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
        eb = b.GetBoardEdgesBoundingBox()
        ex0, ey0 = pcbnew.ToMM(eb.GetX()) + 0.4, pcbnew.ToMM(eb.GetY()) + 0.4
        ex1, ey1 = ex0 + pcbnew.ToMM(eb.GetWidth()) - 0.8, ey0 + pcbnew.ToMM(eb.GetHeight()) - 0.8
        for ref in refs:
            fp = fps.get(ref)
            if fp is None:
                continue
            if silk_count(r, ref) == 0:
                log.append('%s reference already clear' % ref)
                continue
            bb = fp.GetBoundingBox(False)
            cx, cy = pcbnew.ToMM(bb.GetCenter().x), pcbnew.ToMM(bb.GetCenter().y)
            hw, hh = pcbnew.ToMM(bb.GetWidth()) / 2, pcbnew.ToMM(bb.GetHeight()) / 2
            t = fp.Reference()
            th = pcbnew.ToMM(t.GetTextHeight())
            tw = th * 0.8 * max(1, len(t.GetShownText(False)))
            old = t.GetPosition()
            cands = []
            for k in (0.2, 0.6, 1.2, 2.0):
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)):
                    cands.append((cx + dx * (hw + tw / 2 + k), cy + dy * (hh + th / 2 + k)))
            # the whole text stays inside the board outline (silk off the board is not a DRC error, just lost)
            cands = [c for c in cands if ex0 + tw / 2 <= c[0] <= ex1 - tw / 2 and ey0 + th / 2 <= c[1] <= ey1 - th / 2]
            cands.sort(key=lambda c: abs(c[0] - cx) + abs(c[1] - cy))
            for x, y in cands:
                t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
                pcbnew.SaveBoard(tmp, b)
                r2 = drc(tmp, pro)
                sidecars(tmp)
                if silk_count(r2, ref) == 0 and silk_count(r2) < total and r2['score'] <= base:
                    r, total = r2, silk_count(r2)
                    log.append('moved %s reference to (%.2f, %.2f)' % (ref, x, y))
                    break
            else:
                t.SetPosition(old)
                log.append('%s: no clear spot found nearby; hide it (refs hide %s) or move it by hand' % (ref, ref))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        sidecars(tmp)


def main():
    a = sys.argv[1:]
    dry, force = '--dry-run' in a, '--force' in a
    a = [x for x in a if x not in ('--dry-run', '--force')]
    if len(a) < 2 or a[1] not in ('fp-update', 'refs') or (a[1] == 'refs' and (len(a) < 3 or a[2] not in ('hide', 'move', 'auto'))):
        sys.exit(__doc__)
    path = os.path.abspath(a[0])
    d, base_name = os.path.split(path)
    if os.path.exists(os.path.join(d, '~' + base_name + '.lck')) and not force and not dry:
        sys.exit('error: KiCad has %s open. Close the PCB editor first; --force to edit anyway.' % base_name)
    pro = path[:-len('.kicad_pcb')] + '.kicad_pro'
    r0 = drc(path, pro)
    base = r0['score']
    b = pcbnew.LoadBoard(path)
    log = ['DRC before: ' + summary(r0)]
    if a[1] == 'fp-update':
        refs = a[2:]
        if not refs:
            refs = [re.match(r'Footprint (\S+)', i['description']).group(1) for v in r0['violations']
                    if v['type'] == 'lib_footprint_mismatch' for i in v['items'] if i['description'].startswith('Footprint ')]
        fp_update(b, set(refs), d, log)
    elif a[2] == 'hide':
        fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
        for ref in a[3:]:
            if ref in fps:
                fps[ref].Reference().SetVisible(False)
                log.append('hid %s reference' % ref)
    elif a[2] == 'move':
        fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
        for spec in a[3:]:
            ref, _, xy = spec.partition('=')
            x, y = (float(v) for v in xy.split(','))
            if ref in fps:
                fps[ref].Reference().SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
                log.append('moved %s reference to (%g, %g)' % (ref, x, y))
    else:
        refs_auto(path, b, a[3:], pro, base, log)
    tmpf = os.path.join(d, '.%s.tmp.kicad_pcb' % base_name[:-len('.kicad_pcb')])
    pcbnew.SaveBoard(tmpf, b)
    # SaveBoard also writes a .kicad_pro/.kicad_prl beside the copy (KiCad's default rules when no project was
    # loaded); drop them so no stray project file is left next to the board
    for ext in ('.kicad_pro', '.kicad_prl'):
        side = tmpf[:-len('.kicad_pcb')] + ext
        if os.path.exists(side):
            os.remove(side)
    r1 = drc(tmpf, pro)
    log.append('DRC after: ' + summary(r1))
    print('\n'.join(log))
    if r1['score'][0] > base[0] or r1['score'][1] > base[1]:
        os.remove(tmpf)
        sys.exit('error: DRC got worse; nothing written')
    if dry:
        os.remove(tmpf)
        print('(dry run: nothing written)')
        return
    bdir = os.path.join(d, '.backups')
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(path, os.path.join(bdir, '%s.%s' % (base_name, time.strftime('%Y%m%d-%H%M%S'))))
    os.replace(tmpf, path)
    print('wrote %s' % path)


if __name__ == '__main__':
    main()
