#!/usr/bin/env python3
"""Find a part, value, LCSC/MPN number, library symbol or net label across sibling KiCad projects.

    proj-find.py TEXT [--root DIR] [--labels | --parts]
Case-insensitive substring match against, for every placed symbol, its reference, lib_id and all property values
(Value, Footprint, LCSC, MPN, ...), and against label / global label / hierarchical label names.
--root DIR     folder holding the projects (default: the parent of the nearest project folder above the
               current directory, else the current directory)
--parts/--labels  only search parts / only search labels

Why: related boards live side by side (e.g. ~/hackery/nstott/kicad/*). When a circuit is not in the project you
are in, this answers "which project has it" without opening every schematic. Skips .history, .backups and
.mcp-backups copies.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch.sx import block_head, children  # noqa: E402

SKIP = {'.history', '.backups', '.mcp-backups', '.git', 'node_modules'}
PROP = re.compile(r'\(property "([^"]+)" "((?:[^"\\]|\\.)*)"')


def default_root():
    d = os.getcwd()
    while True:
        if any(f.endswith('.kicad_pro') for f in os.listdir(d)):
            return os.path.dirname(d)
        if os.path.dirname(d) == d:
            return os.getcwd()
        d = os.path.dirname(d)


def schematics(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x not in SKIP]
        for f in sorted(fn):
            if f.endswith('.kicad_sch'):
                yield os.path.join(dp, f)


def search(path, needle, parts=True, labels=True):
    text = open(path, errors='replace').read()
    hits = []
    for sp in children(text, (0, len(text))):
        h = block_head(text, sp)
        body = text[sp[0]:sp[1]]
        if parts and h == 'symbol' and '(lib_id' in body.split('(property')[0]:
            lib = re.search(r'\(lib_id "([^"]+)"', body).group(1)
            props = dict(PROP.findall(body))
            if lib.startswith('power:') and not needle.lower() in lib.lower():
                continue
            if needle in lib.lower() or any(needle in v.lower() for v in props.values()):
                keep = ('Value', 'Footprint', 'LCSC', 'MPN')
                hits.append('%-6s %-28s %s' % (props.get('Reference', '?'), lib,
                                               ' '.join('%s=%s' % (k, props[k]) for k in keep if props.get(k))))
        elif labels and h in ('label', 'global_label', 'hierarchical_label'):
            name = re.match(r'\(%s "((?:[^"\\]|\\.)*)"' % h, body).group(1)
            if needle in name.lower():
                hits.append('%-6s %s' % ('label' if h == 'label' else h[:-6], name))
    # a label is usually used many times; show each name once with its count
    seen, out = {}, []
    for x in hits:
        seen[x] = seen.get(x, 0) + 1
    for x, n in seen.items():
        out.append(x + ('  (x%d)' % n if n > 1 else ''))
    return out


def main():
    a = sys.argv[1:]
    root = None
    if '--root' in a:
        i = a.index('--root')
        root = a[i + 1]
        del a[i:i + 2]
    parts, labels = '--labels' not in a, '--parts' not in a
    a = [x for x in a if x not in ('--parts', '--labels')]
    if len(a) != 1:
        sys.exit(__doc__)
    root = os.path.abspath(root or default_root())
    needle = a[0].lower()
    found = False
    for path in schematics(root):
        res = search(path, needle, parts, labels)
        if res:
            found = True
            print(os.path.relpath(path, root))
            for r in res:
                print('  ' + r)
    if not found:
        print('no match for %r under %s' % (a[0], root))
        sys.exit(1)


if __name__ == '__main__':
    main()
