#!/usr/bin/env python3
"""Set (or check) a project's board design-rule floors for a fab tier, so KiCad DRC enforces what the fab charges for.

    pcb-rules.py FILE.kicad_pro [--fab jlc-standard|jlc-advanced] [--clearance MM] [--check] [--force]

Sets board.design_settings.rules min_track_width, min_via_diameter, min_via_drill (the tier's floors, the same values
pcb-quality.py grades against) and, with --clearance, min_clearance and the Default net class clearance.
--check only reports differences (exit 1 if any): run it after a KiCad session, because KiCad writes its in-memory
rules back over the file when the project is saved or closed, and an auto-router (KiCad Routing Tools
`--fab-tier auto`) may lower these to pass its own DRC at a costlier tier.
Refuses while KiCad has the project open (`~NAME.kicad_pro.lck` / `~NAME.lck`) unless --force; backup in `.backups/`.
"""
import json
import os
import shutil
import sys
import time

FABS = {  # keep in step with pcb-quality.py FABS
    'jlc-standard': dict(min_track_width=0.127, min_via_diameter=0.45, min_via_drill=0.2),
    'jlc-advanced': dict(min_track_width=0.09, min_via_diameter=0.25, min_via_drill=0.15),
}


def main():
    a = sys.argv[1:]
    check, force = '--check' in a, '--force' in a
    a = [x for x in a if x not in ('--check', '--force')]
    opt = {}
    for k in ('--fab', '--clearance'):
        if k in a:
            i = a.index(k)
            opt[k] = a[i + 1]
            del a[i:i + 2]
    if len(a) != 1 or not a[0].endswith('.kicad_pro'):
        sys.exit(__doc__)
    pro = a[0]
    fab = opt.get('--fab', 'jlc-standard')
    if fab not in FABS:
        sys.exit('unknown --fab %s (have: %s)' % (fab, ', '.join(FABS)))
    want = dict(FABS[fab])
    cl = float(opt['--clearance']) if '--clearance' in opt else None
    if cl is not None:
        want['min_clearance'] = cl
    d, base = os.path.split(os.path.abspath(pro))
    stem = base[:-len('.kicad_pro')]
    locks = [f for f in ('~%s.kicad_pro.lck' % stem, '~%s.lck' % stem) if os.path.exists(os.path.join(d, f))]
    j = json.load(open(pro))
    rules = j.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})
    diffs = ['%s: %s -> %s' % (k, rules.get(k), v) for k, v in want.items() if rules.get(k) != v]
    default = [c for c in j.get('net_settings', {}).get('classes', []) if c.get('name') == 'Default']
    if cl is not None and default and default[0].get('clearance') != cl:
        diffs.append('Default net class clearance: %s -> %s' % (default[0].get('clearance'), cl))
    if not diffs:
        print('rules already at %s%s' % (fab, ' (clearance %s)' % cl if cl is not None else ''))
        return
    print('\n'.join(diffs))
    if check:
        sys.exit(1)
    if locks and not force:
        sys.exit('error: KiCad has the project open (%s); it would write its own rules back. Close it, or --force.' % locks[0])
    rules.update(want)
    if cl is not None and default:
        default[0]['clearance'] = cl
    bdir = os.path.join(d, '.backups')
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(pro, os.path.join(bdir, '%s.%s' % (base, time.strftime('%Y%m%d-%H%M%S'))))
    tmp = os.path.join(d, '.%s.tmp' % base)
    json.dump(j, open(tmp, 'w'), indent=2)
    os.replace(tmp, pro)
    print('wrote %s' % pro)


if __name__ == '__main__':
    main()
