#!/usr/bin/env bash
# Render a schematic to PNG so layout can be checked by eye (and optionally crop a region).
#   kicad-render.sh FILE.kicad_sch OUTDIR [WIDTH_PX] [--crop X Y W H]
# WIDTH defaults to 3400 (A3 = 8.1 px/mm; use 6800 for detail). Crop coordinates are pixels of that image.
. "$(dirname "$0")/_env.sh"
f="$1"; out="$2"; [ -f "$f" ] && [ -n "$out" ] || die "usage: kicad-render.sh FILE.kicad_sch OUTDIR [WIDTH] [--crop X Y W H]"
shift 2
width=3400
if [ -n "$1" ] && [ "$1" != "--crop" ]; then width="$1"; shift; fi
need "$KICAD_CLI"; need rsvg-convert
mkdir -p "$out"; rm -rf "$out/svg"
kcli sch export svg -o "$out/svg" "$f" >/dev/null || die "kicad-cli export failed"
svg=$(ls "$out"/svg/*.svg | head -1)
rsvg-convert -w "$width" -b white "$svg" -o "$out/sheet.png"
echo "$out/sheet.png"
if [ "$1" = "--crop" ]; then
  sips -c "$5" "$4" --cropOffset "$3" "$2" "$out/sheet.png" --out "$out/crop.png" >/dev/null 2>&1 && echo "$out/crop.png"
fi
