---
name: my-work
description: What's on your plate in IKANGAI Signals. Use for "what should I work on", "my tasks", "my work items", "what's due", "mark … as done" — lists the work items assigned to you across projects plus outstanding timesheet obligations, and moves a work item's status after you confirm.
---

# My work

Load the signals-operating skill first if not already loaded this
session. All tier rules come from it.

## Step 1 — The list (Tier 0)

1. `signals_call {command: "work-items mine"}` — work items assigned to you,
   across all projects.
2. `signals_call {command: "timesheets my-obligations"}` — a timesheet due or
   rejected counts as work too (empty `items` = nothing outstanding).

Present one list, most urgent first: Blocked, then overdue (`dueDate` in the
past), then due this week, then the rest; Done only as a count (Cancelled items
are not returned). Per item: project, title, status, priority, due date. Timesheet
obligations go on top with a pointer to the timesheet skill.

Items with a 🔗 / `syncSource` are mirrored from an external tracker — their
title, status and due date are owned there. Say so instead of offering a
status change (the API answers 409 `SYNCED_STATUS_EXTERNAL`).

## Step 2 — Move a status (Tier 2)

Statuses: `Open`, `In Progress`, `Blocked`, `Done`. Cancelling is for the
project manager. Show `signals_call {command: "work-items status", args:
["<id>"], body: {status: "<status>"}, dryRun: true}`, confirm, run live. A
status change is not a timesheet — it books no hours.

Wrap up with: what's most urgent, and any status changed.
