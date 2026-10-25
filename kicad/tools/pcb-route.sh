#!/usr/bin/env bash
# Auto-route a placed board with KiCad Routing Tools (drandyhaas), at the fab's standard tier, planes kept clean.
#   pcb-route.sh setup                                   clone/update the router, venv, Rust core
#   pcb-route.sh IN.kicad_pcb OUTDIR [--planes "GND=In1.Cu +3V3=In2.Cu"] [--pair "/USB_D+ /USB_D-"]...
#                [--power "+3V3=0.25 /VSYS=0.4 ..."] [--nets "PATTERN ..."] [--skip "NET ..."] [--ripup N]
# Stages in OUTDIR: 0-in, 1-planes, 2-pairN, 3-route, 4-retry (open nets again, more rip-up), 5-final (pcb-tidy prune,
# widen the --power nets, straighten corners). Prints DRC (zones refilled) and pcb-quality summaries; exit 1 while any
# net is open or a copper DRC error is left. --skip: nets a copper pour already joins (a reference regulator's LX pour
# inside a no-tracks keep-out), which the router would otherwise stub into the keep-out.
# Never touches IN: review OUTDIR/5-final.kicad_pcb, then install it yourself (backup first, KiCad closed).
# Choices baked in (each learned the hard way on rp2350-devboard):
#   --fab-tier standard --escalation off  : `auto` quietly falls back to 0.15 mm drills / 0.09 mm tracks (surcharge)
#   plane layers get layer cost -1         : no signal track may slot a plane; other inner layers cost 4, B.Cu 2
#   via 0.45/0.2, clearance from the project rules (pcb-rules.py sets the floors first)
. "$(dirname "$0")/_env.sh"
KRT="${KRT_DIR:-$HOME/.cache/nstott-tools/KiCadRoutingTools}"
VENV="${KRT_VENV:-$HOME/.cache/nstott-tools/krt-venv}"
PY="$VENV/bin/python"

if [ "$1" = "setup" ]; then
  need git; need python3
  if [ -d "$KRT/.git" ]; then git -C "$KRT" pull -q --ff-only || die "git pull failed in $KRT"
  else git clone -q https://github.com/drandyhaas/KiCadRoutingTools.git "$KRT" || die "clone failed"; fi
  [ -x "$PY" ] || python3 -m venv "$VENV" || die "venv failed"
  "$PY" -m pip install -q -r "$KRT/requirements.txt" || die "pip install failed"
  (cd "$KRT" && "$PY" build_router.py) || die "Rust router build/download failed (needs cargo to build from source)"
  echo "KiCad Routing Tools ready: $KRT ($(cat "$KRT/VERSION" 2>/dev/null))"
  exit 0
fi

in="$1"; out="$2"
[ -f "$in" ] && [ -n "$out" ] || die "usage: pcb-route.sh setup | pcb-route.sh IN.kicad_pcb OUTDIR [--planes ..] [--pair ..] [--power ..] [--nets ..] [--skip ..] [--ripup N]"
shift 2
planes=""; power=""; nets="*"; skip=""; ripup=6; pairs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --planes) planes="$2"; shift 2;;
    --pair) pairs+=("$2"); shift 2;;
    --power) power="$2"; shift 2;;
    --nets) nets="$2"; shift 2;;
    --skip) skip="$2"; shift 2;;
    --ripup) ripup="$2"; shift 2;;
    *) die "unknown option $1";;
  esac
done
[ -x "$PY" ] && [ -f "$KRT/py_router/route.py" ] || die "router not installed: run pcb-route.sh setup"
pro="${in%.kicad_pcb}.kicad_pro"; [ -f "$pro" ] || die "no $pro beside the board (the router reads its rules)"
mkdir -p "$out"; out="$(cd "$out" && pwd)"
cp "$in" "$out/0-in.kicad_pcb"
# every stage gets the project file under its own name, so KiCad tools find the rules
stage() { cp "$pro" "$out/$1.kicad_pro"; }
stage 0-in

