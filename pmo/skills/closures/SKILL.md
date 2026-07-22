---
name: closures
description: Month-end closure driver for CompassAI. Use for "run month-end", "closure status", "what's ready to approve", "sweep closures" — sweeps the portfolio for a month, buckets projects (missing/open/locked/approved), collects blockers, presents an approve/reject decision table, and can create missing closure drafts. Argument is the month as YYYY-MM (defaults to the current month).
argument-hint: [YYYY-MM]
---

# Month-end closure driver

Load the pmo:compass-operating skill first if not already loaded this session
(Skill tool). All tier rules come from it.

**Month**: parse from $ARGUMENTS (YYYY-MM); default = current month
(`date +%Y-%m`). Derive YEAR, and MONTH_START = YYYY-MM-01.

## Step 1 — Portfolio sweep (Tier 0)

1. `compass_call {command: "closures matrix", query: {year: YEAR}}` — closure
   status per project × month (deliberately portfolio-wide).
2. `compass_call {command: "projects list", query: {year: YEAR}}` — the
   denominator.

Bucket every project that is not Closed/Cancelled, for the target month:
- **approved** — closure Approved (count only, no action)
- **locked** — awaiting PMO decision
- **open** — draft exists, in progress
- **missing** — no closure for the month

## Step 2 — Collect detail (Tier 0, fan out)

Spawn one pmo:pmo-collector agent per project in locked/open/missing — in
parallel; if more than ~8, batch several projects per collector, using the
project's `projectId` (from the Step 1 matrix rows; the list's `project_id`
is the same value) as `<projectId>`. Give each the compass_call invocations
plus:
- missing/open: `compass_call {command: "closures preflight", args:
  ["<projectId>", "<YYYY-MM>"]}` → blockers, warnings
- open/locked (pmcId comes from the matrix): `compass_call {command:
  "closures get", args: ["<pmcId>"]}` → read `status`/`notes`/`isFinal`/`kpis`
  from the returned JSON (raw KPI numbers + notes)
Ask for the shape
`{projectId, projectCode, status, blockers: [], warnings: [], kpis: {}, notes: null, error: null}`
— have the collector pass the `kpis` object through verbatim (it is raw
numbers, not a judgment); YOU flag anomalies in Step 3, the collector does not.

`notes` may be either plain text or a JSON object of structured sections
(`{delivery, financial, resources, escalations, general}` — the web UI's
structured commentary, #194). When it parses as such an object, render it in
Step 3 as `Delivery: … · Financial: …` lines instead of pasting the raw JSON.

## Step 3 — Decision table

Present ONE table, most actionable first:
- **Ready to approve** — locked, no blockers, and no KPI anomaly in the raw
  `kpis` you flag here — treat cpi < 1 (over budget) or spi < 1 (behind
  schedule) as anomalies; say why clean, citing the numbers
- **Investigate before deciding** — locked but has preflight warnings or a
  flagged KPI anomaly (list the specific numbers)
- **In progress** — open, each project's blockers and its PM
- **Missing** — no closure yet, with anything preflight says would block it
- One line: "N already approved."
Name collectors that errored; never silently drop a project.

## Step 4 — Actions

- **Tier 1 (autonomous):** for missing projects whose preflight is clean,
  create the draft: `compass_call {command: "closures create", body:
  {projectId: <projectId>, monthStart: "<MONTH_START>"}, dryRun: true}` then
  live (omit `dryRun`). A 409 means it already exists — re-bucket, not an
  error. Do NOT create drafts where preflight shows blockers — surface those
  instead. List every draft created in the summary.
- **Tier 2 (per-action confirmation, only when the user asks):**
  - approve: show `compass_call {command: "closures approve", args:
    ["<pmcId>"], dryRun: true}` output → confirm → run live (omit `dryRun`).
  - reject: requires a reason — `compass_call {command: "closures reject",
    args: ["<pmcId>"], body: {rejectionReason: "<reason>"}}` (exact field
    name; the route 400s otherwise). Dry-run first → confirm → live.

Wrap up with: bucket counts, Tier-1 actions taken, Tier-2 actions
executed/declined, and what needs a human next.
