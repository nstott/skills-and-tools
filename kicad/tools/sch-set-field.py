#!/usr/bin/env python3
"""Set symbol fields in a .kicad_sch by editing the file text: backup, atomic write, connectivity check.

    sch-set-field.py FILE REF FIELD VALUE [REF FIELD VALUE ...]
    sch-set-field.py FILE --dry-run R3 Value 100k
Options: --dry-run (show the changes, write nothing), --force (edit even if KiCad has the file open).

Why: the KiCad MCP rewrites the whole schematic on every call (and a 30 s timeout truncates it to 0 bytes);
changing a value or LCSC number does not need that. Refuses when KiCad holds the lock file
(`~NAME.kicad_sch.lck`), because KiCad would overwrite the edit on its next save. A missing field is added as a
hidden property. Backup goes to `<project>/.backups/`; net connectivity is compared before and after.
"""
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch import netlist  # noqa: E402
from kicad_sch.sx import block_head, children, tokenize_blocks  # noqa: E402

NEW_PROP = '''(property "%s" "%s"
\t\t\t(at %s %s 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)'''


def esc(v):
    return v.replace('\\', '\\\\').replace('"', '\\"')


def find_symbol(text, ref):
    """Span of the placed (top-level) symbol whose Reference property is `ref`."""
    root = (0, len(text))
    for sp in children(text, root):
        if block_head(text, sp) != 'symbol':
            continue
        body = text[sp[0]:sp[1]]
        if '(lib_id' not in body.split('(property')[0]:
            continue
        m = re.search(r'\(property "Reference" "([^"]*)"', body)
        if m and m.group(1) == ref:
            return sp
    return None


def set_field(text, ref, field, value):
    sp = find_symbol(text, ref)
    if sp is None:
        sys.exit('error: no placed symbol with reference %s' % ref)
    body = text[sp[0]:sp[1]]
    pat = re.compile(r'(\(property "%s" ")((?:[^"\\]|\\.)*)(")' % re.escape(field))
    m = pat.search(body)
    if m:
        old = m.group(2)
        body = body[:m.start(2)] + esc(value) + body[m.end(2):]
    else:
        old = None
        at = re.search(r'\n\t\t\(at ([-\d.e]+) ([-\d.e]+)', body)
        last = list(re.finditer(r'\n\t\t\(property "[^"]+" "[^"]*"\n(?:\t\t\t.*\n)*?\t\t\)', body))[-1]
        body = body[:last.end()] + '\n\t\t' + NEW_PROP % (esc(field), esc(value), at.group(1), at.group(2)) + body[last.end():]
    return text[:sp[0]] + body + text[sp[1]:], old


def main():
    a = sys.argv[1:]
    dry, force = '--dry-run' in a, '--force' in a
    a = [x for x in a if x not in ('--dry-run', '--force')]
    if len(a) < 4 or (len(a) - 1) % 3:
        sys.exit(__doc__)
    path, trip = a[0], a[1:]
    if not os.path.isfile(path):
        sys.exit('error: %s not found' % path)
    d, base = os.path.split(os.path.abspath(path))
    lock = os.path.join(d, '~' + base + '.lck')
    if os.path.exists(lock) and not force and not dry:
        sys.exit('error: KiCad has %s open (%s). Close the schematic or revert it afterwards; --force to edit anyway.'
                 % (base, os.path.basename(lock)))
    text = open(path).read()
    new = text
    for ref, field, value in zip(trip[0::3], trip[1::3], trip[2::3]):
        new, old = set_field(new, ref, field, value)
        print('%s %s: %s -> %s' % (ref, field, '(new field)' if old is None else repr(old), repr(value)))
    if new == text:
        print('no changes')
        return
    if dry:
        return
    # the edited text must still parse and keep every net
    if len(tokenize_blocks(new, 0)) != 1:
        sys.exit('error: result is not one s-expression; nothing written')
    tmp = tempfile.NamedTemporaryFile('w', suffix='.kicad_sch', delete=False)
    tmp.write(new)
    tmp.close()
    try:
        if netlist.nets(path) != netlist.nets(tmp.name):
            sys.exit('error: connectivity changed; nothing written')
    finally:
        os.unlink(tmp.name)
    bdir = os.path.join(d, '.backups')
    os.makedirs(bdir, exist_ok=True)
    bak = os.path.join(bdir, '%s.%s' % (base, time.strftime('%Y%m%d-%H%M%S')))
    shutil.copy2(path, bak)
    part = os.path.join(d, '.%s.tmp' % base)
    with open(part, 'w') as f:
        f.write(new)
    os.replace(part, path)
    print('wrote %s (backup %s)' % (path, bak))


if __name__ == '__main__':
    main()
