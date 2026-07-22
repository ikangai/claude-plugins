---
name: capacity
description: Resource capacity and over-allocation check for CompassAI. Use for "who's over-allocated", "capacity check", "staffing gaps", "allocation vs actuals" — analyzes the planned-vs-actual allocation matrix for over-allocations, allocation cliffs, and plan/actual divergence. Argument is the year (defaults to current).
argument-hint: [year]
---

# Capacity & over-allocation check

Load the pmo:compass-operating skill first if not already loaded. YEAR from
$ARGUMENTS, default current year. Tier 0 only — never writes.

## Step 1 — The matrix

`compass_call {command: "capacity", query: {year: YEAR}}` — one server-side
aggregated call (project × employee monthly planned % vs actual %). Usually
no fan-out needed; if the response is huge, re-pull filtered (`query:
{customerId: …}` or `{projectId: …}`) — otherwise read the returned JSON and
extract what you need.
Note: a PM-only login sees only its own projects — if the matrix looks thin,
check `compass_call {command: "whoami"}` and warn.

## Step 2 — Analyze (in context)

- **Over-allocation**: months where an employee's summed planned allocation
  exceeds 100% — employee, months, contributing projects with their %.
- **Cliffs**: employee at ≥80% in the current or next month and <20% two
  months later while their projects are still Active — likely an unstaffed
  follow-on or a missing plan.
- **Plan-vs-actual divergence**: actuals present and |actual − planned| ≥ 25
  percentage points for 2+ months — plan rot; name the projects.

Optional drill-down for flagged employees only — one pmo:pmo-collector with
`compass_call {command: "timesheets list", query: {employeeId: "<id>",
startDate: "YEAR-01-01", endDate: "YEAR-12-31"}}` (read
`projectId`/`workDate`/`hoursWorked`/`status` from the returned rows).

## Step 3 — Report

A table per finding class: employee → months → projects → suggested action
(rebalance / staff the follow-on / fix the plan). End with the clean count
("N of M resources have no findings") so an empty finding class is visibly
checked, not silently skipped.
