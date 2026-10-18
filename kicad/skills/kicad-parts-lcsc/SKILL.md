---
name: kicad-parts-lcsc
description: Choose JLCPCB/LCSC parts for a KiCad design, check stock and Basic vs Extended, and write LCSC and MPN fields so the BOM is assembly-ready. Use when picking components for JLCPCB SMT assembly or preparing/validating a BOM.
---

# Parts selection for JLCPCB assembly

## Find parts (local DB, no network)
Load the DB once (`kicad-mcp-safety` skill: `tools/jlc-load-db.sh`). Then:
- `tools/jlc_find.py --mfr RT6150` / `--lcsc C165948`
- `tools/jlc_find.py --passive R 10k 0402 --basic`, `--passive C 100nF 0402 16V --basic`
- `tools/jlc_find.py --sql "package='0402' AND description LIKE '%15pF%C0G%'"`

Rules of thumb:
- **Basic** parts have no per-part setup fee; **Extended** cost about $3 each *distinct* part. Reuse values (one 100 nF,
  one 10 k) rather than many near-equivalent ones. ICs, crystals, connectors are usually Extended; that's fine.
- Check `stock` against quantity x boards. Low stock (e.g. 119) is fine for a prototype run, a risk for volume.
- Follow the vendor's reference-design part when the manufacturer is strict (crystals, regulator inductors,
  RP2350 core inductor): match MPN exactly, don't substitute "equivalent".
- 0402 passives need a decent soldering iron by hand; for hand-assembled boards use 0603.
- Pin headers and other through-hole parts: decide hand-solder vs JLC THT service explicitly.
- If the exact connector isn't in the catalogue, a clone may exist (verify the footprint) - record that in the notes.

## Write the fields
Put `LCSC` and `MPN` as hidden symbol fields (the relayout engine's `r.set_part(ref, lcsc, mpn)` does it in a
single atomic write; through the MCP use `add_symbol_property`/`edit_schematic_component`). Mark parts not to be
assembled `DNP`.

## Check
`tools/bom-check.sh FILE.kicad_sch` (run in the project dir so project libraries resolve) prints the grouped BOM
and lists parts without an LCSC number. Pull the JLCPCB-format CSV from the same `kicad-cli sch export bom` call
(fields `Reference,Value,Footprint,LCSC`) when ordering.
