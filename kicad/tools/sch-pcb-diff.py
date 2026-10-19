#!/usr/bin/env python3
"""Compare a schematic with its PCB: parts, values, footprints and the net on every pad.

    sch-pcb-diff.py FILE.kicad_sch FILE.kicad_pcb
Exit 0 when they agree, 1 when they differ. Run it before and after "Update PCB from Schematic", and before
exporting fab files: a value changed only in the schematic otherwise ships stale in the PCB silkscreen/BOM.

Uses `kicad-cli sch export netlist` (run from the schematic's folder so project libraries resolve) for the
schematic side, and reads the .kicad_pcb text for the PCB side. Not compared: PWR_FLAG / power symbols
(schematic only), mounting holes and other board-only footprints (listed as notes).
"""
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kicad_sch.sx import block_head, children  # noqa: E402

KICAD_CLI = os.environ.get('KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')


def norm_net(n):
    if not n or n.startswith('unconnected-'):
        return None
    return n.lstrip('/')


def sch_side(sch):
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
        parts[ref] = {'value': c.findtext('value'), 'footprint': c.findtext('footprint') or '', 'pads': {}}
    for n in root.find('nets'):
        for nd in n:
            if nd.get('ref') in parts:
                parts[nd.get('ref')]['pads'][nd.get('pin')] = norm_net(n.get('name'))
    return parts


def pcb_side(pcb):
    text = open(pcb).read()
    parts = {}
    for sp in children(text, (0, len(text))):
        if block_head(text, sp) != 'footprint':
            continue
        body = text[sp[0]:sp[1]]
        ref = re.search(r'\(property "Reference" "([^"]*)"', body)
        if not ref:
            continue
        fp = re.match(r'\(footprint "([^"]*)"', body).group(1)
        val = re.search(r'\(property "Value" "((?:[^"\\]|\\.)*)"', body)
        pads = {}
        for p in children(text, sp):
            if block_head(text, p) != 'pad':
                continue
            pb = text[p[0]:p[1]]
            num = re.match(r'\(pad "([^"]*)"', pb).group(1)
            net = re.search(r'\(net (?:\d+ )?"([^"]*)"', pb)
            if num:
                pads.setdefault(num, norm_net(net.group(1)) if net else None)
        parts[ref.group(1)] = {'value': val.group(1) if val else '', 'footprint': fp, 'pads': pads}
    return parts


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sch, pcb = sch_side(sys.argv[1]), pcb_side(sys.argv[2])
    bad = 0

    def report(msg):
        nonlocal bad
        bad += 1
        print(msg)

    for ref in sorted(set(sch) - set(pcb)):
        report('only in schematic: %s (not placed on the PCB: run Update PCB from Schematic)' % ref)
    for ref in sorted(set(sch) & set(pcb)):
        s, p = sch[ref], pcb[ref]
        if s['value'] != p['value']:
            report('%s value: schematic %r, PCB %r' % (ref, s['value'], p['value']))
        # newer KiCad saves the PCB footprint name without its library prefix, so compare names only
        if s['footprint'] and s['footprint'].split(':')[-1] != p['footprint'].split(':')[-1]:
            report('%s footprint: schematic %s, PCB %s' % (ref, s['footprint'], p['footprint']))
        for pin, net in sorted(s['pads'].items()):
            if pin not in p['pads']:
                report('%s pad %s: in schematic (%s), no such pad in PCB footprint' % (ref, pin, net))
            elif p['pads'][pin] != net:
                report('%s pad %s: schematic net %s, PCB net %s' % (ref, pin, net, p['pads'][pin]))
    for ref in sorted(set(pcb) - set(sch)):
        print('note, only on the PCB: %s (%s)' % (ref, pcb[ref]['footprint']))
    print('%d difference(s)' % bad if bad else 'schematic and PCB agree (%d parts)' % len(set(sch) & set(pcb)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
