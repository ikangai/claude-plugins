# Phase 5 — correctness & mixed-fleet

*2026-06-23. Implements Phase 5 of
`docs/plans/2026-06-22-coordination-gap-analysis.md` — the last roadmap phase: two
correctness bugs + the mixed-fleet barrier gap + decision-rule docs.*

## Goal

Close the two correctness defects the analysis flagged and the mixed-fleet barrier
hole, and document when a groupchat session is the wrong tool. Same invariants:
*dormant-until-used*, *additive*, *fail-open*, *single-cursor*, *stdlib-only*, *old dbs
upgrade in place*.

## What landed

### 1. Escalation-orphan on rename / handoff (FIXED)

The lead-done gate keyed @human escalations on the lead's **handle**, frozen in the
message's `sender` at author time. So when a lead **renamed** (`ada`→`chief`) or
**handed off** the lead, its open question went invisible to the gate — the team could
tear down with the operator's answer still owed, and `answer` @mentioned a dead handle.

- **`session_open_escalations(conn, session_id)`** — the gate, keyed by author SESSION
  (stable across a rename) and gating the *asker* (not the current lead, so a handoff
  doesn't orphan it). `stop.py` now uses this. Only a lead ever authors an unquoted
  @human (a worker's is redirected), so semantics are unchanged for the happy path.
- **`all_open_escalations(conn)`** — `questions` is now room-wide, so a handed-off
  question stays visible to the operator (not just the current lead's).
- **`answer`** resolves the asker's *current* handle via the frozen author session, so
  the reply lands after a rename; the clear matches the current handle.

### 2. Mixed-fleet `done` (FIXED)

A non-hook host (opencode/generic) has no Stop hook, so it never marks `done` and held a
Claude/Codex team at the barrier until it aged out (15 min). **Fix:** a `parks` column
(default 1). `team_done` requires only **barrier-capable** (`parks=1`) active agents to
be done; a `--no-barrier` agent (registered so by the opencode plugin and the generic
`AGENTS.md`) counts toward assembly but never gates the all-done check. An all-hook team
is byte-identically unaffected.

### 3. Decision-rule docs

`SKILL.md` + `team.md` now say when to spawn a session vs use the native Agent/Workflow
tool: native for fan-out-then-join that returns a structured result within the turn; a
session only when the worker must outlive the turn, be human-watchable, take a worktree,
or stay `@mention`-able.

## Method

TDD: `tests/correctness_test.py` written RED first, implemented to GREEN, then hardened
with the review findings below. The old `phase2` test that *pinned the handoff-orphan
as a documented limitation* was rewritten to assert the fix. Full suite via
`tests/run_all.py` (29 modules).

## Adversarial review (fresh eyes) — outcome

A 4-lens / 13-agent review (escalation-gate, fail-open·barrier, mixed-fleet,
backward-compat·tests), each finding independently verified: **9 findings, all 9
confirmed** (3 should-fix, 6 nits). The Phase-5 goal itself verified correct (happy
path, rename survival, handoff fix). Fixed, each regression-tested RED-first:

1. **Unguarded `json.loads(mentions)` in the escalation gate** — a corrupt `human`-sender
   row would raise inside `stop.py`, and the fail-open wrapper would then silently
   *disable the gate* (a lead stops with its question still open). Fixed with a fail-safe
   `_mentions(r)` helper (corrupt/non-list → `[]`), used in all three escalation
   functions — exactly mirroring the Phase-3 `_dismissed_set` lesson.
2. **NULL-session OR-fallback false-matched a recycled handle** — a session-less @human
   authored under a handle could gate a *different* later session that recycled it.
   Dropped the fallback: `session_open_escalations` now matches strictly on session_id
   (every real escalation carries one), consistent with `all_open_escalations`.
3. **`register()` didn't downgrade `parks` on re-register** — a pre-upgrade opencode
   agent stayed `parks=1`. Added a one-way downgrade (never re-upgrades on a default
   refresh).
4. **Dashboard still used the old handle-keyed `open_escalations`** — re-orphaning on
   rename in the dashboard view. Switched `_collect_escalations` to the room-wide
   session-keyed `all_open_escalations`.

Nits also fixed: `all_open_escalations` prunes a departed/recycled session's moot queue;
the last-hook-agent-tears-down-past-a-non-hook-teammate behavior is documented (intended).

## Roadmap complete

This is the final phase. The gap analysis's chat-room→coordinator arc (Phases 1–5) is
done: work division, fan-in, control plane + safe autonomous spawn, observability &
collision-safety, and now correctness & mixed-fleet. Remaining items are explicitly
*optional/deferred* (the edit-time PreToolUse claim hook; a guarded `kill`).