# DRC with zones refilled on a scratch copy beside IN, so ${KIPRJMOD} libraries and the project rules resolve.
# Prints a summary, writes OUTDIR/NAME.drc.json, sets OPEN_NETS and COPPER_ERRORS.
check() {
  local f="$1" name scratch
  name="$(basename "${f%.kicad_pcb}")"
  scratch="$(dirname "$in")/.pcb-route-check-$$"
  cp "$f" "$scratch.kicad_pcb"; cp "$pro" "$scratch.kicad_pro"
  kcli pcb drc --severity-all --refill-zones --format json -o "$out/$name.drc.json" "$scratch.kicad_pcb" >/dev/null
  rm -f "$scratch.kicad_pcb" "$scratch.kicad_pro" "$scratch.kicad_prl"
  read -r COPPER_ERRORS OPEN_NETS <<<"$(python3 "$TOOLS_DIR/pcb-route-drc.py" "$out/$name.drc.json" "$name" 2>"$out/$name.drc.txt")"
  cat "$out/$name.drc.txt"
}

# copper layers from the board, costs: F.Cu 1, B.Cu 2, plane layers -1, other inner 4
read -r layers costs <<<"$(python3 - "$in" "$planes" <<'PYEOF'
import re, sys
t = open(sys.argv[1]).read()
blk = re.search(r'\(layers\s*\n(.*?)\n\t\)', t, re.S).group(1)
cu = re.findall(r'\(\d+ "([^"]+\.Cu)"', blk)
pl = {p.split('=')[1] for p in sys.argv[2].split() if '=' in p}
cost = {'F.Cu': '1', 'B.Cu': '2'}
print(','.join(cu), ','.join('-1' if l in pl else cost.get(l, '4') for l in cu))
PYEOF
)"
L=(${layers//,/ }); C=(${costs//,/ })
COMMON=(--fab-tier standard --escalation off --same-net-pad-clearance 0.1 --via-size 0.45 --via-drill 0.2)
cur="$out/0-in.kicad_pcb"

if [ -n "$planes" ]; then
  pn=(); plyr=()
  for p in $planes; do pn+=("${p%%=*}"); plyr+=("${p#*=}"); done
  stage 1-planes
  (cd "$KRT" && "$PY" py_router/route_planes.py "$cur" "$out/1-planes.kicad_pcb" --nets "${pn[@]}" --plane-layers "${plyr[@]}" \
     --track-width 0.15 --skip-existing-zones "${COMMON[@]}") > "$out/1-planes.log" 2>&1 || die "plane step failed: $out/1-planes.log"
  cur="$out/1-planes.kicad_pcb"
fi

i=0
for pr in "${pairs[@]}"; do
  i=$((i + 1)); read -r P N <<<"$pr"
  stage "2-pair$i"
  (cd "$KRT" && "$PY" py_router/route_diff.py "$cur" "$out/2-pair$i.kicad_pcb" --nets "$P" "$N" --layers F.Cu B.Cu \
     --diff-pair-gap 0.15 --track-width 0.16 --via-cost 200 --diff-pair-intra-match --length-match-tolerance 0.5 \
     "${COMMON[@]}") > "$out/2-pair$i.log" 2>&1 || die "pair step failed: $out/2-pair$i.log"
  cur="$out/2-pair$i.kicad_pcb"
done

set -f  # net patterns such as "*" or "/GP*" must reach the router unexpanded
if [ -n "$skip" ]; then
  # route.py has no exclusion syntax: expand the patterns to every pad net, minus --skip
  nets="$(python3 - "$cur" "$nets" "$skip" <<'PYEOF'
import re, sys, fnmatch
t = open(sys.argv[1]).read()
found = set(re.findall(r'\(pad "[^"]*"[\s\S]*?\(net (?:\d+ )?"([^"]+)"\)', t))
all_nets = sorted(n for n in found if n and not n.startswith('unconnected-'))
pats, skip = sys.argv[2].split(), set(sys.argv[3].split())
print(' '.join(n for n in all_nets if any(fnmatch.fnmatchcase(n, p) for p in pats) and n not in skip))
PYEOF
)"
fi
pw=(); pwv=()
for p in $power; do pw+=("${p%%=*}"); pwv+=("${p#*=}"); done
args=(--layers "${L[@]}" --layer-costs "${C[@]}" --track-width 0.15)
[ ${#pw[@]} -gt 0 ] && args+=(--power-nets "${pw[@]}" --power-nets-widths "${pwv[@]}")
stage 3-route
(cd "$KRT" && "$PY" py_router/route.py "$cur" "$out/3-route.kicad_pcb" --nets $nets --max-ripup "$ripup" "${args[@]}" "${COMMON[@]}") \
  > "$out/3-route.log" 2>&1 || die "route step failed: $out/3-route.log"
set +f
cur="$out/3-route.kicad_pcb"
check "$cur"

# second pass on whatever is still open, with more rip-up (the first pass's order often leaves a few stuck)
if [ -n "$OPEN_NETS" ]; then
  stage 4-retry
  set -f
  (cd "$KRT" && "$PY" py_router/route.py "$cur" "$out/4-retry.kicad_pcb" --nets $OPEN_NETS --max-ripup 25 "${args[@]}" "${COMMON[@]}") \
    > "$out/4-retry.log" 2>&1 || die "retry step failed: $out/4-retry.log"
  set +f
  cur="$out/4-retry.kicad_pcb"
  check "$cur"
fi

# tidy: dangling copper, power widths (the router necks pad entries even where the full width fits), acute corners
stage 5-final
cp "$cur" "$out/5-final.kicad_pcb"
tidy="$TOOLS_DIR/pcb-tidy.py"
"$tidy" "$out/5-final.kicad_pcb" prune > "$out/5-final.tidy.log" 2>&1
[ -n "$power" ] && "$tidy" "$out/5-final.kicad_pcb" widen $power >> "$out/5-final.tidy.log" 2>&1
"$tidy" "$out/5-final.kicad_pcb" corners >> "$out/5-final.tidy.log" 2>&1
grep -cE '^prune ' "$out/5-final.tidy.log" | sed 's/^/tidy: pruned /'
grep -E '^(widened|below target)' "$out/5-final.tidy.log" | head -8 | sed 's/^/tidy: /'
grep -cE '^straightened' "$out/5-final.tidy.log" | sed 's/^/tidy: straightened corners /'
grep -hE 'Single-ended:|Multi-point:|Diff pairs:' "$out"/*.log | cut -c1-120
check "$out/5-final.kicad_pcb"
sch="${in%.kicad_pcb}.kicad_sch"
[ -f "$sch" ] && cp "$sch" "$out/5-final.kicad_sch"
python3 "$TOOLS_DIR/pcb-quality.py" "$out/5-final.kicad_pcb" > "$out/5-final.quality.txt"
grep -E '^(BAD|WARN)' "$out/5-final.quality.txt" | head -40
# the router can drop vias into same-net pads despite --same-net-pad-clearance (seen on back-side parts)
vip=$(grep -c 'via-in-pad' "$out/5-final.quality.txt")
echo "result: $out/5-final.kicad_pcb"
if [ -n "$OPEN_NETS" ] || [ "$COPPER_ERRORS" != "0" ] || [ "$vip" != "0" ]; then
  echo "NOT DONE: $COPPER_ERRORS copper DRC errors; $vip via(s) in pads; open nets: ${OPEN_NETS:-none}. Fix those by hand (see the kicad-pcb-routing skill)." >&2
  exit 1
fi
echo "routed: 0 open, 0 copper DRC errors (next: pcb-kicad.py fp-update / refs auto, review, then install)"
