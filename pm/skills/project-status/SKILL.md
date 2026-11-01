---
name: project-status
description: Status of the projects you manage in IKANGAI Signals. Use for "how are my projects", "status of PRJ-…", "project health", "what needs my attention", "prepare my status report" — reads health, money, delivery, risks and overdue actions per project and ranks what needs attention. Argument is an optional project code; without it, all your projects.
argument-hint: [project code]
---

# My projects' status

Load the pm:signals-operating skill first if not already loaded this session
(Skill tool). All tier rules come from it. This workflow is Tier 0 only — it
never writes.

## Step 1 — Scope

`signals_call {command: "projects list"}` — as a project manager you see the
projects you manage. Keep the one named in $ARGUMENTS (match `projectCode`),
else every project that is not Closed/Cancelled. None at all → check `me`
and say the workflow expects a project-manager login.

## Step 2 — Per project (Tier 0)

1. `signals_call {command: "projects status-report", args: ["<code>"]}` —
   defaults to the last ended month; sections identity, summary, health,
   delivery, money, progress, exceptions, decisions. A section with status
   `unavailable` carries its reason — show it, never drop it.
2. `signals_call {command: "projects risks", args: ["<code>"]}` — ALL risks,
   severity-ordered, closed ones included: keep the open ones by `status`.
3. `signals_call {command: "projects actions", args: ["<code>"], query:
   {overdue: true}}` — overdue actions (overdue is derived; there is no
   "Overdue" status).

With more than ~5 projects, run step 1 only and offer the detail per
project on request — context stays lean.

## Step 3 — Report

One row per project, most attention-worthy first: health, the money line
(as the report states it — invoiced vs recognised revenue are different
figures; quote the basis), delivery progress, top risk, overdue action
count. Below it, per project needing attention: what, why (cite the
numbers), and the next step — pointing to the pm:raid or pm:month-end skill
where it fits. Null means "not measured", never 0.
