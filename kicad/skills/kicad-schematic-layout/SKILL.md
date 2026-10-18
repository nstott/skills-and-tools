---
name: kicad-schematic-layout
description: Turn a label-only or messy KiCad 10 schematic into a readable one - wires for local connections, power symbols for rails, labels only for long nets - using the kicad_sch relayout engine with connectivity verification. Use when the user asks to tidy, relayout, draw connections, or fix overlapping text in a schematic.
---

# Schematic layout

## House style (user preference)
Draw wires between connected pins by default. Use a net label only where a net has too many jumps or wiring it would
get complicated. Concretely:
- **Wire**: parts that belong together (crystal + load caps + series resistor, bus between two ICs, regulator
  parts, LED chains, decoupling next to a pin).
- **Power symbols** (`GND`, `+3V3`, `+1V1`, `VBUS`) on every rail pin instead of long rail wires.
- **Stub + label**: cross-block nets and fan-outs (GPIO to headers, USB, SWD, reset).
- Group blocks, add a bold heading text per block, fill the title block. A3 for a board this size.
- Mirror/rotate a part so its pins line up with its neighbour: an N-wire bus becomes N straight wires.

## Workflow
1. Snapshot connectivity: `PYTHONPATH=${CLAUDE_PLUGIN_ROOT}/tools python3 -m kicad_sch nets SRC.kicad_sch --json nets.json`.
   SRC must be a file **without** generated wires/power symbols; keep one as the permanent source.
2. Copy `tools/kicad_sch/examples/rp2350_devboard_layout.py` and edit: `r.place()` every symbol (grid units of
   1.27 mm), then draw with `r.seg/path`, `r.pwr`, `r.stub_label`, `r.vstub`, `r.pin_or_power`, `r.title`, and set
   text positions with `r.set_text`. `r.pin(ref, num)` gives exact mm coordinates; use it, never guess.
3. Run it: `python3 spec.py SRC DST --expect nets.json`. Nothing is written unless nets are identical.
4. Render and look: `tools/kicad-render.sh DST outdir 6800 --crop X Y W H`. Iterate on overlaps.
5. Install: `tools/sch-install.sh DST PROJECT_DIR` (backup, atomic, ERC). Reload the file in KiCad.
6. Cross-check: `python3 -m kicad_sch diff-nets old new`, `kicad-cli sch erc`, and the MCP `run_erc`.

## KiCad rules the engine already handles (know them if you hand-edit)
- A **pin connects to a wire only at a wire endpoint**. A pin in the middle of a wire is unconnected (ERC:
  "pin not connected"). The engine splits wires at every pin and adds junction dots where 3+ things meet.
- A label connects anywhere along a wire; a wire endpoint on another wire's interior connects.
- Stacked pins (same coordinates) are one connection point.
- A local label named like a power symbol (`GND`) triggers "same local/global label" warnings: use global labels
  for those names. Header GND/+3V3 pins use a long stub + power symbol so the boxes don't overlap neighbours.
- Field text on a rotated (90/270) symbol is relative to the symbol: use text angle 90 or it renders sideways. The
  engine does this. Left/right text justification on rotated symbols anchors unexpectedly: use centred text.
- Strings containing quotes must be escaped (`\"`), otherwise KiCad silently refuses to load the file.
- Rows on a 2.54 mm pitch: text above a wire spills into the row above; boxed labels are as tall as the pitch.
  Increase spacing or move power items out of the text column rather than shrinking text.

## Reviewing a render
Check, in order: text overlapping symbols or other text, labels colliding with pin names, GND symbols sitting on the
next row, reference/value pairs ambiguous between two parts, junction dots missing at T-joins, titles on top of parts.
