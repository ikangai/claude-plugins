---
name: month-end
description: Get your project's monthly closure ready and lock it in IKANGAI Signals. Use for "month-end for PRJ-…", "close the month", "what blocks my closure", "enter actual cost", "lock my closure" — runs the preflight, creates the closure draft, enters the actual cost you state, and locks it for PMO approval after you confirm. Arguments are a project code and the month as YYYY-MM (defaults to the last ended month).
argument-hint: <project code> [YYYY-MM]
---

# Month-end for my project

Load the pm:signals-operating skill first if not already loaded this session
(Skill tool). All tier rules come from it — especially the closure
lifecycle and the "no lock without actual cost" rule in §6.

**Inputs**: project code and MONTH (YYYY-MM) from $ARGUMENTS; month default
= the last ended month. No code and more than one project → list your
projects (`projects list`) and ask. MONTH_START = MONTH-01. Approving is
the PMO's step, not yours.

## Step 1 — Readiness (Tier 0)

1. `signals_call {command: "closures preflight", args: ["<code>", "<MONTH>"]}`
   → blockers and warnings.
2. `signals_call {command: "closures list", query: {projectId: "<code>",
   year: <YYYY>, month: <M>}}` → the closure for that month, if any
   (`pmcId`, status).

Show the status (missing / Open / Locked / Approved) and every blocker in
plain words. `finance_ac_missing` = the month's actual cost (AC) is not
entered; nobody can lock until it is.

## Step 2 — Draft (Tier 1)

No closure yet: `signals_call {command: "closures create", body: {projectId:
"<code>", monthStart: "<MONTH_START>"}, dryRun: true}` → live. A 409 means it
exists — re-read, not an error.

## Step 3 — Actual cost (Tier 2)

Ask the user for the month's actual cost — manpower and subcontracting, as
their finance figures say. Never derive or estimate it, and never enter 0
to get past the gate (0 only when the month truly had no cost). Show
`signals_call {command: "closures update", args: ["<pmcId>"], body: {kpis:
{acManpower: <n>, acSubcontracting: <n>}}, dryRun: true}`, confirm, run live
(the total is derived from the parts). 409 `CLOSURE_AC_TOTAL_CONFLICT` = a
total was entered separately — ask which figure is right and send `acMonth`
with the parts. Commentary goes in `noteSections` ({delivery, financial,
resources, escalations, general} — only the sections you change).

## Step 4 — Reconcile and confirm (Tier 2)

Over the connector the lock also needs what the web app's "Reconcile &
Confirm" card collects (agent-strict, see the operating skill §3):

1. `signals_call {command: "closures get", args: ["<pmcId>"]}` — compare,
   entered vs collected:
   - employee cost: `kpis.acManpower` vs `acReference.sources.timesheet`
   - third-party cost: `kpis.acSubcontracting` vs
     `acReference.sources.supplierInvoices`
   Show each variance. Don't use `acReference.value`/`delta`: they compare
   the AC total incl. items, which the lock doesn't judge, and don't compare
   `rrFinance` with the cumulative `rrSystem`. A null collected figure means
   "not measurable", not 0. Recognised revenue is a third row the lock may
   name (`recognizedRevenue` in `extra.missingExplanations`); explain it only
   when it does.
2. A variance is MATERIAL when it is at least 10% of the collected figure
   AND at least 100 absolute. Each material one needs the user's
   explanation; ask, never write one yourself.
3. Ask the user to confirm the three checks one by one: source data
   complete, finance figures entered, differences reviewed.
4. Then `signals_call {command: "closures update", args: ["<pmcId>"], body:
   {checklist: {sourceData: true, financeFigures: true, differencesReviewed:
   true}, varianceNotes: {employeeCost?, thirdPartyCost?,
   recognizedRevenue?}}, dryRun: true}` → confirm → live. Set a check to
   true ONLY when the user confirmed it.

## Step 5 — Lock (Tier 2)

Re-run the preflight. With no blockers left: show `signals_call
{command: "closures lock", args: ["<pmcId>"], dryRun: true}`, confirm, run
live. Refusals you may get:
- 409 `CLOSURE_RECONCILIATION_INCOMPLETE` — `extra.missingChecklist` /
  `extra.missingExplanations` name what Step 4 still lacks. The lock
  re-books approved labour at current rates first, so it can name a row
  your Step 4 comparison didn't: re-read `closures get` and ask for that
  explanation.
- 409 `CLOSURE_CHAIN_GAP` — earlier months have no closure at all
  (`extra.months`). A draft for each is enough (Step 2 per month); they
  don't have to be locked.
- 409 `ACCOUNTING_PERIOD_CLOSED` — Finance closed the period; only the PMO
  can reopen it.
- 409 `CLOSURE_FINANCE_ACTUALS_MISSING` — back to Step 3.
- 422 `CLOSURE_MONTH_NOT_ENDED` — the month hasn't ended. An early close
  needs `{earlyClose: true, earlyCloseReason: "…"}` and the user's explicit
  say-so.
- 500 `CLOSURE_SNAPSHOT_FAILED` — nothing was locked; report it.
- Any other 409 (e.g. not Open any more) — quote `error`.

Locked = waiting for the PMO; a rejection comes back as Open with a reason.

Wrap up with: closure status, AC entered, locked or not, and what the PMO
or finance still has to do.
