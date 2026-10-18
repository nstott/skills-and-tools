#!/usr/bin/env bash
# Safely replace a project's schematic: timestamped backup, atomic rename, then KiCad ERC.
#   sch-install.sh NEW.kicad_sch PROJECT_DIR
# Why: the KiCad MCP can truncate a schematic to 0 bytes when a tool call times out; a backup + atomic mv
# means a generated file can never leave the project half-written.
. "$(dirname "$0")/_env.sh"
new="$1"; proj="$2"; [ -f "$new" ] && [ -d "$proj" ] || die "usage: sch-install.sh NEW.kicad_sch PROJECT_DIR"
[ -s "$new" ] || die "$new is empty"
pro=$(ls "$proj"/*.kicad_pro 2>/dev/null | head -1); [ -n "$pro" ] || die "no .kicad_pro in $proj"
dst="${pro%.kicad_pro}.kicad_sch"
mkdir -p "$proj/.backups"
[ -f "$dst" ] && cp "$dst" "$proj/.backups/$(basename "$dst").$(date +%Y%m%d-%H%M%S)"
cp "$new" "$proj/.$(basename "$dst").tmp" && mv "$proj/.$(basename "$dst").tmp" "$dst"
echo "installed $dst ($(wc -c <"$dst") bytes); backup in $proj/.backups/"
need "$KICAD_CLI"
( cd "$proj" && "$KICAD_CLI" sch erc --severity-all -o /tmp/erc-$$.rpt "$(basename "$dst")" >/dev/null 2>&1 )
grep -E '^\[' /tmp/erc-$$.rpt | sort | uniq -c || true
echo "ERC: $(grep -c '^\[' /tmp/erc-$$.rpt) violation type(s); full report /tmp/erc-$$.rpt"
