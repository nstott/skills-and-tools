#!/usr/bin/env python3
"""Tidy routed copper on a .kicad_pcb, every change checked by KiCad DRC (zones refilled).

    pcb-tidy.py FILE.kicad_pcb prune
    pcb-tidy.py FILE.kicad_pcb corners
    pcb-tidy.py FILE.kicad_pcb widen NET=WIDTH [NET=WIDTH ...]
    pcb-tidy.py FILE.kicad_pcb meander NET --add MM [--partner NET] [--pitch 0.3]
    pcb-tidy.py FILE.kicad_pcb vias
    options: --dry-run (report, write nothing)  --force (edit while KiCad has the board open)

  prune    delete what DRC calls dangling (track_dangling / via_dangling, matched by net, layer and length), repeated
           until none is left, plus duplicate and zero-length segments. The usual leftovers of auto-routing and rip-up.
  corners  straighten acute (<90 deg) corners: the two segments meeting at the corner become one A-B segment, kept
           only where DRC stays clean. Corners of a net inside its own pour are left alone (as pcb-quality does).
  widen    raise tracks of each NET to WIDTH (power nets, LX nodes, sub-floor tracks); a segment DRC objects to is
           retried at half the step (0.15 -> 0.4 tries 0.275, 0.21, ...), so it ends as wide as its neighbours allow.
  meander  add MM of length to NET (the shorter member of a pair) with square bumps on its longest straight segments,
           on the side away from the partner (D+ <-> D-, _P <-> _N inferred; --partner to name it).
           Measure the pair with pcb-quality.py first; it reports the signal-path skew.
  vias     move every via that sits inside an SMD pad (pcb-quality `via-in-pad`: solder wicks down an open via) just
           off the pad: nearest clear spot first, tracks that ended on the via follow it, and a short track on the
           pad's layer joins pad and via. Kept only where DRC stays clean. With no clear spot, the via is deleted if
           nothing becomes unconnected (it was redundant), otherwise reported for a hand fix.

A change is kept only when DRC (copper errors: clearance, shorts, crossings, hole/edge clearance, mask bridges, plus
unconnected items) is no worse than before. DRC runs on a temporary copy next to the board so the project rules apply.
Refuses while KiCad has the board open (`~NAME.kicad_pcb.lck`) unless --force; timestamped backup in `.backups/`,
atomic write.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid as uuidlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch.sx import block_head, children  # noqa: E402

KICAD_CLI = os.environ.get('KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')
COPPER = ('clearance', 'shorting_items', 'tracks_crossing', 'hole_clearance', 'copper_edge_clearance',
          'solder_mask_bridge', 'hole_to_hole')


def items(t):
    out = []
    for sp in children(t, (0, len(t))):
        h = block_head(t, sp)
        if h not in ('segment', 'via'):
            continue
        s = t[sp[0]:sp[1]]
        d = dict(kind=h, span=sp, text=s, net=re.search(r'\(net (?:\d+ )?"([^"]*)"\)', s).group(1))
        if h == 'via':
            a = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', s)
            d.update(at=(float(a.group(1)), float(a.group(2))), size=float(re.search(r'\(size ([\d.]+)\)', s).group(1)))
        else:
            st = re.search(r'\(start ([-\d.]+) ([-\d.]+)\)', s)
            en = re.search(r'\(end ([-\d.]+) ([-\d.]+)\)', s)
            d.update(a=(float(st.group(1)), float(st.group(2))), b=(float(en.group(1)), float(en.group(2))),
                     width=float(re.search(r'\(width ([\d.]+)\)', s).group(1)),
                     layer=re.search(r'\(layer "([^"]+)"\)', s).group(1))
        out.append(d)
    return out


def seg_text(a, b, w, net, layer):
    return ('\t(segment\n\t\t(start %.6g %.6g)\n\t\t(end %.6g %.6g)\n\t\t(width %s)\n\t\t(layer "%s")\n'
            '\t\t(net "%s")\n\t\t(uuid "%s")\n\t)\n' % (a[0], a[1], b[0], b[1], w, layer, net, uuidlib.uuid4()))


def edit(t, remove=(), add=''):
    """Remove item spans and insert new items before the first segment/via (or the end of the board)."""
    for a, b in sorted(remove, key=lambda s: -s[0]):
        t = t[:a] + t[b:]
    t = re.sub(r'\n\t\n', '\n', t)
    if add:
        m = re.search(r'\n\t\((segment|via|arc|zone)\b', t)
        i = m.start() + 1 if m else t.rindex('\n)') + 1
        t = t[:i] + add + t[i:]
    return t


def pour_layers(t):
    out = set()
    for sp in children(t, (0, len(t))):
        if block_head(t, sp) == 'zone':
            z = t[sp[0]:sp[1]]
            n = re.search(r'\(net (?:\d+ )?"([^"]*)"\)', z)
            ly = re.search(r'\(layers? ([^)]*)\)', z)
            if n and ly and '(keepout' not in z:
                out |= {(n.group(1), l) for l in re.findall(r'"([^"]+)"', ly.group(1))}
    return out


class Drc:
    """KiCad DRC on a scratch copy beside the board (so the project's rules and net classes apply)."""

    def __init__(self, pcb):
        d, base = os.path.split(os.path.abspath(pcb))
        self.dir = tempfile.mkdtemp(prefix='.pcb-tidy-', dir=d)
        stem = base[:-len('.kicad_pcb')]
        pro = os.path.join(d, stem + '.kicad_pro')
        if os.path.exists(pro):
            shutil.copy(pro, os.path.join(self.dir, 'tidy.kicad_pro'))
        self.pcb = os.path.join(self.dir, 'tidy.kicad_pcb')

    def run(self, text):
        open(self.pcb, 'w').write(text)
        out = os.path.join(self.dir, 'drc.json')
        subprocess.run([KICAD_CLI, 'pcb', 'drc', '--severity-all', '--refill-zones', '--format', 'json', '-o', out, self.pcb],
                       capture_output=True)
        d = json.load(open(out))
        d['copper'] = [v for v in d['violations'] if v['type'] in COPPER]
        return d

    def score(self, d):
        return len(d['copper']), len(d['unconnected_items'])

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def no_worse(drc, d, base):
    return drc.score(d)[0] <= base[0] and drc.score(d)[1] <= base[1]


def do_prune(t, drc, base, log):
    its = items(t)
    seen, rm = set(), []
    for i in its:
        if i['kind'] != 'segment':
            continue
        k = (i['net'], i['layer'], i['width'], frozenset([i['a'], i['b']]))
        if i['a'] == i['b'] or k in seen:
            rm.append(i['span'])
        seen.add(k)
    if rm:
        t2 = edit(t, rm)
        if no_worse(drc, drc.run(t2), base):
            t = t2
            log.append('removed %d duplicate / zero-length segments' % len(rm))
    for rnd in range(12):
        d = drc.run(t)
        dang = [v for v in d['violations'] if v['type'] in ('track_dangling', 'via_dangling')]
        if not dang:
            break
        its = items(t)
        rm = []
        for v in dang:
            it = v['items'][0]
            p = (it['pos']['x'], it['pos']['y'])
            m = re.search(r'\[([^\]]*)\]', it['description'])
            net = m.group(1) if m else None
            ml = re.search(r'on (\S+), length ([\d.]+) mm', it['description'])
            best = None
            for i in its:
                if i['net'] != net or i['span'] in rm or (v['type'] == 'via_dangling') != (i['kind'] == 'via'):
                    continue
                if i['kind'] == 'via':
                    dd = math.dist(i['at'], p)
                else:
                    if ml and (i['layer'] != ml.group(1) or abs(math.dist(i['a'], i['b']) - float(ml.group(2))) > 0.002):
                        continue
                    dd = min(math.dist(i['a'], p), math.dist(i['b'], p), math.dist(((i['a'][0] + i['b'][0]) / 2, (i['a'][1] + i['b'][1]) / 2), p))
                if best is None or dd < best[0]:
                    best = (dd, i)
            if best and best[0] < 0.05 + (math.dist(best[1]['a'], best[1]['b']) / 2 if best[1]['kind'] == 'segment' else 0):
                rm.append(best[1]['span'])
                log.append('prune %s %s at (%.2f, %.2f)' % (v['type'], net, p[0], p[1]))
            else:
                log.append('could not match %s %s at (%.2f, %.2f)' % (v['type'], net, p[0], p[1]))
        if not rm:
            break
        t2 = edit(t, rm)
        if not no_worse(drc, drc.run(t2), base):
            log.append('a prune round made DRC worse; stopped')
            break
        t = t2
    return t


def acute_corners(t):
    pours = pour_layers(t)
    ends = {}
    for i in items(t):
        if i['kind'] != 'segment' or (i['net'], i['layer']) in pours:
            continue
        for p, o in ((i['a'], i['b']), (i['b'], i['a'])):
            ends.setdefault((i['net'], i['layer'], round(p[0], 4), round(p[1], 4)), []).append((i, o))
    vias = {(round(i['at'][0], 4), round(i['at'][1], 4)) for i in items(t) if i['kind'] == 'via'}
    out = []
    for (n, ly, x, y), vs in ends.items():
        if len(vs) != 2 or (x, y) in vias:
            continue
        (s1, A), (s2, B) = vs
        ax, ay, bx, by = A[0] - x, A[1] - y, B[0] - x, B[1] - y
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la > 1e-6 and lb > 1e-6:
            ang = math.degrees(math.acos(max(-1, min(1, (ax * bx + ay * by) / (la * lb)))))
            if ang < 89.5:
                out.append((n, ly, (x, y), ang, s1, s2, A, B))
    return out


def straighten(t, c):
    n, ly, P, ang, s1, s2, A, B = c
    w = max(s1['width'], s2['width'])
    return edit(t, [s1['span'], s2['span']], seg_text(A, B, w, n, ly) if math.dist(A, B) > 1e-4 else '')


def do_corners(t, drc, base, log):
    cs = acute_corners(t)
    log.append('%d acute corners' % len(cs))
    if not cs:
        return t
    for _ in range(len(cs)):
        cs = acute_corners(t)
        if not cs:
            break
        done = False
        for c in cs:
            t2 = straighten(t, c)
            if no_worse(drc, drc.run(t2), base):
                t = t2
                log.append('straightened %.0f deg corner on %s (%s) at (%.2f, %.2f)' % (c[3], c[0], c[1], *c[2]))
                done = True
                break
            log.append('kept %.0f deg corner on %s (%s) at (%.2f, %.2f): DRC objects' % (c[3], c[0], c[1], *c[2]))
        if not done:
            break
    return t


def do_widen(t, drc, base, want, log):
    """Widen to the target; a segment DRC refuses is retried at half the remaining step until the step is tiny."""
    uid = lambda s: re.search(r'\(uuid "([^"]+)"\)', s).group(1)
    target = {}
    for rnd in range(10):
        t2, changed = t, []
        for i in sorted(items(t), key=lambda i: -i['span'][0]):
            if i['kind'] != 'segment' or i['net'] not in want:
                continue
            w = target.get(uid(i['text']), want[i['net']])
            if i['width'] < w - 1e-6:
                new = re.sub(r'\(width [\d.]+\)', '(width %.4g)' % w, i['text'])
                t2 = t2[:i['span'][0]] + new + t2[i['span'][1]:]
                changed.append((i, w))
        if not changed:
            break
        d = drc.run(t2)
        if no_worse(drc, d, base):
            t = t2
            log.append('widened %d segments' % len(changed))
            continue
        pos = [(it['pos']['x'], it['pos']['y']) for v in d['copper'] for it in v['items']]
        def dseg(p, i):
            L2 = max(math.dist(i['a'], i['b']) ** 2, 1e-12)
            u = max(0, min(1, ((p[0] - i['a'][0]) * (i['b'][0] - i['a'][0]) + (p[1] - i['a'][1]) * (i['b'][1] - i['a'][1])) / L2))
            return math.dist(p, (i['a'][0] + u * (i['b'][0] - i['a'][0]), i['a'][1] + u * (i['b'][1] - i['a'][1])))
        refused = [(i, w) for i, w in changed if any(dseg(p, i) < 0.6 for p in pos)] or changed
        for i, w in refused:
            step = (w - i['width']) / 2
            target[uid(i['text'])] = i['width'] + step if step >= 0.02 else i['width']
        log.append('round %d: %d segments near a DRC error retry narrower' % (rnd, len(refused)))
    left = [i for i in items(t) if i['kind'] == 'segment' and i['net'] in want and i['width'] < want[i['net']] - 1e-6]
    for i in left:
        log.append('below target: %.3f mm %s (%s) (%.2f, %.2f)-(%.2f, %.2f)' % (i['width'], i['net'], i['layer'], *i['a'], *i['b']))
    return t


def partner_of(net):
    for a, b in (('+', '-'), ('_P', '_N'), ('DP', 'DM'), ('_p', '_n')):
        if net.endswith(a):
            return net[:-len(a)] + b
        if net.endswith(b):
            return net[:-len(b)] + a
    return None


def do_meander(t, drc, base, net, add, partner, pitch, log):
    its = items(t)
    mine = sorted([i for i in its if i['kind'] == 'segment' and i['net'] == net], key=lambda i: -math.dist(i['a'], i['b']))
    oth = [i for i in its if i['kind'] == 'segment' and i['net'] == partner]
    mid = lambda i: ((i['a'][0] + i['b'][0]) / 2, (i['a'][1] + i['b'][1]) / 2)
    for nb in (3, 4, 2, 5, 6):
        h = add / (2 * nb)
        if h < pitch / 2 or h > 1.0:
            continue
        for i in mine:
            a, b = i['a'], i['b']
            L = math.dist(a, b)
            if L < 2 * nb * pitch + 0.6:
                continue
            u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
            n = (-u[1], u[0])
            if oth:
                q = mid(min(oth, key=lambda o: math.dist(mid(i), mid(o))))
                if (q[0] - mid(i)[0]) * n[0] + (q[1] - mid(i)[1]) * n[1] > 0:
                    n = (-n[0], -n[1])
            s = (L - 2 * nb * pitch) / 2
            P = lambda s_, o: (a[0] + u[0] * s_ + n[0] * o, a[1] + u[1] * s_ + n[1] * o)
            pts = [a, P(s, 0)]
            for _ in range(nb):
                pts += [P(s, h), P(s + pitch, h), P(s + pitch, 0), P(s + 2 * pitch, 0)]
                s += 2 * pitch
            pts.append(b)
            new = ''.join(seg_text(pts[k], pts[k + 1], i['width'], net, i['layer'])
                          for k in range(len(pts) - 1) if math.dist(pts[k], pts[k + 1]) > 1e-6)
            t2 = edit(t, [i['span']], new)
            d = drc.run(t2)
            dang = [v for v in d['violations'] if v['type'] == 'track_dangling']
            if no_worse(drc, d, base) and not dang:
                log.append('meander: %d bumps of %.2f mm on %s (%s) (%.2f, %.2f)-(%.2f, %.2f), +%.2f mm'
                           % (nb, h, net, i['layer'], *a, *b, 2 * nb * h))
                return t2
    log.append('meander: no straight segment of %s takes +%.2f mm without a DRC error' % (net, add))
    return t


def rot(lx, ly, deg):
    th = math.radians(deg)
    return lx * math.cos(th) + ly * math.sin(th), -lx * math.sin(th) + ly * math.cos(th)


def smd_pads(t):
    """SMD pads with absolute centre, board-axis width/height (same convention as pcb-quality), copper layer, net."""
    out = []
    for sp in children(t, (0, len(t))):
        if block_head(t, sp) != 'footprint':
            continue
        b = t[sp[0]:sp[1]]
        ref = re.search(r'\(property "Reference" "([^"]*)"', b).group(1)
        at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', b)
        x0, y0, r0 = float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)
        for p in children(b, (0, len(b))):
            if block_head(b, p) != 'pad':
                continue
            pb = b[p[0]:p[1]]
            pm = re.match(r'\(pad "([^"]*)" (\w+)', pb)
            if pm.group(2) != 'smd':
                continue
            pa = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', pb)
            sz = re.search(r'\(size ([-\d.]+) ([-\d.]+)\)', pb)
            net = re.search(r'\(net (?:\d+ )?"([^"]*)"\)', pb)
            cu = [l for l in re.findall(r'"([^"]+)"', re.search(r'\(layers ([^)]*)\)', pb).group(1)) if l.endswith('.Cu')]
            dx, dy = rot(float(pa.group(1)), float(pa.group(2)), r0)
            w, h = float(sz.group(1)), float(sz.group(2))
            if int(round((float(pa.group(3) or 0) - r0) / 90)) % 2:
                w, h = h, w
            out.append(dict(ref=ref, num=pm.group(1), at=(x0 + dx, y0 + dy), w=w, h=h,
                            layer=cu[0] if cu else 'F.Cu', net=net.group(1) if net else ''))
    return out


