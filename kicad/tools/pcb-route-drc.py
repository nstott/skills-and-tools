#!/usr/bin/env python3
"""Helper for pcb-route.sh: summarise a kicad-cli DRC JSON report.

    pcb-route-drc.py DRC.json NAME

Prints the summary (violation counts by type and open connections) to stderr, and
`COPPER_ERRORS OPEN_NET OPEN_NET ...` on stdout for the shell to read.
"""
import collections
import json
import re
import sys

COPPER = ('clearance', 'shorting_items', 'tracks_crossing', 'hole_clearance', 'copper_edge_clearance',
          'solder_mask_bridge', 'hole_to_hole', 'items_not_allowed')

d = json.load(open(sys.argv[1]))
name = sys.argv[2] if len(sys.argv) > 2 else ''
sys.stderr.write('%s DRC: %s | unconnected: %d\n' % (name, dict(collections.Counter(v['type'] for v in d['violations'])),
                                                    len(d['unconnected_items'])))
for u in d['unconnected_items'][:20]:
    sys.stderr.write('  open: %s\n' % ' <-> '.join(i['description'][:50] for i in u['items']))
nets = sorted({m.group(1) for u in d['unconnected_items'] for i in u['items']
               for m in [re.search(r'\[([^\]]+)\]', i['description'])] if m})
print(sum(1 for v in d['violations'] if v['type'] in COPPER), ' '.join(nets))
