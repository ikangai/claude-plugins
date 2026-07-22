---
name: chase
description: Compliance chasing for CompassAI — missing timesheets and overdue invoices. Use for "who hasn't submitted timesheets", "chase list", "overdue invoices", "AR follow-up". Produces a per-person / per-customer chase list with copy-ready nudge messages (v1 drafts only, does not send). Argument is the month as YYYY-MM (defaults to current).
argument-hint: [YYYY-MM]
---

# Compliance chase

Load the pmo:compass-operating skill first if not already loaded. MONTH from
$ARGUMENTS (YYYY-MM), default current month. FIRST/LAST = first and last day
of MONTH as YYYY-MM-DD. Reads are Tier 0; drafting messages is Tier 1 — text
only, since the connector has no notifications-send tool yet (delivery is
manual in v1).

## Step 1 — Timesheet compliance (fan out)

One pmo:pmo-collector with:
- `compass_call {command: "employees list"}` (active employees only; read
  `id`/`name` from the JSON)
- `compass_call {command: "timesheets list", query: {startDate: "<FIRST>",
  endDate: "<LAST>"}}` (read `employeeId`/`status`/`hoursWorked`/`workDate`)
Shape: `{employees: [{id, name}], entries: [{employeeId, status, hoursWorked}]}`.

Compute per employee: total hours in MONTH and status mix. Buckets:
- **Nothing submitted** — zero entries.
- **Draft-stuck** — entries exist but all still Draft (never submitted).
- **Thin** — submitted but under ~60% of a 160h working month. This heuristic
  ignores holidays, leave, and part-time — SAY SO in the output.

## Step 2 — Invoice compliance

Same collector or a second one:
- `compass_call {command: "invoices aging"}` — flat array of Invoiced-status
  rows, each with `dueDate`/`agingDays` already computed server-side (no
  bucketing — the collector returns the rows, MAIN buckets them by age).
- `compass_call {command: "invoices list", query: {status: "Invoiced"}}` —
  adds `invoiceNumber` (not present on aging rows) for citing in drafts; read
  `id`/`invoiceNumber`/`projectId`/`customerName`/`amount` from the JSON.
Overdue = past `dueDate` (from the aging rows); group by customer, worst
bucket first.

## Step 3 — Chase list + drafts (Tier 1: text only)

1. **Timesheets** — table: person → bucket → hours → their projects' PMs.
   Below it one short reusable nudge template, plus per-person one-liners
   (name, month, what's missing).
2. **Invoices (AR)** — table: customer → overdue total → oldest invoice →
   aging bucket. One firm-but-polite payment-reminder draft per customer
   citing the specific invoice numbers and amounts.

Close with: "v1 drafts only — delivery is manual (follow-up: a
notifications-send tool)." Never state or imply anything was sent.
