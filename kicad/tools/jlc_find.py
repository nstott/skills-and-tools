#!/usr/bin/env python3
"""Query the local JLCPCB parts DB (loaded by jlc-load-db.sh) without going through the MCP.

    jlc_find.py --mfr RT6150          manufacturer part, substring
    jlc_find.py --lcsc C165948
    jlc_find.py --passive R 10k 0402  resistor value (k/M/R) + package;  --passive C 100nF 0402 [16V]
    jlc_find.py --sql "package='SOIC-8_208mil' AND description LIKE '%W25Q%'"
Add --basic to keep only JLCPCB Basic parts (no per-part setup fee). Sorted Basic first, then stock.
"""
import os
import sqlite3
import sys

DB = os.environ.get('JLC_DB', os.path.expanduser('~/Library/Application Support/kicad-mcp/jlcpcb_parts.db'))


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    basic = '--basic' in a
    a = [x for x in a if x != '--basic']
    where, args = '1=1', []
    if a[0] == '--mfr':
        where, args = 'mfr_part LIKE ?', ['%' + a[1] + '%']
    elif a[0] == '--lcsc':
        where, args = 'lcsc = ?', [a[1]]
    elif a[0] == '--sql':
        where = a[1]
    elif a[0] == '--passive':
        kind, val, pkg = a[1], a[2], a[3]
        extra = a[4] if len(a) > 4 else None
        if kind.upper() == 'R':
            val = val if val.endswith('Ω') else val.upper().replace('R', '') + 'Ω'
            where = "package=? AND description LIKE ? AND description LIKE '%±1%%' AND subcategory LIKE '%Resistor%'"
            args = [pkg, '%' + val + '%']
        else:
            where = "package=? AND description LIKE ? AND subcategory LIKE '%Capacitor%'"
            args = [pkg, '%' + val + '%']
            if extra:
                where += ' AND description LIKE ?'
                args.append('%' + extra + '%')
    if basic:
        where += " AND library_type='Basic'"
    con = sqlite3.connect(DB)
    q = ("SELECT lcsc, mfr_part, package, library_type, stock, substr(description,1,70) FROM components WHERE %s "
         "ORDER BY (library_type='Basic') DESC, stock DESC LIMIT 15" % where)
    rows = con.execute(q, args).fetchall()
    for r in rows:
        print('%-10s %-26s %-18s %-9s %9d  %s' % r)
    if not rows:
        print('no match (is the DB loaded? run jlc-load-db.sh)')


if __name__ == '__main__':
    main()
