#!/usr/bin/env bash
# BOM sanity for JLCPCB assembly: prints grouped BOM with LCSC/MPN and lists parts missing an LCSC number.
#   bom-check.sh FILE.kicad_sch   (run from the project dir if it uses project symbol/footprint libraries)
. "$(dirname "$0")/_env.sh"
f="$1"; [ -f "$f" ] || die "usage: bom-check.sh FILE.kicad_sch"
need "$KICAD_CLI"
csv=$(mktemp -t bom).csv
"$KICAD_CLI" sch export bom --fields 'Reference,Value,Footprint,LCSC,MPN' --group-by 'Value,Footprint,LCSC' \
  --exclude-dnp -o "$csv" "$f" >/dev/null || die "bom export failed"
cat "$csv"
echo "--- missing LCSC (PWR_FLAG ignored):"
awk -F'","' 'NR>1 && $4=="" && $2!="PWR_FLAG" {print $1" "$2}' "$csv" | tr -d '"' || true
