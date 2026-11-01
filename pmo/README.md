# pmo — IKANGAI Signals PMO Assistant (Claude plugin)

Turns a Claude Code, Claude Desktop **or ChatGPT** session into a PMO assistant for a
running IKANGAI Signals instance. Four workflows over a **bundled remote MCP
connector** (`signals` → `https://signals.ikangai.com/mcp`) — **no CLI, no
`npm link`**:

| Command | What it does |
|---|---|
| `/pmo:closures [YYYY-MM]` | Month-end sweep: bucket closures, collect blockers, approve/reject decision table; creates missing drafts (Tier 1) |
| `/pmo:briefing [filter]` | Portfolio health briefing ranked by attention-worthiness |
| `/pmo:capacity [year]` | Over-allocations, allocation cliffs, plan-vs-actual divergence |
| `/pmo:chase [YYYY-MM]` | Missing-timesheet + overdue-invoice chase list with copy-ready nudges |

All four also trigger ambiently from natural language ("how's month-end
looking?"). The shared `signals-operating` skill handles connector tools, the
auth/preset preflight, and the write-safety policy; the `pmo-collector` agent
keeps JSON-heavy sweeps out of the main context.

## How it connects (no CLI)

The plugin declares an MCP connector in `.mcp.json` (Claude) and `mcp.json`
(ChatGPT). Its three tools — `signals_describe`, `signals_call`,
`signals_api` — reach the IKANGAI Signals REST API. **Auth is OAuth 2.1, run by the
host:** the first call gets a `401 + WWW-Authenticate`, the app opens a browser
for login + consent, and caches/refreshes the token. You never handle a
password or token.

## Prerequisites

- **A running IKANGAI Signals instance** with the remote-MCP + OAuth surface deployed
  (`/mcp`, `/oauth/*`, `/.well-known/oauth-*`).
- **A PMO-credentialed account** — you authorize it in the browser when the
  connector prompts. Project managers and employees have their own plugins,
  `pm` and `employee` (`plugins/pm/`, `plugins/employee/`).

## Write safety

- **Tier 0**: reads — free.
- **Tier 1** (autonomous, `dryRun: true` first): `closures create` drafts;
  chase-text drafting; `projects invoices prefill`. (The pm and employee
  plugins add their own: a user's own Draft timesheet entries and Draft
  expense claims — the list lives in the shared `signals-operating` skill.) Every action is listed in the session summary.
- **Tier 2** (confirmed per action): closure approve/reject/lock, invoice status
  changes, timesheet approve/reject, deletes, anything with `forceReplace: true`.

v1 never sends notifications — chase messages are copy-ready text.

## Install

Published in the public marketplace `ikangai/claude-plugins`. After installing,
connect the `signals` connector once (browser sign-in to IKANGAI Signals).

- **Claude Code:** `/plugin marketplace add ikangai/claude-plugins`, then
  `/plugin install pmo@ikangai`. From a clone of this repo: `/plugin marketplace
  add .` then `/plugin install pmo`; one session only: `claude --plugin-dir
  ./plugins/pmo/`.
- **Claude Desktop:** Customize → Plugins → Add → Add marketplace →
  `ikangai/claude-plugins`, install **pmo**, then open its Connectors tab and
  connect `signals`. Team/Enterprise: an Owner syncs the repo under
  Organization settings → Plugins & skills (and can set it Installed by
  default); each member still connects. Skills work in chat and Cowork; the
  pmo collector agent only in Cowork (in chat the skills make the calls
  themselves).
- **ChatGPT desktop:** the plugin root also carries the portable
  `plugin.json` + `mcp.json` (agent-plugins.org 1.0.0). Workspace: an admin
  imports the repo under Admin → Plugins and publishes it to the roles that
  need it. Personal: clone `ikangai/claude-plugins`, copy `pmo/` to
  `~/.codex/plugins/pmo/`, list it in `~/.agents/plugins/marketplace.json`,
  restart the app, install from the Plugins Directory. ChatGPT runs OAuth on
  install.

## Tests

`claude plugin validate plugins/pmo` checks the plugin structure without
installing; `/plugin info pmo` shows what a session loaded.

    node --test plugins/tests/*.test.mjs

One suite for all three role plugins (`plugins/tests/plugins.mjs` lists them):

- `structure.test.mjs` — manifest + frontmatter validation for all
  skills/agents (incl. the collector's connector-tool scoping), the marketplace
  entry, and that `pm`/`employee` carry a byte-identical copy of this plugin's
  `skills/signals-operating/SKILL.md` (pmo holds the source — edit it here,
  then `cp` it over).
- `cli-drift.test.mjs` — every `signals_call {command}` referenced by a skill
  must exist in the IKANGAI Signals command manifest and run over MCP (imports
  `cli/lib/manifest/index.js` — the same manifest the MCP reuses; zero network).

## Smoke recipe (behavioral check)

1. Ensure the IKANGAI Signals instance the connector points at is reachable and has
   the remote-MCP + OAuth surface deployed.
2. Install the plugin; on first tool call, authorize the `signals` connector
   in the browser (a PMO account).
3. Run each of the four commands.
4. Expect: closures sweep buckets projects for the month; briefing ranks them
   and reports portfolio totals; capacity reports allocations; chase lists any
   employee without timesheets in the target month. No workflow writes anything
   except (Tier 1) closure drafts it announces. Under redacted AI-access mode,
   person/customer names read as `Customer_xxxx` (expected — key off codes).
