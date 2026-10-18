#!/usr/bin/env bash
# Dev loop: copy <domain>/ from this repo into the INSTALLED plugin copy so edits are live after /reload-plugins.
# Claude Code installs marketplace plugins as a copy in ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/ and
# does not re-read the repo until `/plugin marketplace update <m>` + `/plugin update <plugin>@<m>` (which needs a
# version bump in <domain>/.claude-plugin/plugin.json). This script skips that for day-to-day iteration.
#   ./dev-sync.sh [domain=kicad] [--dry-run]
set -e
here="$(cd "$(dirname "$0")" && pwd)"
domain="${1:-kicad}"; [ "$domain" = "--dry-run" ] && domain=kicad
flag=""; for a in "$@"; do [ "$a" = "--dry-run" ] && flag="-n"; done
market="$(python3 -c "import json;print(json.load(open('$here/.claude-plugin/marketplace.json'))['name'])")"
dest="$(python3 - "$domain@$market" <<'PY'
import json, os, sys
d = json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))
e = d['plugins'].get(sys.argv[1])
print(e[0]['installPath'] if e else '')
PY
)"
[ -n "$dest" ] || { echo "$domain@$market is not installed. Run: /plugin install $domain@$market" >&2; exit 1; }
rsync -a $flag --delete --exclude '__pycache__' --exclude '.DS_Store' "$here/$domain/" "$dest/"
echo "synced $here/$domain/ -> $dest/ ${flag:+(dry run)}"
echo "Now run /reload-plugins in Claude Code."
