# kicad/tools

Scripts the skills call via `${CLAUDE_PLUGIN_ROOT}/tools/...`. Paths and tool locations come from `_env.sh`
(override with `KICAD_APP`, `KICAD_MCP_DIR`, `JLC_DB`).

| Tool | What it does |
|---|---|
| `kicad_sch/` | Python package. `relayout.py` layout engine (wires, power symbols, labels, verified + atomic write); `netlist.py` + `python3 -m kicad_sch nets / diff-nets`. Needs `PYTHONPATH=<this folder>`. Example spec: `kicad_sch/examples/rp2350_devboard_layout.py`. |
| `sch-install.sh NEW PROJECT_DIR` | Timestamped backup in `PROJECT_DIR/.backups`, atomic replace, then `kicad-cli` ERC. |
| `sch-pcb-diff.py SCH PCB` | Compare schematic and PCB: missing parts, value and footprint mismatches, net on every pad. Exit 1 on any difference (board-only parts such as mounting holes are notes). Run before/after "Update PCB from Schematic" and before fab export. |
| `kicad-render.sh FILE OUTDIR [W] [--crop X Y W H]` | Schematic to PNG (+ crop) for visual checks. |
| `sch-quality.py SCH [--json OUT] [--no-erc]` | Schematic quality signals with ref/net and sheet position: ERC by type, title block, notes, unnamed nets on IC pins, parts with no wires, 4-way junctions, decoupling coverage, cap voltage rating vs rail (DC-bias derating), missing fields, overlapping text, and the active parts to check against their datasheets. Used by `kicad-schematic-review`. |
| `datasheet-get.py SCH [REF ...] [--render]` | Download each active part's datasheet (symbol link, else the JLCPCB DB link) into `~/.cache/nstott-tools/datasheets/<MPN>/`, extract the text and list the pages with the application circuit, component selection, recommended values and layout guidance; `--render` writes those pages as PNG. |
| `sch-set-field.py FILE REF FIELD VALUE [...]` | Set symbol fields (Value, LCSC, MPN, ...) by editing the file text: refuses while KiCad holds the `.lck`, timestamped backup in `.backups/`, atomic write, aborts if net connectivity would change. `--dry-run`, `--force`. Use instead of the MCP for field-only edits. |
| `bom-check.sh FILE` | Grouped BOM with LCSC/MPN; lists parts missing an LCSC number. |
| `proj-find.py TEXT [--root DIR] [--parts\|--labels]` | Find a part, value, LCSC/MPN, symbol or net label across sibling KiCad projects (default root: the parent of the project you are in). Answers "which project has the USB-C / SS14 / VBUS_SENSE". |
| `jlc_find.py` | Query the local JLCPCB parts DB (`--mfr`, `--lcsc`, `--passive`, `--sql`, `--basic`). |
| `jlc-load-db.sh` | Load the JLCPCB catalogue outside the MCP's 30 s timeout. |
| `pcb-quality.py PCB [--sch SCH] [--fab jlc-standard\|jlc-advanced] [--json OUT]` | Measure a routed board against layout-quality signals and print each deviation with part/net and coordinates: decoupling distance, switching loops, crystal keep-out, diff-pair match/vias, vias on clock nets, power-track widths, sub-standard vias/tracks, via-in-pad, acute corners, dangling ends, GND stitching, plane cuts, plane adjacency, fiducials/test points. Used by `kicad-pcb-review`. |
| `pcb-sync-from-sch.py SCH PCB [--place REF=X,Y[,ROT]] [--dry-run]` | File-level "Update PCB from Schematic": sets the value and the net of every pad from the `kicad-cli` netlist (names match KiCad's own), adds missing footprints from the project/KiCad libraries linked to their symbol, never deletes. Refuses while KiCad has the board open (`.lck`); backup in `.backups/`. Use instead of the MCP `sync_schematic_to_board`, which renames nets and drops pads. |
| `kicad-mcp-restart.sh [--dry-run]` | Restart this session's KiCad MCP python backend only. |
| `pdf_extract.py` + `venv-python` | Datasheet text and embedded images (land patterns). venv at `~/.cache/nstott-tools/venv`. |

## Relayout engine in 10 lines
```python
from kicad_sch.relayout import run
def layout(r):
    r.place('U1', 200, 125); r.place('C1', 190, 100)          # grid units, 1.27 mm
    r.seg(r.pin('U1', '5'), r.pin('C1', '1'))                  # wire (mm points from r.pin / r.at)
    r.pwr('GND', r.pin('C1', '2')); r.stub_label('U1', '7', 'SIG', 'r')
if __name__ == '__main__': run(layout)                         # spec.py SRC DST --expect nets.json
```
Regression test used when refactoring: the engine output for the RP2350 example is byte-identical to the
installed schematic apart from UUIDs.
