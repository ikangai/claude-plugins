---
name: expenses
description: File and submit your own expense claims in IKANGAI Signals. Use for "file an expense", "I paid … for the train to the customer", "submit my expenses", "why was my expense rejected" — lists your claims, records new ones as drafts, fixes rejected ones, and submits after you confirm. Argument is optional free text describing the expense.
argument-hint: [expense description]
---

# My expenses

Load the signals-operating skill first if not already loaded this
session. All tier rules come from it. A 404 `PRESET_DISABLED`
means this deployment has no expense claims — say so and stop.

## Step 1 — Your claims (Tier 0)

`signals_call {command: "expenses list", query: {view: "mine"}}` — your claims
with status (Draft/Submitted/Approved/Rejected), project, amount + currency,
and `canEdit`. Show Drafts and Rejected first, with rejection reasons
verbatim. Amounts stay in their own currency — never add up different
currencies.

## Step 2 — Record a claim (Tier 1, as a Draft)

Needs from the user: the project (code), the date (YYYY-MM-DD), the amount
(> 0, max 2 decimals), and a category — one of `travel`, `accommodation`,
`meals`, `transport`, `materials`, `software`, `other`. Optional currency
(defaults to the project's) and description. Ask for anything missing;
never guess an amount.

`signals_call {command: "expenses create", body: {projectId: "<code>",
expenseDate: "<date>", category: "<category>", amount: <amount>,
description: "<text>"}, dryRun: true}` → check → live. Do NOT send
`submit: true` — the draft is the Tier-1 action, submitting is separate.

A rejected or draft claim is changed with `signals_call {command: "expenses
update", args: ["<id>"], body: {<only the fields that change>}}` (dryRun
first). The project can't change — delete and re-file instead (Tier 2).

Receipts can't be attached over the connector: tell the user to add the
receipt in the app before submitting.

## Step 3 — Submit (Tier 2)

Per claim: show `signals_call {command: "expenses submit", args: ["<id>"],
dryRun: true}` with its project, date, amount and category, get explicit
confirmation, then run it live. Submitting notifies the project manager.

- 403 with a month-lock code = the expense month is already submitted/locked
  for that project.
- 409 `PROJECT_CLOSED` = the project is closed; claims can't be filed on it.

Wrap up with: drafts recorded (Tier 1), claims submitted, anything waiting
on a receipt.
