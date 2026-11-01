---
name: leave
description: Check your leave balance and request leave in IKANGAI Signals. Use for "how many vacation days do I have left", "request leave", "book holiday from … to …", "status of my leave request" — shows balances per leave type and your request history, and files a leave request after you confirm. Argument is optional free text like "vacation 2026-12-22 to 2026-12-31".
argument-hint: [type and dates]
---

# My leave

Load the signals-operating skill first if not already loaded this
session. All tier rules come from it. A 404 `PRESET_DISABLED`
means this deployment has no leave management — say so and stop.

## Step 1 — Balances and history (Tier 0)

1. `signals_call {command: "me"}` → EMPLOYEE_ID.
2. `signals_call {command: "leaves balances", args: ["<EMPLOYEE_ID>"]}` —
   per active leave type for the current year: `total`, `carriedOver`, `used`,
   `pending`, `available` (days), plus each type's `leaveTypeId`.
3. `signals_call {command: "leaves requests", args: ["<EMPLOYEE_ID>"]}` — your
   requests and their status.

Show a small balance table and any pending requests. Balances are for the
CURRENT calendar year only; for leave in another year say the balance shown
doesn't apply. `total` 0 can mean "no entitlement on record" or a real
0-day entitlement — the balance can't tell them apart.

## Step 2 — Request (Tier 2)

Needs a leave type (match the user's words to a `typeName` from the balances;
ask when it's ambiguous), a start and an end date (YYYY-MM-DD). Never pick
dates for the user.

Show `signals_call {command: "leaves create", body: {employeeId:
<EMPLOYEE_ID>, leaveTypeId: <id>, startDate: "<start>", endDate: "<end>"},
dryRun: true}` and, from the balance, whether the request fits into
`available`. Get explicit confirmation — the request goes straight to your
approver — then run it live.

- 409 `LEAVE_ENTITLEMENT_MISSING` = no entitlement on record for that
  type/year. Nothing was filed: over the connector this is a refusal, not a
  warning. The user can file it in the web app (where it only warns) or ask
  HR to record the entitlement.
- 400 = the request exceeds an entitlement that IS on record; report the
  numbers, don't retry.

Wrap up with: the request filed (or not), its status, and the remaining
balance for that type.
