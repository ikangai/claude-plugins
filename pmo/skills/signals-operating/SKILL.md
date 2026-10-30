---
name: signals-operating
description: Foundation for operating a IKANGAI Signals instance through the bundled remote MCP connector. Load FIRST from any pmo workflow skill (closures, briefing, capacity, chase) or whenever calling IKANGAI Signals — the connector tools, auth/preset preflight, error decoding, context-lean reads, the write-safety tier policy, and IKANGAI Signals domain conventions.
user-invocable: false
---

# Operating IKANGAI Signals via the remote MCP connector

This plugin bundles a remote MCP connector named **`signals`**
(`https://signals.ikangai.com/mcp`). No CLI, no `npm link` — installing the
plugin is enough.

## 1. The connector tools

The `signals` connector exposes three generic tools:

- **`signals_describe {filter?}`** — discover the command surface. Returns each
  matching command's name, HTTP method, path, positional `args`, `query` params,
  whether it `write`s, and notes. Call this FIRST to find the exact command name
  and its args before `signals_call`.
- **`signals_call {command, args?, query?, body?, dryRun?, forceReplace?}`** —
  run one command. `command` is the exact name (e.g. `"closures list"`); `args`
  are positional path params in order (e.g. `["PRJ-001"]`); `query` is
  query-string params (e.g. `{year: 2026}`); `body` is the JSON request body for
  writes. The primary tool.
- **`signals_api {method, path, query?, body?}`** — raw HTTP escape hatch for
  endpoints not wrapped as a command. Prefer `signals_call`.

## 2. Preflight (once per session)

1. `signals_call {command: "health"}` — API liveness + DB, no auth needed. On
   failure, report it and stop; nothing else will work.
2. **Auth is OAuth, handled by the host.** If a call returns an auth error
   (401/403 auth), the host prompts the user to connect (browser login +
   consent) and refreshes the token automatically. **Never ask for, or handle, a
   password or token** — you don't manage credentials. If the user isn't
   connected, tell them to authorize the `signals` connector when prompted.
3. `signals_call {command: "preset"}` — note enabled features. If a workflow
   section needs a disabled feature, skip that section and say which feature the
   deployment strips — never fail the whole workflow over it.
4. Role expectations: pmo workflows assume PMO credentials. A PM-only login sees
   only its own projects (the API hides others as 404s) and cannot
   approve/reject closures. Some portfolio endpoints are PMO-only outright (e.g.
   `closures matrix` → 403 for a PM-only login) — a 403 there means wrong role,
   not thin data. If results look implausibly thin, check
   `signals_call {command: "whoami"}` and warn the user.

## 3. Errors & the 404-vs-403 decoder

A failing tool call returns an **`isError` result** carrying the classified
`{error, code}` (the same shape the CLI prints). Decode:
auth error (401 = token expired → host re-prompts OAuth; 403 = role gate,
re-auth will NOT help) · not-found OR hidden by PM-scope OR feature disabled by
preset (body code `PRESET_DISABLED`) · 409 conflict — `code` is the API's own
(e.g. `CLOSURE_FINANCE_ACTUALS_MISSING`, `ROW_VERSION_CONFLICT`), with its detail
in `extra`; bare `CONFLICT` only when the API named none · rate-limited (back off;
never loop).

## 4. Keep reads context-lean

Constrain reads with `query` where the endpoint supports it (e.g.
`{limit: N, year: YYYY}`); otherwise read the returned JSON and extract only
what you need. Discover exact params with `signals_describe {filter: "closures"}`
— formats vary: `closures preflight` takes YYYY-MM; timesheet filters are
YYYY-MM-DD `startDate`/`endDate`; `closures list` wants integer `year`/`month`.

## 5. Write-safety tiers (policy for ALL pmo workflows)

- **Tier 0 — reads: free.** No confirmation.
- **Tier 1 — autonomous.** Additive, reversible drafts only. Currently exactly
  two things, each with its own procedure:
  (a) `signals_call {command: "closures create", body: {…}}` (creates an Open,
  editable snapshot) — call with `dryRun: true`, sanity-check the printed
  request, then call live (`dryRun` omitted);
  (b) drafting chase-message text — the draft in your reply IS the whole action;
  v1 has no send step, so never look for one.
  List every Tier-1 action taken in your final summary.
- **Tier 2 — confirm every action.** All governance transitions and destructive
  writes: `closures approve` / `closures reject` / `closures lock` /
  `closures unlock`, `invoices status`, `timesheets approve` / `timesheets
  reject`, every delete, anything requiring `forceReplace: true`. Procedure: show
  the exact `signals_call` AND its `dryRun: true` output, get explicit
  confirmation per action (a batch may be confirmed at once only after every
  target is listed), then execute live.
- Anything not named in Tier 1 is Tier 2 by default. Expanding Tier 1 is a
  design change, not a judgment call.

## 6. IKANGAI Signals domain conventions

- **Net sales** = total_selling − sales_discount. ALL margin/profit/EVM math
  uses net sales.
- **Closure lifecycle**: Open → Locked → Approved; reject returns Locked → Open
  with a reason (body field name exactly `rejectionReason`). **No lock without
  actual cost** (owner decision 2026-09-30, every caller): `closures lock`
  answers 409 `CLOSURE_FINANCE_ACTUALS_MISSING` until the month's AC is
  entered (`closures update` with the parts, e.g. `kpis.acManpower` — the
  total is derived while the stored total is empty or was their sum; parts
  that would replace a total entered apart from them are 409
  `CLOSURE_AC_TOTAL_CONFLICT` — send `acMonth` with them). 0 only when the month truly had no cost; never enter 0 to
  get past the gate. Ask whoever owns the number. One closure per
  project per month (409 on duplicate). Corrections after approval go through
  `closures new-version` (marks the old one Superseded).
- **The monthly closure is the month-end anchor**: readiness =
  `closures preflight`, portfolio state = `closures matrix`.
- **EVM is cost-based** (actual/budget ratio), not progress-based.
- **Presets are orthogonal to roles**: effective access = role AND preset.
- **Seeding is explicit (#2278)**: `projects invoices` is a pure read (it only
  reports `prefillAvailable`); `projects invoices prefill` WRITES planned rows
  from the cost sheet (Tier 1).

## 7. Redacted mode (PII)

The deployment's AI-access mode may be **redacted**: person/customer **names
come back pseudonymized** (e.g. `Customer_4f2a`), while codes, ids, money, and
dates are preserved intact. Key your logic off project codes and numbers, not
names, and **never treat a pseudonymized name as an error or missing data** —
it's the privacy layer working as intended.
