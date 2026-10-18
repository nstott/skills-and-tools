# Shared environment for the kicad plugin tools. Source it: `. "$(dirname "$0")/_env.sh"`
KICAD_APP="${KICAD_APP:-/Applications/KiCad/KiCad.app}"
KICAD_CLI="${KICAD_CLI:-$KICAD_APP/Contents/MacOS/kicad-cli}"
KICAD_PYTHON="${KICAD_PYTHON:-$KICAD_APP/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3}"
KICAD_MCP_DIR="${KICAD_MCP_DIR:-$HOME/hackery/mixelpixx/KiCAD-MCP-Server}"
JLC_DB="${JLC_DB:-$HOME/Library/Application Support/kicad-mcp/jlcpcb_parts.db}"
TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}"
die() { echo "error: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || [ -x "$1" ] || die "missing tool: $1"; }
# kicad-cli with Fontconfig warning noise removed
kcli() { "$KICAD_CLI" "$@" 2> >(grep -v '^Fontconfig warning' >&2); }
