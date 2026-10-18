"""python3 -m kicad_sch nets FILE [--json OUT] | diff-nets A B  (run with PYTHONPATH set to the tools/ folder)"""
import argparse
import sys

from . import netlist


def main():
    ap = argparse.ArgumentParser(prog='kicad_sch')
    sub = ap.add_subparsers(dest='cmd', required=True)
    n = sub.add_parser('nets', help='print nets of a schematic; --json writes a snapshot for Relayout.verify')
    n.add_argument('file')
    n.add_argument('--json')
    d = sub.add_parser('diff-nets', help='compare connectivity of two schematics (pin partitions, names ignored)')
    d.add_argument('a')
    d.add_argument('b')
    a = ap.parse_args()
    if a.cmd == 'nets':
        data = netlist.dump(a.file, a.json)
        for k in sorted(data):
            print('%-24s %3d  %s' % (k.split('|')[0][:24], len(data[k]), ' '.join(data[k][:10]) + (' ...' if len(data[k]) > 10 else '')))
        print('%d nets' % len(data))
    else:
        oa, ob, ren = netlist.diff(netlist.nets(a.a), netlist.nets(a.b))
        for k, v in oa:
            print('only in A:', k, v[:12])
        for k, v in ob:
            print('only in B:', k, v[:12])
        for x, y in ren:
            print('renamed:', x, '->', y)
        print('identical connectivity' if not oa and not ob else 'DIFFERENT')
        sys.exit(1 if (oa or ob) else 0)


if __name__ == "__main__":
    main()
