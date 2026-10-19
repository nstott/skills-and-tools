# kicad

Circuit design skills and tools for KiCad 10 projects (installed as `kicad@nstott`).

## Skills
- `kicad-mcp-safety` - keep work safe when driving the KiCad MCP; JLCPCB DB load; backend restart.
- `kicad-schematic-layout` - tidy a schematic: wires for local nets, power symbols, labels for long nets.
- `kicad-schematic-review` - ERC + netlist + design checklist, how to report.
- `kicad-parts-lcsc` - pick JLCPCB parts, LCSC/MPN fields, BOM check.
- `kicad-footprint-from-datasheet` - make and verify a footprint from a datasheet land pattern.
- `rp2350-design` - RP2350 supply/clock/flash/USB/Pico 2 pinout notes.
- `kicad-pcb-review` - measure a PCB against layout-quality signals (decoupling, loops, plane cuts, pairs, fab margin) and report deviations.

## Tools
See `tools/README.md`.

## Adding to it
Put what we learn on a project here as soon as it is reusable: a gotcha goes in the relevant skill, a repeatable
step becomes a script in `tools/` and gets mentioned in a skill. Test scripts on a throwaway copy of a project.
Iterate without version bumps via `../dev-sync.sh`, then `/reload-plugins`.
