---
name: briefing
description: Portfolio health briefing for IKANGAI Signals. Use for "how's the portfolio", "status report", "portfolio briefing", "which projects need attention" — collects financials, YTD variance, risks, AR aging, capacity and closure state across projects, ranks by attention-worthiness, and reports. Optional argument filters by customer name or project code.
argument-hint: [customer-or-project filter]
---

# Portfolio health briefing

Load the signals-operating skill first if not already loaded. YEAR =
current year. This workflow is Tier 0 only — it never writes. In particular,
never call `projects invoices prefill` here (it seeds rows); `projects invoices`
and `invoices list` are pure reads.

## Step 1 — Scope (Tier 0)

`signals_call {command: "projects list", query: {year: YEAR}}`
If $ARGUMENTS gives a filter, keep matching projects (project code or customer
substring, case-insensitive). The default list already excludes inactive
(Closed/Cancelled) projects. Note the count.

## Step 2 — Collect (fan out)

Portfolio-level — ONE pmo-collector subagent with:
- `signals_call {command: "invoices aging"}`
- `signals_call {command: "capacity", query: {year: YEAR}}`
- `signals_call {command: "closures matrix", query: {year: YEAR}}`
`invoices aging` returns a flat array of invoice rows (status=Invoiced only,
no server-side bucketing) — the collector returns the raw rows (selected
keys), MAIN does the bucketing/aggregation. Shape: `{agingRows:
[{customerName, projectCode, amount, currency, invoiceDate, dueDate,
agingDays}], overAllocated: [{employee, months}], closuresMissing:
[projectCode]}`.

Per project — one collector per project, in parallel (batch if more than ~8),
using `projectId` from Step 1 as `<projectId>`:
- `signals_call {command: "projects financials", args: ["<projectId>"]}`
- `signals_call {command: "projects risks", args: ["<projectId>"]}`
Shape: `{projectId, projectCode, financials: {planned: {revenue, marginPct},
actualCost: {totalCost}, variance: {costVariance, actualMarginPct}, progress:
{budgetConsumedPct}}, forecast: {basis, varianceMargin}, topRisks:
[{title, severity}], error: null}` — `financials.*` mirrors `projects
financials`'s `planned`/`actualCost`/`variance`/`progress` sub-objects
verbatim (actual cost = cost incurred: booked supplier invoices + approved
labour);
`forecast` is its `forecast` sub-object — THE forecast of record: `basis`
(closure | plan | none — only `closure` is a forecast) and `varianceMargin`
(margin at completion minus plan; negative = erosion, null off `closure`).

## Step 3 — Rank & report

Score each project's attention-need from: negative margin variance vs
plan (`forecast.varianceMargin` negative while `forecast.basis` is `closure`); cost overrun — early warning
when `financials.variance.costVariance` > 0 (actual cost ahead of the
pro-rata plan), blown budget when `financials.progress.budgetConsumedPct`
exceeds 100%; high-severity risks; overdue AR share; involvement in
over-allocation; missing closure.

Report as terminal prose + one compact table:
1. Headline: portfolio totals (net sales, margin, overdue AR).
2. **Needs attention** — top projects, one short paragraph each with the WHY
   in specific numbers.
3. **Healthy** — one line each.
4. Portfolio hygiene: missing closures, over-allocations, AR aging tail.
5. Collectors that errored — project + error, never silently dropped.

Remember margin math uses NET sales. Only build an HTML artifact if the user
asks for something shareable.