def in_pad(pt, p, margin=0.0):
    return abs(pt[0] - p['at'][0]) < p['w'] / 2 + margin and abs(pt[1] - p['at'][1]) < p['h'] / 2 + margin


def do_vias(t, drc, base, log, clearance=0.1):
    def offenders(t):
        ps = smd_pads(t)
        return [(v, p) for v in items(t) if v['kind'] == 'via' for p in ps if in_pad(v['at'], p)]
    bad = offenders(t)
    log.append('%d via(s) inside SMD pads' % len(bad))
    tried = set()
    while True:
        bad = [(v, p) for v, p in offenders(t) if (round(v['at'][0], 3), round(v['at'][1], 3)) not in tried]
        if not bad:
            break
        v, p = bad[0]
        tried.add((round(v['at'][0], 3), round(v['at'][1], 3)))
        if v['net'] != p['net']:
            log.append('via %s at (%.2f, %.2f) sits in %s pad %s of another net (%s): fix by hand'
                       % (v['net'], *v['at'], p['ref'], p['num'], p['net']))
            continue
        r = v['size'] / 2
        cands = []
        for k in (0.0, 0.15, 0.35, 0.6, 0.9):
            ox, oy = p['w'] / 2 + r + clearance + k, p['h'] / 2 + r + clearance + k
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                cands.append((p['at'][0] + dx * ox, p['at'][1] + dy * oy))
        cands.sort(key=lambda c: math.dist(c, v['at']))
        old = v['at']
        attached = [i for i in items(t) if i['kind'] == 'segment' and i['net'] == v['net']
                    and (math.dist(i['a'], old) <= r or math.dist(i['b'], old) <= r)]
        w = min(0.25, p['w'], p['h'])
        done = False
        for c in cands:
            new_via = re.sub(r'\(at [-\d.]+ [-\d.]+\)', '(at %.4f %.4f)' % c, v['text'], count=1)
            segs = ''
            for i in attached:
                a = c if math.dist(i['a'], old) <= r else i['a']
                b = c if math.dist(i['b'], old) <= r else i['b']
                if math.dist(a, b) > 1e-4:
                    segs += seg_text(a, b, i['width'], i['net'], i['layer'])
            segs += seg_text(p['at'], c, w, v['net'], p['layer'])
            t2 = edit(t, [v['span']] + [i['span'] for i in attached], '\t' + new_via + '\n' + segs)
            if no_worse(drc, drc.run(t2), base):
                t = t2
                log.append('moved %s via from %s pad %s (%.2f, %.2f) to (%.2f, %.2f)' % (v['net'], p['ref'], p['num'], *old, *c))
                done = True
                break
        if not done:
            # a redundant via (another via or the track itself already carries the net) can simply go
            t2 = edit(t, [v['span']])
            if no_worse(drc, drc.run(t2), base):
                t = do_prune(t2, drc, base, [])
                log.append('removed redundant %s via in %s pad %s at (%.2f, %.2f): nothing became unconnected'
                           % (v['net'], p['ref'], p['num'], *old))
            else:
                log.append('%s via in %s pad %s at (%.2f, %.2f): no clear spot nearby; move it by hand'
                           % (v['net'], p['ref'], p['num'], *old))
    return t


