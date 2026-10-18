#!/usr/bin/env bash
# Restart THIS Claude session's KiCad MCP python backend (e.g. after replacing jlcpcb_parts.db) without touching
# other sessions' servers: walk up from this shell to the owning `claude`, find its MCP node child, kill its python
# child; the node wrapper respawns it.   kicad-mcp-restart.sh [--dry-run]
pid=$$; owner=""
while [ "$pid" -gt 1 ]; do
  cmd=$(ps -o command= -p "$pid" 2>/dev/null)
  case "$(basename "${cmd%% *}")" in claude) owner=$pid; break ;; esac
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
done
[ -n "$owner" ] || { echo "error: no claude ancestor found" >&2; exit 1; }
node=$(pgrep -P "$owner" -f 'KiCAD-MCP-Server/dist/index.js' | head -1)
[ -n "$node" ] || { echo "error: no KiCad MCP node under claude pid $owner" >&2; exit 1; }
py=$(pgrep -P "$node" -f kicad_interface.py | head -1)
[ -n "$py" ] || { echo "error: MCP node $node has no python child" >&2; exit 1; }
echo "claude=$owner mcp-node=$node python=$py"
if [ "$1" = "--dry-run" ]; then echo "(dry run, not killing)"; exit 0; fi
kill "$py" && sleep 3 && echo "new python: $(pgrep -P "$node" -f kicad_interface.py | head -1)"
