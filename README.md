# skills-and-tools

Personal Claude Code skills and tools, one plugin per domain.

## Layout

```
.claude-plugin/marketplace.json   # lists every domain plugin
_template/                        # copy to start a new domain
shared/                           # cross-domain helpers
<domain>/
  .claude-plugin/plugin.json
  skills/<skill-name>/SKILL.md    # instructions
  agents/*.md                     # optional subagents
  tools/                          # scripts / small MCP servers skills call
  .mcp.json                       # optional MCP servers the domain ships
```

Skills must be flat under `<domain>/skills/`; grouping happens at the plugin level.

## Domains

- `kicad` — circuit design

## Install

```
/plugin marketplace add ~/hackery/nstott/skills-and-tools
/plugin install kicad@nstott
```

## Dev loop (important)

Claude Code installs a marketplace plugin as a *copy* under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.
Editing this repo does not change what a session sees until the copy is refreshed:

- Day to day: `./dev-sync.sh` (rsyncs `kicad/` into the installed copy), then `/reload-plugins`.
- Release: bump `version` in `<domain>/.claude-plugin/plugin.json`, then `/plugin marketplace update nstott` and
  `/plugin update kicad@nstott`.

Skills reach their scripts through `${CLAUDE_PLUGIN_ROOT}/tools/...`.

## New domain

1. `cp -r _template <domain>`
2. Edit `<domain>/.claude-plugin/plugin.json`
3. Add an entry to `.claude-plugin/marketplace.json`
