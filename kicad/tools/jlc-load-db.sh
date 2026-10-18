#!/usr/bin/env bash
# Load the JLCPCB parts catalogue (~370k parts, ~250 MB) into the KiCad MCP's database.
# The MCP tool download_jlcpcb_database dies at its 30 s tool timeout, and the downloader's resume fails on the
# host's gzip encoding. Running the same downloader outside the tool, from a fresh (non-resumed) download, works.
# Run it in the background / with a long timeout, then restart the MCP server's python (kicad-mcp-restart.sh).
. "$(dirname "$0")/_env.sh"
need "$KICAD_PYTHON"; [ -d "$KICAD_MCP_DIR/python" ] || die "KICAD_MCP_DIR not found: $KICAD_MCP_DIR"
cache="$(dirname "$JLC_DB")/jlcparts_cache"
rm -rf "$cache"   # a partial file triggers the broken resume path
cd /tmp && PYTHONPATH="$KICAD_MCP_DIR/python" PYTHONUNBUFFERED=1 "$KICAD_PYTHON" -c "
from commands import jlcpcb_downloader as d
r = d.download_database(prefer_source='cdfer', progress=print)
print('RESULT', r)
raise SystemExit(0 if r.get('success') else 1)"
