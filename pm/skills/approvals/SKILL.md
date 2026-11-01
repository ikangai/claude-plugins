---
name: approvals
description: Approve or reject your team's timesheets and expense claims in IKANGAI Signals. Use for "what's waiting for my approval", "approve timesheets", "review expenses", "reject this timesheet" — lists submitted timesheets and expense claims on your projects, and approves or rejects each after you confirm. Argument is an optional project code.
argument-hint: [project code]
---

# My approvals

Load the signals-operating skill first if not already loaded this session. All tier rules come from it: every approve and reject is
Tier 2.

## Step 1 — What's waiting (Tier 0)

0. `signals_call {command: "me"}` → your EMPLOYEE_ID.
1. `signals_call {command: "timesheets list", query: {status: "Submitted"}}`
   (add `projectId` when $ARGUMENTS names a project) — submitted entries you
   may decide. The list also holds YOUR OWN submitted entries — drop every
   row whose `employeeId` is yours (you can't approve them, and one in a
   batch fails the whole batch). Group by person and week: project, hours
   per day, total, notes.
2. `signals_call {command: "expenses list", query: {view: "approve"}}` —
   submitted claims you may decide (`canDecide`). Per claim: person, project,
   date, category, amount + currency (never summed across currencies).

Flag what deserves a look before approving: a day over ~10 h, a week far
above or below the person's usual, an expense without description. Say what
you flagged and why; the decision is the user's.

Leave requests are decided by line managers — not part of this workflow;
point to the app if the user asks.

## Step 2 — Decide (Tier 2, per batch the user names)

- Timesheets approve: list the entry ids, person and hours, show
  `signals_call {command: "timesheets approve", body: {ids: [<ids>]},
  dryRun: true}`, confirm, run live. All-or-nothing: one entry you may not
  decide, or one in a locked month, fails the whole batch with a 403 —
  quote its `error` (both arrive as `FORBIDDEN`) and retry without it. Entries no longer Submitted are
  skipped silently — compare the approved count with the ids you sent.
- Timesheets reject: needs a reason the user gives — `signals_call
  {command: "timesheets reject", body: {ids: [<ids>], reason: "<reason>"}}`,
  dryRun → confirm → live.
- Expense approve: first `signals_call {command: "expenses get", args:
  ["<id>"]}` for its `rowVersion`, then `signals_call {command: "expenses
  approve", args: ["<id>"], body: {expectedVersion: <rowVersion>}}` — dryRun
  → confirm → live. 409 `ROW_VERSION_CONFLICT` = the claim changed since you
  read it (`current` holds the new version); show it again. 403
  `EXPENSE_SELF_APPROVAL` = your own claim; someone else decides it.
- Expense reject: `signals_call {command: "expenses reject", args: ["<id>"],
  body: {reason: "<reason>", expectedVersion: <rowVersion>}}` — dryRun →
  confirm → live.

Wrap up with: approved and rejected per person, and what is still waiting.
