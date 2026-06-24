# Phase 1 — the work-division layer (tasks / assign / goal)

*2026-06-23. Implements Phase 1 of
`docs/plans/2026-06-22-coordination-gap-analysis.md` (the chat-room→coordinator gap).*

## Goal

Turn the chat *room* into a *coordinator*: durable, race-safe work-division on the bus
so an agent learns its slice from the chat instead of a human typing it into each
terminal — without disturbing the flat case. Holds the existing invariants:
*dormant-until-used*, *additive*, *fail-open*, *single-cursor*, *stdlib-only*, *old dbs
upgrade in place*.

## What landed

- **`tasks` table** (`id, ts, title, owner, status, paths, creator`) + `task
  add/list/claim/done`. `status` ∈ `open | claimed | done`.
- **Atomic claim** — `claim_task` is a status-guarded `UPDATE … WHERE id=? AND
  status='open'` + `rowcount`; of two racing claimers exactly one wins, the loser is
  told who holds it. This is the "two agents grab the same task" fix.
- **`assign <handle> "…"`** — `assign_task` creates a task *already owned* by the
  assignee (`status='claimed'`) **and** @mentions them, so an assignment is both
  durable (a ledger row) and delivered (rides their cursor / blocks their Stop), even
  before they join.
- **`goal` meta key** (`get_goal`/`set_goal`) — the one-line shared objective,
  auto-set by `bootstrap --goal` on a real launch, surfaced in every briefing + `who`.
- **Per-agent bootstrap prompts** — a spec is `name` or `name:prompt`; `_parse_spec`
  splits on the first colon before handle resolution, the resolved handle is mapped
  back to its prompt, and `spawn_agents(prompts={…})` deals each agent its own work.
- **Surfacing** (dormant-until-used) — `who` gains a `goal:` line + a `tasks:` tally
  (only when there is *live* work); the SessionStart briefing gains `Goal:` / `Your
  task(s):` / `Open tasks:`, all inside a fail-open `try` so a coordinator-surface
  error can never break the briefing.

## Method

TDD throughout: `tests/tasks_test.py` written RED first (22 checks failing because the
verbs didn't exist), then implemented to GREEN, then hardened with the review findings
below (61 checks total). Full suite via `tests/run_all.py` (now 25 modules).

## Adversarial review (fresh eyes) — outcome

A 5-lens / 27-agent review (concurrency·TOCTOU, invariants·fail-open, backward-compat,
adversarial inputs, test integrity), each finding independently verified against the
source before counting: **22 findings, 17 confirmed (1 blocker, 4 should-fix, 12
nits)**. Every confirmed should-fix-or-above was fixed and regression-tested; the fixes
clustered into two roots:

1. **BLOCKER — `assign` leaked its title text into routing.** The notification was
   `send(@h [assignment] #N: <raw title>)` as `kind='chat'`, so any `@token` in the
   title was a live mention and an `@human` *by the lead* opened a **phantom escalation
   that wedged the lead-done gate** (invariant #5); a *worker*'s `@human` got rewritten
   to `@<lead>` (mangled text + spurious ping); a third `@agent` in the title blocked
   that agent's Stop. **Fix:** `_quote_span()` wraps the title (and paths) in a markdown
   code span — `parse_mentions` / `_apply_human_guard` / `open_escalations` all ignore
   code spans, so the embedded tokens are inert, while the assignee `@h` (outside the
   span) still pings. The full, unquoted title stays verbatim in the ledger row and the
   briefing. Backticks in the title are flattened so they can't close the span early.
   Regression: `test_assign_title_does_not_route_or_escalate`,
   `test_assign_mention_blocks_stop_not_barrier`.

2. **should-fix — `complete_task` clobbered a concurrent claim (lost update).** It was
   a read-then-write (`row = read; owner = row.owner or h; UPDATE … WHERE id=?`); a
   `claim_task` committing in the window let the stale read overwrite the fresh owner —
   reintroducing "two agents each own the slice". The threaded regression reproduced it
   at **75/80** trials. **Fix:** a single atomic `UPDATE … SET owner=COALESCE(owner, ?)
   WHERE id=? AND status!='done'`, which never overwrites a committed owner → **0/80**.
   Regression: `test_complete_does_not_clobber_a_concurrent_claim`,
   `test_claim_is_atomic_under_threads`, `test_nonowner_completion_preserves_owner`.

Nits also fixed: `who` task tally went dormant on all-done (gate on live work, matching
the briefing); `doctor.py` schema-drift check gained the `tasks` table; an out-of-range
`task <id>` now degrades to "no task #…" instead of an `OverflowError` traceback; and
test gaps closed (`_parse_spec` edge cases, reserved-handle assign, real
`spawn_agents(method='print', prompts=…)` command wiring, dead code removed).

## Deferred (reported, out of Phase 1 scope)

- **Non-owner completion is silent** (nit [9]) — kept permissive by design (the bus is
  unauthenticated; a lead may tidy a crashed agent's slice), but a "@owner's task #N
  completed by @doer" notice could surface it. Documented via
  `test_nonowner_completion_preserves_owner`.
- **Dormancy tests assert substring-absence, not a byte-identical golden snapshot**
  (nit [15]) — adequate for now.
- Phases 2–5 of the gap analysis (result fan-in, control plane, spawn-depth guard,
  `focus`/file-claim ledger, mixed-fleet `done`) remain open.
