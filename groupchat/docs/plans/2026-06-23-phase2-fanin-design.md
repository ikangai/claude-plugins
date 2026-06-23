# Phase 2 — the fan-in layer (results / summary / worktree reconciliation)

*2026-06-23. Implements Phase 2 of
`docs/plans/2026-06-22-coordination-gap-analysis.md` — close the loop back to the
orchestrator so it collects structured outcomes instead of prose-grepping the chat.*

## Goal

Phase 1 made the room a coordinator (push work out: tasks/assign/goal). Phase 2 is the
return path (pull outcomes back in), holding the same invariants: *dormant-until-used*,
*additive*, *fail-open*, *single-cursor*, *stdlib-only*, *old dbs upgrade in place*.
**No new table** — results reuse the `messages` table via a new `kind`.

## What landed

- **`result --from <h> "…" [--task N]`** — posts a `kind='result'` message. Because
  `send()` gives every non-chat kind an empty `mentions`, a result **never @mentions
  anyone**, so it can't block a teammate's Stop or gate the team barrier (and it never
  harvests a constitution rule cite — that's chat-only). `--task N` also closes task N
  (the natural "finished my slice — here's the outcome") and tags the body `[task #N]`.
- **`results [--from <h>]`** — the orchestrator's fan-in view: every reported result,
  optionally filtered by sender. Dormant ("(no results reported yet)") until used.
- **`summary`** — a read-only one-shot digest: goal + roster (with ✓ done) + task tally
  and open items + results. The whole picture in one call instead of four.
- **`worktrees` / `harvest` [--base <ref>]`** — a read-only, **diff-only**
  reconciliation of the `bootstrap --worktree` branches: per `groupchat/<name>` branch
  ahead/behind, changed files, **cross-branch file overlaps** (the merge-carefully
  signal), and an advisory merge order (smallest blast radius first). It computes only
  `git rev-list`/`diff`/`for-each-ref`/`rev-parse` — it **never merges**; the operator
  runs the merges from the report.

## Method

TDD: `tests/fanin_test.py` written RED first, implemented to GREEN, then hardened with
the review findings below (now 36 checks). Full suite via `tests/run_all.py` (26
modules).

## Adversarial review (fresh eyes) — outcome

A 5-lens / 26-agent review (invariants·fail-open, result-semantics, worktree·git
read-only safety, adversarial inputs, backward-compat·test-integrity), each finding
independently verified: **21 findings, 18 confirmed** (many duplicates of two roots).
Verified non-bugs were also surfaced and kept as regression guards: a result carries no
@mention / never escalates / never harvests a cite (invariant 2), and `worktree_report`
is provably read-only even on adversarial `--base` (list-form `subprocess`, read-only
git verbs only — an injection-flavoured `--base` is just an unresolvable revspec).

Fixed, each regression-tested RED-first:

1. **`result --task N` poisoned the fan-in ledger.** `post_result` discarded
   `complete_task`'s return, so a typo'd/stale/nonexistent id still tagged the body
   `[task #N]` and printed "(closed task #N)" for a task that never existed — the exact
   thing the fan-in view exists to make trustworthy. **Fix:** `complete_task` now
   returns `'missing' | 'already' | 'done'` (via `rowcount`); `post_result` raises
   before storing anything on `'missing'`, and `cmd_result` reports the close honestly
   ("closed" vs "was already done" vs error), mirroring `task done`. Regression:
   `test_result_missing_task_errors`, `test_result_already_done_task_is_honest`.
2. **`worktrees` base resolution.** (a) The default base was the *cwd's* branch, so
   running it from **inside** a `groupchat/<name>` worktree compared that branch against
   itself — silently zeroing its own work. **Fix:** `_default_base` reads the **main**
   worktree's branch from `git worktree list --porcelain`. (b) A nonexistent `--base`
   produced a false "all clean / +0/-0" report. **Fix:** `cmd_worktrees` validates the
   resolved base with `rev-parse --verify` and errors out. Regression:
   `test_worktree_report_base_from_main_worktree`, `test_worktrees_nonexistent_base_errors`.

Nits also fixed: the unregistered-`--from` result sender is lowercased so it agrees with
the case-insensitive `results --from` filter; the printed `git merge <branch>`
suggestion is `shlex.quote`d for copy-paste safety; test coverage added for result
cite-inertness and @mention-inertness.

Kept by design (consistent with the Phase 1 decision): a non-owner may close a task via
`result --task` (the bus is cooperative/unauthenticated; `complete_task`'s `COALESCE`
preserves the original owner). The `--task` close is a documented, intended side effect.

## Deferred (Phases 3–5 of the gap analysis)

Control plane (`standdown`/`dismiss`/`direct`), a spawn-depth guard for autonomous
spawning, a `focus`/file-claim ledger, stuck-detection, and mixed-fleet `done` remain
open.
