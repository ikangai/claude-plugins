# pm — IKANGAI Signals Project Manager Assistant (Claude plugin)

Turns a Claude Code, Claude Desktop **or ChatGPT** session into an assistant for a
project manager on a running IKANGAI Signals instance. Four workflows over a
**bundled remote MCP connector** (`signals` → `https://signals.ikangai.com/mcp`)
— no CLI:

| Command | What it does |
|---|---|
| `/pm:project-status [code]` | Health, money, delivery, risks and overdue actions of your projects, ranked by attention |
| `/pm:month-end <code> [YYYY-MM]` | Closure preflight, draft (Tier 1), actual-cost entry and lock for PMO approval |
| `/pm:approvals [code]` | Submitted timesheets and expense claims on your projects — approve or reject each |
| `/pm:raid <code>` | Review and update risks, issues, actions and decisions |

All four also trigger from natural language ("what's waiting for my
approval?"). The shared `signals-operating` skill (a copy of pmo's — see
`plugins/pmo/README.md` → Tests) handles the connector tools, the preflight
and the write-safety policy.

## Prerequisites

- A running IKANGAI Signals instance with the remote MCP + OAuth surface.
- A **project-manager** account, authorized in the browser when the connector
  first asks. You see the projects you manage; others don't exist for you.

## Write safety

- **Tier 0**: reads — free.
- **Tier 1** (autonomous, `dryRun: true` first): creating a closure draft.
- **Tier 2** (confirmed per action): entering actual cost, locking,
  approving/rejecting timesheets and expenses, every RAID create/update,
  deletes.

Approving closures stays with the PMO (the pmo plugin).

## Install

Published in the public marketplace `ikangai/claude-plugins`. After installing,
connect the `signals` connector once (browser sign-in to IKANGAI Signals).

- **Claude Code:** `/plugin marketplace add ikangai/claude-plugins`, then
  `/plugin install pm@ikangai`. From a clone of this repo: `/plugin marketplace
  add .` then `/plugin install pm`; one session only: `claude --plugin-dir
  ./plugins/pm/`.
- **Claude Desktop:** Customize → Plugins → Add → Add marketplace →
  `ikangai/claude-plugins`, install **pm**, then open its Connectors tab and
  connect `signals`. Team/Enterprise: an Owner syncs the repo under
  Organization settings → Plugins & skills (and can set it Installed by
  default); each member still connects. Skills work in chat and Cowork.
- **ChatGPT desktop:** the plugin root also carries the portable
  `plugin.json` + `mcp.json` (agent-plugins.org 1.0.0). Workspace: an admin
  imports the repo under Admin → Plugins and publishes it to the roles that
  need it. Personal: clone `ikangai/claude-plugins`, copy `pm/` to
  `~/.codex/plugins/pm/`, list it in `~/.agents/plugins/marketplace.json`,
  restart the app, install from the Plugins Directory. ChatGPT runs OAuth on
  install.

## Tests

`node --test plugins/tests/*.test.mjs` — shared with pmo and employee.
