# kicad/tools

Scripts the skills call via `${CLAUDE_PLUGIN_ROOT}/tools/...`. Paths and tool locations come from `_env.sh`
(override with `KICAD_APP`, `KICAD_MCP_DIR`, `JLC_DB`).

| Tool | What it does |
|---|---|
| `kicad_sch/` | Python package. `relayout.py` layout engine (wires, power symbols, labels, verified + atomic write); `netlist.py` + `python3 -m kicad_sch nets / diff-nets`. Needs `PYTHONPATH=<this folder>`. Example spec: `kicad_sch/examples/rp2350_devboard_layout.py`. |
| `sch-install.sh NEW PROJECT_DIR` | Timestamped backup in `PROJECT_DIR/.backups`, atomic replace, then `kicad-cli` ERC. |
| `kicad-render.sh FILE OUTDIR [W] [--crop X Y W H]` | Schematic to PNG (+ crop) for visual checks. |
| `bom-check.sh FILE` | Grouped BOM with LCSC/MPN; lists parts missing an LCSC number. |
| `proj-find.py TEXT [--root DIR] [--parts\|--labels]` | Find a part, value, LCSC/MPN, symbol or net label across sibling KiCad projects (default root: the parent of the project you are in). Answers "which project has the USB-C / SS14 / VBUS_SENSE". |
| `jlc_find.py` | Query the local JLCPCB parts DB (`--mfr`, `--lcsc`, `--passive`, `--sql`, `--basic`). |
| `jlc-load-db.sh` | Load the JLCPCB catalogue outside the MCP's 30 s timeout. |
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
