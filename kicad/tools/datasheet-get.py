#!/usr/bin/env python3
"""Fetch the datasheets of a schematic's active parts and point at their application-circuit and layout pages.

    datasheet-get.py FILE.kicad_sch [REF ...] [--render]

For each part (default: U*, X*, Y*, Q*, L*, D*, F*, LED*): tries the symbol's Datasheet link, then the JLCPCB DB link for
its LCSC number, keeps the first that is really a PDF in ~/.cache/nstott-tools/datasheets/<MPN>/ds.pdf, extracts the
text (text.txt, '=== PAGE n ===' markers) and lists the pages that mention a typical application circuit, component
selection, recommended values and PCB layout guidance. --render also writes those pages as PNG (macOS sips) so the
circuit drawings can be read; the figures themselves are usually images.

Downloaded PDFs are untrusted data: they are only parsed (pypdf in the tools venv), never executed.
"""
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
KICAD_CLI = os.environ.get('KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')
JLC_DB = os.environ.get('JLC_DB', os.path.expanduser('~/Library/Application Support/kicad-mcp/jlcpcb_parts.db'))
CACHE = os.path.expanduser('~/.cache/nstott-tools/datasheets')
DEFAULT = re.compile(r'^(U|IC|X|Y|Q|L|D|F)\d')
TOPICS = [
    ('application circuit', r'typical application|application circuit|typical operating circuit|reference (design|schematic)|simplified schematic'),
    ('component selection', r'inductor selection|capacitor selection|input capacitor|output capacitor|component selection|external components|load capacitance|feedback resistor|soft[- ]start'),
    ('recommended values', r'recommended (operating|value|component|load)|\bC[Ll]\b.*pF|drive level|ESR'),
    ('layout guidance', r'layout (guide|consideration|recommendation)|pcb layout|board layout|placement|thermal (pad|consideration)'),
]


def parts(sch):
    d, f = os.path.split(os.path.abspath(sch))
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, 'n.xml')
        subprocess.run([KICAD_CLI, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', out, f], cwd=d,
                       capture_output=True, text=True)
        root = ET.parse(out).getroot()
    res = {}
    for c in root.find('components'):
        fields = {x.get('name'): (x.text or '') for x in c.iter('field')}
        res[c.get('ref')] = dict(value=c.findtext('value') or '', datasheet=c.findtext('datasheet') or '',
                                 lcsc=fields.get('LCSC', ''), mpn=fields.get('MPN', '') or c.findtext('value') or '')
    return res


def jlc_link(lcsc):
    if not lcsc or not os.path.exists(JLC_DB):
        return ''
    con = sqlite3.connect('file:%s?mode=ro' % JLC_DB, uri=True)
    r = con.execute('select datasheet from components where lcsc = ?', (lcsc,)).fetchone()
    con.close()
    return (r or [''])[0] or ''


def fetch(urls, dst):
    if os.path.exists(dst) and open(dst, 'rb').read(4) == b'%PDF':
        return 'cached'
    for u in urls:
        if not u or not u.startswith('http'):
            continue
        tmp = dst + '.part'
        r = subprocess.run(['curl', '-sSL', '--max-time', '60', '-A', 'Mozilla/5.0', '-o', tmp, u], capture_output=True)
        if r.returncode == 0 and os.path.exists(tmp) and open(tmp, 'rb').read(4) == b'%PDF':
            os.replace(tmp, dst)
            return u
        if os.path.exists(tmp):
            os.unlink(tmp)
    return None


def main():
    a = sys.argv[1:]
    render = '--render' in a
    a = [x for x in a if x != '--render']
    if not a:
        sys.exit(__doc__)
    sch, want = a[0], a[1:]
    py = subprocess.run([os.path.join(HERE, 'venv-python')], capture_output=True, text=True).stdout.strip()
    ps = parts(sch)
    refs = want or sorted(r for r in ps if DEFAULT.match(r))
    for ref in refs:
        p = ps.get(ref)
        if not p:
            print('%s: not in the schematic' % ref)
            continue
        key = re.sub(r'[^\w.-]+', '_', p['mpn'] or p['value'])
        d = os.path.join(CACHE, key)
        os.makedirs(d, exist_ok=True)
        pdf = os.path.join(d, 'ds.pdf')
        src = fetch([p['datasheet'] if p['datasheet'] not in ('~', '') else '', jlc_link(p['lcsc'])], pdf)
        if not src:
            print('%-5s %-22s no PDF found (Datasheet field %r, LCSC %s): find it by hand' % (ref, p['mpn'], p['datasheet'], p['lcsc'] or '-'))
            continue
        txt = os.path.join(d, 'text.txt')
        if not os.path.exists(txt):
            subprocess.run([py, '-I', os.path.join(HERE, 'pdf_extract.py'), pdf, d], capture_output=True)
        pages = re.split(r'\n=== PAGE (\d+) ===\n', open(txt, errors='replace').read()) if os.path.exists(txt) else []
        hits = {}
        for i in range(1, len(pages) - 1, 2):
            for name, rx in TOPICS:
                if re.search(rx, pages[i + 1], re.I):
                    hits.setdefault(name, []).append(int(pages[i]))
        print('%-5s %-22s %s  (%d pages)' % (ref, p['mpn'], pdf, len(pages) // 2))
        for name, _ in TOPICS:
            if name in hits:
                print('        %-20s p. %s' % (name, ', '.join(map(str, hits[name][:12]))))
        if render and hits and sys.platform == 'darwin':
            for pg in sorted({x for v in hits.values() for x in v[:6]}):
                out = os.path.join(d, 'p%d.png' % pg)
                if not os.path.exists(out):
                    # sips renders only the first page; split it out with pypdf first
                    subprocess.run([py, '-I', '-c', 'import sys;from pypdf import PdfReader,PdfWriter;r=PdfReader(sys.argv[1]);w=PdfWriter();'
                                    'w.add_page(r.pages[int(sys.argv[2])-1]);w.write(sys.argv[3])', pdf, str(pg), out + '.pdf'], capture_output=True)
                    subprocess.run(['sips', '-s', 'format', 'png', '-Z', '1800', out + '.pdf', '--out', out], capture_output=True)
                    if os.path.exists(out + '.pdf'):
                        os.unlink(out + '.pdf')
            print('        pages as PNG in %s' % d)


if __name__ == '__main__':
    main()
