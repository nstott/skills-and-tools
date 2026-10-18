"""Read a KiCad 10 .kicad_sch and extract its nets (no KiCad needed). Used to snapshot / diff connectivity."""
import json
import re

from .core import Sheet, connectivity
from .sx import block_head


def _xy(block):
    m = re.search(r'\(at ([-\d.e]+) ([-\d.e]+)', block)
    return float(m.group(1)), float(m.group(2))


def load(path):
    """-> (sheet, comps, wires, labels, powers) with power symbols (lib_id power:*, not PWR_FLAG) as named points."""
    text = open(path).read()
    sh = Sheet(text)
    comps = [(s['ref'], s['lib_id'], s['x'], s['y'], s['rot'], s['mirror']) for s in sh.symbols]
    wires, labels, powers = [], [], []
    for sp in sh.top:
        h, b = block_head(text, sp), text[sp[0]:sp[1]]
        if h in ('label', 'global_label'):
            labels.append((re.match(r'\(%s "([^"]+)"' % h, b).group(1), _xy(b)))
        elif h == 'wire':
            m = re.search(r'\(xy ([-\d.e]+) ([-\d.e]+)\) \(xy ([-\d.e]+) ([-\d.e]+)\)', b)
            wires.append(((float(m.group(1)), float(m.group(2))), (float(m.group(3)), float(m.group(4)))))
    for s in sh.symbols:
        if s['lib_id'].startswith('power:') and s['lib_id'] != 'power:PWR_FLAG':
            b = text[s['span'][0]:s['span'][1]]
            powers.append((re.search(r'\(property "Value" "([^"]*)"', b).group(1), (s['x'], s['y'])))
    return sh, comps, wires, labels, powers


def nets(path):
    """-> {'NAME|NAME2' or '<unnamed>|pins': sorted ['REF/PIN', ...]}; power-symbol pseudo parts (#PWR) are left out."""
    sh, comps, wires, labels, powers = load(path)
    out = {}
    for k, pins in connectivity(sh.lib_pins, comps, wires, labels, powers).items():
        pins = sorted('%s/%s' % p for p in pins if not p[0].startswith('#'))
        if not pins:
            continue
        out['|'.join(k) if k[0] != '<unnamed>' else '<unnamed>|' + ','.join(pins)] = pins
    return out


def diff(a, b):
    """Compare two nets() results by pin partition. Returns (only_in_a, only_in_b, renamed)."""
    sa = {frozenset(v): k for k, v in a.items()}
    sb = {frozenset(v): k for k, v in b.items()}
    only_a = [(sa[s], sorted(s)) for s in sa if s not in sb]
    only_b = [(sb[s], sorted(s)) for s in sb if s not in sa]
    renamed = [(sa[s], sb[s]) for s in sa if s in sb and sa[s] != sb[s] and not sa[s].startswith('<') and not sb[s].startswith('<')]
    return only_a, only_b, renamed


def dump(path, out=None):
    data = nets(path)
    if out:
        json.dump(data, open(out, 'w'), indent=1, sort_keys=True)
    return data
