---
name: raid
description: Keep your project's risks, issues, actions and decisions current in IKANGAI Signals. Use for "add a risk", "update the risk log", "log an issue", "record a decision", "overdue actions on PRJ-…", "RAID review" — reads the four registers and creates or updates entries after you confirm. Argument is a project code.
argument-hint: <project code>
---

# RAID log

Load the pm:signals-operating skill first if not already loaded this session
(Skill tool). All tier rules come from it: every create, update and delete
here is Tier 2.

**Project**: from $ARGUMENTS; none and more than one project → list yours
(`projects list`) and ask.

## Step 1 — Review (Tier 0)

1. `signals_call {command: "projects risks", args: ["<code>"]}`
2. `signals_call {command: "issues list", query: {projectId: "<code>"}}`
3. `signals_call {command: "projects actions", args: ["<code>"]}` —
   `isOverdue` is derived from `dueDate`; there is no "Overdue" status.
4. `signals_call {command: "projects decisions", args: ["<code>"]}`

Show four short tables (open entries first; closed only as a count) and
call out: high risks without mitigation or owner, overdue actions, issues
without owner or due date, decisions still waiting on a body
(`decisionRequiredBy`).

## Step 2 — Change (Tier 2)

Take the content from the user — never invent a risk, an owner or a date.
For each change show the call with `dryRun: true`, confirm, run live.
Updates send only the fields that change.

- Risk: `signals_call {command: "projects risks create", args: ["<code>"],
  body: {title, severity?, probabilityPct?, mitigation?, ownerEmployeeId?}}`;
  update with `{command: "projects risks update", args: ["<code>", "<riskId>"], body: {…}}`.
- Issue: `signals_call {command: "issues create", body: {projectId: "<code>",
  title, priority?, ownerEmployeeId?, dueDate?}}`; update with
  `{command: "issues update", args: ["<id>"], body: {…}}`.
- Action: `signals_call {command: "projects actions create", args:
  ["<code>"], body: {title, ownerEmployeeId?, dueDate?, priority?}}`;
  statuses Open | In Progress | Done | Cancelled; update with
  `{command: "projects actions update", args: ["<code>", "<actionId>"], body: {…}}`.
- Decision: `signals_call {command: "projects decisions create", args:
  ["<code>"], body: {title, decisionDate: "<YYYY-MM-DD>", description?,
  decidedByName?}}`; update with `{command: "projects decisions update",
  args: ["<code>", "<decisionId>"], body: {…}}`.

Owners are employee ids: look the person up (`signals_call {command:
"employees list"}`) and confirm the match with the user. Delete only on an
explicit request.

Wrap up with: what changed per register, and what still needs an owner or
a date.
