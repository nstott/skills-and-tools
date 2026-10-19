---
name: kicad-mcp-safety
description: Rules for driving the KiCad MCP server without losing work (30 s tool timeout can truncate a schematic to 0 bytes), plus how to load the JLCPCB database and restart the MCP backend. Use before any batch of schematic edits through mcp__kicad__* tools, or when JLCPCB tools report 0 parts.
---

# KiCad MCP safety

The KiCad MCP (`mixelpixx/KiCAD-MCP-Server`, swig backend) rewrites the **whole** `.kicad_sch` on every call. A call
that hits the 30 s tool timeout is killed mid-write and leaves the file at **0 bytes**. This destroyed a full
schematic once; project folders are usually not git repos, so nothing else holds a copy.

## Before editing
1. `cp` the schematic into a scratch/backup folder (or `git init` the project and commit). Do this after every
   successful call, **in a separate step** from the next MCP write (never in the same parallel block).
2. Keep calls small: about 5 components or 10 net labels per `batch_add_and_connect` / `batch_connect`. Place big
   symbols (20-pin headers, 60-pin MCUs) alone. Time per call grows with sheet size.
3. If a call times out: stop, `ls -la` the file, and restore from the backup if it is 0 bytes. Do not retry blindly.

## Prefer file-level generation for big changes
For layout, power symbols, wires, field changes across many parts, generate the file with
`${CLAUDE_PLUGIN_ROOT}/tools/kicad_sch` (see the `kicad-schematic-layout` skill) and install it with
`${CLAUDE_PLUGIN_ROOT}/tools/sch-install.sh NEW.kicad_sch PROJECT_DIR`: timestamped backup, atomic rename, ERC.

## Tool quirks
- Parameter names differ per tool; a validation error shows the right name (`open_project` wants `filename`,
  `search_footprints` wants `search_term`, `list_symbols_in_library` wants `libraryPath`).
- Pass pin **numbers**, not names, when a symbol has repeated names (RP2350A has six `IOVDD`, three `DVDD`).
- `register_symbol_library` writes `lib/sym-lib-table`, not the project root table. Add the entry to the root
  `sym-lib-table` / `fp-lib-table` yourself using `${KIPRJMOD}/lib/...`, and delete the stray file.
- `batch_connect` draws a **diagonal** wire when it sees a facing label nearby. A relayout removes them.
- `kicad-cli` is not on PATH. Use `. ${CLAUDE_PLUGIN_ROOT}/tools/_env.sh` and `kcli ...`, or the full path
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.
- `run_erc` through the MCP can report 0 while KiCad's own ERC finds problems (pins mid-wire); cross-check with
  `kicad-cli sch erc`.
- `list_schematic_nets` only shows nets named by labels, not nets named only by power symbols.

## JLCPCB parts database
`mcp__kicad__download_jlcpcb_database` times out at 30 s and kills the transfer; the downloader's resume also fails
on the host's gzip encoding. Use `${CLAUDE_PLUGIN_ROOT}/tools/jlc-load-db.sh` in the background (several minutes,
~250 MB, 370k parts). Then restart the backend so it reopens the replaced file:
`${CLAUDE_PLUGIN_ROOT}/tools/kicad-mcp-restart.sh` (kills only this session's python child; the node wrapper respawns
it). Verify with `mcp__kicad__get_jlcpcb_database_stats`. Query without the MCP via `tools/jlc_find.py`.
