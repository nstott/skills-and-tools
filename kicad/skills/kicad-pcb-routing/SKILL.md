---
name: kicad-pcb-routing
description: Route a placed KiCad PCB with KiCad Routing Tools at the fab's standard tier, keep inner planes solid, then tidy and install the result. Use when asked to route or re-route a board or some nets, to fix routing found by a PCB review (plane cuts, fine vias, USB pair), or to make a board cheaper to fabricate.
---

# Routing a KiCad board

The flow is rules first, then route, then check, then tidy, then install. Work on copies, never on the project file,
until the result is checked. Keep KiCad closed on the project while files are edited: it writes its in-memory board
and rules back on save or close and silently undoes the work.

## 1. Rules
`${CLAUDE_PLUGIN_ROOT}/tools/pcb-rules.py FILE.kicad_pro --fab jlc-standard` sets the track and via floors that the fab
charges nothing extra for (0.127 mm tracks, 0.45/0.2 mm vias). `--check` after any KiCad session or router run; the
router lowers rules to pass its own DRC (`--fab-tier auto`), and KiCad reverts them from memory.

## 2. Route
- One-time: `${CLAUDE_PLUGIN_ROOT}/tools/pcb-route.sh setup` (clones drandyhaas/KiCadRoutingTools into
  `~/.cache/nstott-tools/`, venv, Rust core).
- Whole board: `pcb-route.sh IN.kicad_pcb OUTDIR --planes "GND=In1.Cu +3V3=In2.Cu" --pair "/USB_D+ /USB_D-"
  --power "+3V3=0.25 /VSYS=0.4 /LX1=0.4 ..." --skip "/VREG_LX"`. Stages: planes, each pair, everything else, a
  second pass with more rip-up on what is still open, then `pcb-tidy.py` (prune, widen the `--power` nets, corners).
  It prints DRC and pcb-quality for `OUTDIR/5-final.kicad_pcb`, exits 1 while any net is open or a copper DRC error
  is left, and never touches IN. `--skip` the nets a copper pour already joins inside a keep-out (a reference
  regulator layout), or the router stubs tracks into the keep-out (`items_not_allowed`). Minutes on a small board;
  run it in the background.
- A few nets (re-route after a rip-up): call `route.py` directly with the same choices the wrapper bakes in:
  `--layers F.Cu In1.Cu In2.Cu B.Cu --layer-costs 1 -1 -1 2 --fab-tier standard --escalation off --via-size 0.45
  --via-drill 0.2 --same-net-pad-clearance 0.1`. Pass net lists as separate arguments (`--nets /GP0 /GP3`). In zsh a
  space-joined string in one variable is ONE argument, and the router then reports that the net does not exist.

Router behaviour to plan around:
- `--fab-tier auto` (and escalation) silently drops to 0.15 mm drills and 0.09 mm tracks where it gets stuck. That is
  JLC's costlier tier. Keep `standard` + `--escalation off` and fix what fails by hand.
- `--layer-costs ... -1` forbids a layer for tracks. Give every plane layer -1, or it will route signals on the plane
  and slot it (pcb-review `plane-cuts`).
- `--max-ripup N` may rip copper you placed by hand (it is just copper of another net). Use `--max-ripup 0` when
  routing around hand-placed stubs.
- A net that is partly routed is completed from its existing copper; rip only the bad pieces (In2 segments, one bad
  via) instead of the whole net when the rest is good.
- `route_diff.py` routes the coupled middle well but leaves "hybrid" single-ended legs and can leave USB-C A/B
  bridges and ESD pins unconnected. Hand-place those short structural pieces first, then let it route the pair:
  - USB-C: bridge the two pads of the net with the outer pad on F.Cu below the pad row, and the other net over the
    top of the pads (no vias needed).
  - USBLC6-style ESD: a flow-through track under the body joins pins 1-6 and 3-4.
  Then `--via-cost 200 --diff-pair-intra-match` usually gives a via-free pair.
- A QFN corner with several escapes and one free via site fails at standard tier ("no rippable blockers"). Work out
  the geometry (pad edges, clearance, via radius), hand-place the stubs and the one via, then route the rest.
- Tracks often end anywhere inside a via's copper rather than at its centre; tools that walk copper must allow for it.
- `--same-net-pad-clearance 0.1` is meant to keep vias out of pads, but route.py still dropped +3V3 vias into
  back-side pads (U3 pad 8, C20 pad 1 on rp2350-devboard). The wrapper fails on any pcb-quality `via-in-pad`; move
  such a via just off the pad by hand.
- The congested escapes around a 0.4 mm-pitch QFN (ADC/VSYS_SENSE/GP26-28 on an RP2350) usually stay open after the
  retry; plan to hand-route them (step above).

## 3. Check
- KiCad DRC with zones refilled (`kcli pcb drc --severity-all --refill-zones`): 0 unconnected, 0 copper errors.
- Count vias by size/drill and the narrowest track; nothing below the tier floor.
- `sch-pcb-diff.py`, then the `kicad-pcb-review` skill (`pcb-quality.py`, render and look).

## 4. Tidy
`pcb-tidy.py prune | corners | widen NET=W... | meander NET --add MM` and `pcb-kicad.py fp-update | refs auto` (see
`kicad-pcb-review` section 4). Each keeps only changes after which DRC is no worse.

## 5. Install
KiCad closed on the project; back up the board and project into `.backups/`; copy the result over the project board;
`pcb-rules.py --check`; DRC once more on the installed file. Then tell the user to reload (File > Revert) if KiCad
had it open.
