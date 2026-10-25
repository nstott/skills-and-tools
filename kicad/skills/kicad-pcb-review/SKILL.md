---
name: kicad-pcb-review
description: Review a KiCad PCB layout against general board-quality signals (decoupling, switching loops, return paths and plane cuts, crystal, differential pairs, power widths, via use, fab margin, silkscreen, assembly) and flag every place the board deviates, with part/net and coordinates. Use when asked to review, sanity-check or sign off a PCB, after auto-routing, or before fab export.
---

# KiCad PCB review

Report deviations, not a tour: every finding names the part / net, the layer and the board coordinates, says which
quality signal it breaks and why it matters, and proposes a fix. Don't say "looks fine" for an area you did not measure
or look at.

## 1. Measure (run these, show the numbers)
- **Measured signals**: `${CLAUDE_PLUGIN_ROOT}/tools/pcb-quality.py FILE.kicad_pcb [--sch FILE.kicad_sch] [--fab jlc-standard|jlc-advanced]`.
  It reads pin types from the schematic netlist when the `.kicad_sch` sits beside the board. The checks and their
  thresholds are listed in its header. Treat its output as candidates: confirm each in the render before reporting it.
- **KiCad DRC with zones refilled** (without the refill, every plane pad reads as unconnected):
  `. ${CLAUDE_PLUGIN_ROOT}/tools/_env.sh; kcli pcb drc --severity-all --refill-zones --format json -o /tmp/drc.json FILE.kicad_pcb`
  (minutes on a dense board; run it in the background). Summarise by type. Triage every violation; never exclude to get to zero.
- **Schematic parity**: `${CLAUDE_PLUGIN_ROOT}/tools/sch-pcb-diff.py FILE.kicad_sch FILE.kicad_pcb`.
- **Fab floor actually used**: compare the project's `rules.min_*` in `.kicad_pro` with the fab's standard capability.
  Auto-routers (KiCad Routing Tools `--fab-tier auto`) lower these silently, so DRC passes at a costlier tier.
- **Render and look**: `kcli pcb export svg --layers "F.Cu,In1.Cu,In2.Cu,B.Cu,Edge.Cuts,F.SilkS" --page-size-mode 2
  --exclude-drawing-sheet -o board.svg FILE.kicad_pcb`, convert with `rsvg-convert`, and crop around each IC, regulator,
  crystal and connector (pixels per mm = image width / board width). One layer at a time is easier to read.

## 2. Quality signals and how to judge them
Electrical:
- **Return paths**: every fast signal runs over an unbroken GND plane on the adjacent layer. Tracks routed *on* a plane
  layer slot it (`plane-cuts`); a signal crossing a slot detours its return current. Bad: a clock/USB/QSPI line over a
  slot, or any signal track on the GND plane layer. A power plane as reference (`planes` info) is acceptable only with
  stitching caps.
- **Switching loops tiny**: regulator input cap, switch pins, inductor and output cap within a few mm, short and wide
  (`switching`). Check by eye that the input cap's GND and the IC's GND meet directly, not through a long plane path.
- **Exposed pads tied down**: an IC's exposed pad needs a via array into the GND plane (`exposed-pad`). On the
  RP2350 QFN-60 it is the chip's only GND; a footprint without vias leaves the whole return on surface tracks. Use
  KiCad's `..._ThermalVias` footprint variant, or same-net vias in the pad (not counted as via-in-pad).
- **Decoupling at the pins**: 100 nF within about 2 mm of each supply pin with its own GND via next to the cap pad
  (`decoupling`). A cap on the far side of the board, or tied in through a long thin track, does little.
- **Few vias on critical nets**: USB, crystal and clock nets stay on one layer; a layer change needs a GND via beside it
  (`critical-vias`, `diff-pairs`).
