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

## New domain

1. `cp -r _template <domain>`
2. Edit `<domain>/.claude-plugin/plugin.json`
3. Add an entry to `.claude-plugin/marketplace.json`
