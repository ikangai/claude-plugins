---
name: timesheet
description: Book and submit your own timesheet in IKANGAI Signals. Use for "fill in my timesheet", "book my hours", "what's missing on my timesheet", "submit my week", "my timesheet was rejected" — shows the week per project and day, books the hours you state as Draft entries, fixes rejected entries, and submits after you confirm. Argument is any date in the week (YYYY-MM-DD) or "last"; defaults to the current week.
argument-hint: [YYYY-MM-DD | last]
---

# My timesheet

Load the signals-operating skill first if not already loaded this
session. All tier rules come from it.

**Week**: from $ARGUMENTS — a date (its Monday–Sunday week) or `last` (the
previous week); default = the current week. MON/SUN = the week's first and
last day as YYYY-MM-DD.

## Step 1 — Where you stand (Tier 0)

1. `signals_call {command: "me"}` → EMPLOYEE_ID. No `employeeId` = this login
   files no hours; say so and stop.
2. `signals_call {command: "timesheets my-obligations"}` — what is due or
   rejected right now (empty `items` = nothing outstanding). Show rejection
   reasons verbatim.
3. `signals_call {command: "assignments list", query: {employeeId: "<EMPLOYEE_ID>"}}`
   — the projects you are staffed on; keep those whose period overlaps the week.
4. `signals_call {command: "timesheets list", query: {employeeId:
   "<EMPLOYEE_ID>", startDate: "<MON>", endDate: "<SUN>"}}` — what is booked.

Show ONE grid: project × day, hours per cell, status per entry
(Draft/Submitted/Approved/Rejected), row and week totals. Flag empty working
days. Never fill a gap yourself.

## Step 2 — Book (Tier 1)

Ask for the hours per project and day if the user hasn't given them — the
hours are the user's statement, never your estimate. Then per entry:

- new: `signals_call {command: "timesheets create", body: {employeeId:
  <EMPLOYEE_ID>, projectId: <projectId>, workDate: "<YYYY-MM-DD>",
  hoursWorked: <h>, notes: "<optional>"}, dryRun: true}` → check → live.
- change a Draft or Rejected entry: `signals_call {command: "timesheets
  update", args: ["<id>"], body: {hoursWorked: <h>}}` — dryRun first.
  Submitted/Approved entries cannot be edited; say so.

A 403 on a create, edit or submit is usually a lock: the month or day is
already submitted/locked for the project, the project is closed, or you are
not billable on it. It may come as a code (`TIMESHEET_MONTH_LOCKED_EDIT`) or
as `FORBIDDEN` — quote the `error` sentence, don't retry, and don't call it
a role problem. The
new entry's id comes back as `timesheet_id` (lists use `timesheetId`).

## Step 3 — Submit (Tier 2)

List every Draft/Rejected entry of the week with its id, project, day and
hours, and the week total. Show `signals_call {command: "timesheets submit",
body: {ids: [<ids>]}, dryRun: true}` output, get explicit confirmation, then
run it live. Submitting sends the hours to your approver.

Wrap up with: entries booked or changed (Tier 1), whether the week was
submitted, and anything still missing.
