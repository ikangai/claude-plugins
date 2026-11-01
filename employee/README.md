# employee — IKANGAI Signals Employee Assistant (Claude plugin)

Turns a Claude Code **or Claude Desktop** session into a self-service
assistant on a running IKANGAI Signals instance. Four workflows over a
**bundled remote MCP connector** (`signals` → `https://signals.ikangai.com/mcp`)
— no CLI:

| Command | What it does |
|---|---|
| `/employee:timesheet [date\|last]` | Your week per project and day; books the hours you state, fixes rejected entries, submits |
| `/employee:leave [type and dates]` | Leave balances and history; files a leave request |
| `/employee:expenses [description]` | Your expense claims; records new ones as drafts, submits |
| `/employee:my-work` | Work items assigned to you plus timesheet obligations; moves a status |

All four also trigger from natural language ("book 8 hours on PRJ-104
yesterday"). The shared `signals-operating` skill (a copy of pmo's — see
`plugins/pmo/README.md` → Tests) handles the connector tools, the preflight
and the write-safety policy.

## Prerequisites

- A running IKANGAI Signals instance with the remote MCP + OAuth surface.
- Your own account, authorized in the browser when the connector first asks.
  Everything is about your own data.

## Write safety

- **Tier 0**: reads — free.
- **Tier 1** (autonomous, `dryRun: true` first): your own Draft timesheet
  entries and Draft expense claims, only with the hours/amounts you state.
- **Tier 2** (confirmed per action): submitting a timesheet or expense,
  requesting leave, changing a work item's status, deletes.

## Install

From a clone of this repo (Claude Code): `/plugin marketplace add .` then
`/plugin install employee`. One session only: `claude --plugin-dir
./plugins/employee/`. Public one-click install: `/plugin marketplace add
ikangai/claude-plugins` then `/plugin install employee@ikangai`.

## Tests

`node --test plugins/tests/*.test.mjs` — shared with pmo and pm.
