# Phase 3 — the control plane + safe autonomous spawn

*2026-06-23. Implements Phase 3 of
`docs/plans/2026-06-22-coordination-gap-analysis.md` — steering, teardown, and the
backstop that makes "Claude spawns a Claude" safe to consider.*

## Goal

Steering was cooperative-only (you `@mention` and hope); a long-running orchestrator
wedged its own finished workers at the barrier; and a spawned agent could fan out with
no recursion limit. Phase 3 adds a control plane and the autonomous-spawn safety layer.
Same invariants: *dormant-until-used*, *additive*, *fail-open*, *single-cursor*,
*stdlib-only*, *old dbs upgrade in place*.

## What landed

- **`standdown` / `disband`** — a timestamped meta flag; every parked agent is released
  from the barrier within a poll tick. Auto-expires after the active window so a stale
  flag can't haunt a reused room; `standdown --clear` lifts it. Lead/operator-gated.
- **`dismiss <handle>`** — release ONE agent from the barrier (lead/operator), so a
  still-active orchestrator doesn't pin its finished workers to the 2h ceiling. Keyed
  by session id (immune to handle reuse); marks the agent done so it stops holding
  others. **One-shot**: consumed when the agent leaves or revives.
- **`direct <handle> "…"`** — an imperative redirect: a blocking @mention after an
  active-set check.
- **`@team` / `@all`** — a broadcast token (`send`) that expands to every active
  teammate (minus the sender and reserved names), so a broadcast actually blocks
  everyone's Stop. `team`/`all` are reserved handles.
- **Spawn-depth + lineage + fleet ceiling** — `_spawn_command` threads
  `GROUPCHAT_SPAWN_DEPTH`/`GROUPCHAT_SPAWNED_BY` to each child; `register` records
  `spawn_depth`/`spawned_by` columns; `bootstrap` refuses past `MAX_SPAWN_DEPTH`
  (default 2, the runaway-recursion backstop) or `MAX_FLEET` (default 16). `--force` is
  the human override. The Stop hook reads `released_from_barrier` (standdown OR
  dismissed) each park tick.

**The headline gap-analysis question** — *should Claude spawn a Claude on its own?* —
is now a qualified yes: the depth guard means an autonomous (non-`--force`) agent can't
recursively fan out unbounded. Note the guard is **advisory**, not a sandbox: it
defends against accidental inherited recursion (the realistic failure mode), since an
agent can in principle set its own env. The native Agent/Workflow tools remain the
right choice for fan-out-then-join that returns structured results in-context; a
groupchat session is for a persistent, watchable, worktree-isolated, `@mention`-able
peer.

## Method

TDD: `tests/control_plane_test.py` written RED first, implemented to GREEN, then
hardened with the review findings below (40 checks). Full suite via `tests/run_all.py`
(27 modules).

## Adversarial review (fresh eyes) — outcome

A 5-lens / 22-agent review (fail-open·barrier-integrity, standdown/dismiss lifecycle,
broadcast/mention, spawn-guard, backward-compat·tests), each finding independently
verified: **17 findings, 14 confirmed** (5 should-fix, 9 nits). Refuted (kept as
regression guards): standdown overriding an escalating lead is *intended* (it's the
explicit teardown), `@team`/`@all` routing is correct, and the broadcast path doesn't
regress normal sends. Fixed, each regression-tested RED-first:

1. **Corrupt `dismissed` meta crashed the Stop hook → premature teardown.** The
   unguarded `json.loads` in `is_dismissed` could raise inside the park loop; the
   outer fail-open swallowed it (hook exits 0) but no block was emitted, so the agent
   stopped with teammates unfinished. **Fix:** a fail-safe `_dismissed_set` (corrupt /
   non-list → empty set → stays parked), mirroring `iso_age_seconds`. Not reachable via
   the system's own writes (which always emit valid JSON), but defense-in-depth on the
   barrier-critical path.
2. **Sticky dismissal.** A dismissed-then-revived session stayed "released" for life —
   after answering a teammate's @mention it would exit on the next Stop even though it
   was active again. **Fix:** dismissal is now **one-shot** — `clear_dismissed` is
   called when the agent revives (`_block_on_mention`) or leaves, so it rejoins the
   barrier.
3. **`standdown` was ungated** while the weaker `dismiss` was lead-gated — any worker
   could disband the fleet. *And* the dismiss gate wrongly rejected a bare operator
   invocation. **Fix:** a shared `_control_caller_ok` — the lead, the operator (sender
   'human'), and a bare CLI invocation (no identity = operator at the terminal) pass; a
   known worker agent is rejected. Applied to both `standdown` and `dismiss`.
4. **Negative `GROUPCHAT_SPAWN_DEPTH` defeated the backstop** and self-propagated.
   **Fix:** `current_spawn_depth` floors at 0.
5. Nits: `_expand_broadcast` excludes reserved handles (a legacy agent named
   `team`/`all` can't self-ping); `--force` help mentions all three backstops.

## Deferred (Phases 4–5 of the gap analysis)

`focus`/file-claim ledger, stuck/silent detection, shared-cwd warning; escalation-orphan
on rename, mixed-fleet `done`, decision-rule docs. An optional guarded `kill` was left
out of Phase 3 deliberately (OS-bound, PID-reuse-prone).
