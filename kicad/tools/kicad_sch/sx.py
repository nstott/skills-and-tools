"""Minimal KiCad s-expression helpers: split a file into blocks without a full parser, read symbol pins."""
import re

def tokenize_blocks(text, start_depth=1):
    """Yield (start, end) spans of every depth-`start_depth` child '(...)' in the root list."""
    spans, depth, i, n = [], 0, 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == '\\': i += 1
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == '(':
                depth += 1
                if depth == start_depth + 1: s = i
            elif c == ')':
                if depth == start_depth + 1: spans.append((s, i + 1))
                depth -= 1
        i += 1
    return spans

def block_head(text, span):
    m = re.match(r'\(\s*([^\s()"]+)', text[span[0]:span[1]])
    return m.group(1)

def children(text, span):
    """Depth-1 children of the block at `span` (returns absolute spans)."""
    sub = text[span[0]:span[1]]
    return [(span[0] + a, span[0] + b) for a, b in tokenize_blocks(sub, 1)]

def parse_lib_pins(text, lib_span):
    """-> {lib_id: [(number, name, x, y, angle)]} from the lib_symbols block."""
    out = {}
    for sp in children(text, lib_span):
        if block_head(text, sp) != 'symbol': continue
        body = text[sp[0]:sp[1]]
        lib_id = re.match(r'\(symbol "([^"]+)"', body).group(1)
        pins = []
        for sp2 in children(text, sp):
            if block_head(text, sp2) != 'symbol': continue
            for sp3 in children(text, sp2):
                if block_head(text, sp3) != 'pin': continue
                pb = text[sp3[0]:sp3[1]]
                at = re.search(r'\(at ([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\)', pb)
                num = re.search(r'\(number "([^"]+)"', pb).group(1)
                nm = re.search(r'\(name "([^"]*)"', pb).group(1)
                hide = '(hide yes)' in pb.split('(name')[0]
                pins.append((num, nm, float(at.group(1)), float(at.group(2)), float(at.group(3))))
        out[lib_id] = pins
    return out