- **Crystal quiet**: load caps right at the crystal pins, GND around it, no other nets under or through it (`crystal`).
- **Differential pairs coupled and matched**: constant gap, short uncoupled legs, length matched across series resistors
  (`diff-pairs`). Rough tolerance: USB full speed is forgiving (a few mm); USB high speed, Ethernet and MIPI are not
  (well under 1 mm, 90/100 ohm impedance from the stackup).
- **Power widths match current**: about 0.3 mm per amp on 1 oz outer copper as a floor; necks only at pad entries
  (`power-width`).
- **Stitching**: GND vias spread across the board, near connectors and along the edge (`stitching`).

Manufacturing:
- **Margin over the fab minimum**: copper at the fab's absolute limit is fragile and sub-standard vias cost extra
  (`fab-margin`). Note every feature below the standard tier.
- **No via-in-pad** unless it is specified as filled and capped (`via-in-pad`).
- **Thermal reliefs** on through-hole and plane pads (DRC `starved_thermal`).
- **Silkscreen** off pads and the board edge, refdes readable, pin 1 and polarity marked (DRC `silk_*`).
- **Assembly spacing**: courtyards clear, polarised parts in consistent orientations, fiducials if a non-JLC assembler
  will place it (`assembly`).

Hygiene:
- 45 degree corners, no acute angles, no doubled-back (0 degree) segments, no dangling stubs (`geometry`, DRC
  `track_dangling`).
- Placement follows the signal flow (connector, then protection, then regulator, then MCU, then headers), so tracks are
  short and seldom cross.
- Test points on the rails and key signals; the debug header reachable.

## 3. Report
Group the findings as **must fix** (will misbehave or fail fab/assembly: plane slots under fast signals, decoupling far
from pins, shorts or unconnected items, via-in-pad without fill), **should fix** (works but fragile or costly: long
switching loops, mismatched pairs, sub-standard vias, acute corners, silkscreen over pads) and **notes** (choices to
confirm). For each give part/net, layer and coordinates, the signal it breaks, and the fix (for example "move C23 next to
X1 pin 1", "re-route /GP7 off In1.Cu", or "re-run the router with `--layers F.Cu In1.Cu In2.Cu B.Cu --layer-costs 1 -1 -1 3`
so the planes stay solid"). State what was *not* checked: impedance (needs a stackup), thermal, EMC, mechanical fit.

## 4. Fixing the hygiene findings
When asked to fix them, use the DRC-checked tools instead of hand-editing copper (each keeps a change only if KiCad
DRC with zones refilled is no worse; backup in `.backups/`; close the board in KiCad first):
- dangling stubs, duplicate segments: `${CLAUDE_PLUGIN_ROOT}/tools/pcb-tidy.py FILE.kicad_pcb prune`
- acute corners: `pcb-tidy.py FILE.kicad_pcb corners`
- via-in-pad: `pcb-tidy.py FILE.kicad_pcb vias` (moves the via just off the pad, or drops a redundant one; same-net
  thermal vias in an exposed pad are left alone)
- power widths / sub-floor tracks: `pcb-tidy.py FILE.kicad_pcb widen /LX1=0.4 /VSYS=0.4 +3V3=0.127`
- pair skew (after `pcb-quality.py` reports the signal-path mismatch): `pcb-tidy.py FILE.kicad_pcb meander /USB_D- --add 2.1`
- `lib_footprint_mismatch`: `${CLAUDE_PLUGIN_ROOT}/tools/pcb-kicad.py FILE.kicad_pcb fp-update` (all flagged parts)
- refdes over pads / other text / the edge: `pcb-kicad.py FILE.kicad_pcb refs hide H1 H2 H3 H4` (mounting holes), then
  `pcb-kicad.py FILE.kicad_pcb refs auto`. Silk that is part of a footprint's own outline (a connector overhanging the
  edge) is a footprint edit, not a text move.
Each DRC run takes seconds; a full pass is a few minutes. Re-run `pcb-quality.py` afterwards and report what was left
and why (a segment that cannot widen without a clearance error is reported with its coordinates).