def main():
    a = sys.argv[1:]
    dry, force = '--dry-run' in a, '--force' in a
    a = [x for x in a if x not in ('--dry-run', '--force')]
    opt = {}
    for k in ('--add', '--partner', '--pitch'):
        if k in a:
            i = a.index(k)
            opt[k] = a[i + 1]
            del a[i:i + 2]
    if len(a) < 2 or a[1] not in ('prune', 'corners', 'widen', 'meander', 'vias'):
        sys.exit(__doc__)
    pcb, cmd, rest = a[0], a[1], a[2:]
    d, base_name = os.path.split(os.path.abspath(pcb))
    if os.path.exists(os.path.join(d, '~' + base_name + '.lck')) and not force and not dry:
        sys.exit('error: KiCad has %s open. Close the PCB editor first; --force to edit anyway.' % base_name)
    text = open(pcb).read()
    drc = Drc(pcb)
    log = []
    try:
        d0 = drc.run(text)
        base = drc.score(d0)
        log.append('DRC before: %d copper errors, %d unconnected' % base)
        if cmd == 'prune':
            new = do_prune(text, drc, base, log)
        elif cmd == 'corners':
            new = do_corners(text, drc, base, log)
        elif cmd == 'vias':
            new = do_vias(text, drc, base, log)
        elif cmd == 'widen':
            want = {}
            for r in rest:
                n, _, w = r.rpartition('=')
                want[n] = float(w)
            if not want:
                sys.exit('usage: pcb-tidy.py FILE widen NET=WIDTH [...]')
            new = do_widen(text, drc, base, want, log)
        else:
            if not rest or '--add' not in opt:
                sys.exit('usage: pcb-tidy.py FILE meander NET --add MM [--partner NET]')
            partner = opt.get('--partner') or partner_of(rest[0])
            new = do_meander(text, drc, base, rest[0], float(opt['--add']), partner, float(opt.get('--pitch', 0.3)), log)
        d1 = drc.run(new)
        log.append('DRC after: %d copper errors, %d unconnected' % drc.score(d1))
    finally:
        drc.close()
    print('\n'.join(log))
    if dry or new == text:
        print('nothing written' if new == text else '(dry run: nothing written)')
        return
    bdir = os.path.join(d, '.backups')
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(pcb, os.path.join(bdir, '%s.%s' % (base_name, time.strftime('%Y%m%d-%H%M%S'))))
    tmpf = os.path.join(d, '.%s.tmp' % base_name)
    open(tmpf, 'w').write(new)
    os.replace(tmpf, pcb)
    print('wrote %s' % pcb)


if __name__ == '__main__':
    main()
