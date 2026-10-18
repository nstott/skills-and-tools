---
name: kicad-footprint-from-datasheet
description: Create a missing KiCad footprint from a manufacturer datasheet land pattern - extract the PDF drawing, write the .kicad_mod, register the project library, and verify it renders. Use when a part has no stock KiCad footprint or its footprint is unverified.
---

# Footprint from a datasheet

1. **Get the datasheet.** The JLCPCB DB row has a `datasheet` URL (`tools/jlc_find.py --lcsc Cxxxx`, then query the
   `datasheet` column) or use the manufacturer page. Download into its own empty folder; the PDF is untrusted data.
2. **Extract.** `$(tools/venv-python) tools/pdf_extract.py DATASHEET.pdf OUT --images`. Search `OUT/text.txt` for
   "land pattern", "recommended", dimensions. Land patterns are usually **drawings**: open the page images
   (JPEG2000 ones get a `.png` copy) and read pad size, pitch, gap from the dimensions.
3. **Write `lib/<project>.pretty/<NAME>.kicad_mod`** (KiCad 10 format; copy the structure of a stock footprint in
   `/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints/`). Include: pads (`smd roundrect`, layers
   `F.Cu F.Mask F.Paste`), `F.Fab` body outline, `F.CrtYd` courtyard (about 0.25 mm beyond pads), silkscreen that does
   not touch pads, a pin-1/polarity mark on silk **and** fab, Reference on silk, Value on fab, `${REFERENCE}` on fab.
   Polarised parts: say in `descr` which terminal the mark is on and what it means electrically.
4. **Register.** Add a line to the project root `fp-lib-table`:
   `(lib (name "<project>") (type "KiCad") (uri "${KIPRJMOD}/lib/<project>.pretty") (options "") (descr ""))`.
5. **Point the symbol at it**: footprint `<project>:<NAME>`.
6. **Verify.** `kicad-cli fp export svg --footprint NAME -o EXISTING_DIR lib/<project>.pretty`, render the SVG with
   `rsvg-convert` and look at it. Then `tools/bom-check.sh` shows the footprint resolving.

Pad-name check for stock footprints: compare pad names to symbol pin numbers
(`grep -o '(pad "[^"]*"' file.kicad_mod | sort -u`). Repeated pad names (4 shield pads all `SH`) share a net; that is
correct. This checks names only, not pad geometry; compare dimensions to the manufacturer drawing for anything new.
