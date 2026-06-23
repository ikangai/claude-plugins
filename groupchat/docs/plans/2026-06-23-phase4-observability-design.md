# Phase 4 — collision-safety & observability

*2026-06-23. Implements Phase 4 of
`docs/plans/2026-06-22-coordination-gap-analysis.md` — surface who-owns-what and where
collisions lurk.*

## Goal

The roster showed liveness + tokens but never WHAT each instance is doing, nothing
warned when agents shared a working tree, there was no structured file-claim, and the
only health signal was the binary 15-min ageout. Phase 4 adds the cheap, high-value
surfaces. Same invariants: *dormant-until-used*, *additive*, *fail-open*,
*single-cursor*, *stdlib-only*, *old dbs upgrade in place*.

## What landed

- **`focus "…"`** — a per-agent current-work field (a `focus` column, **distinct from
  the barrier `status`** so it never touches done-detection), shown in `who` (`▸ …`) and
  the briefing's teammate list. Interior whitespace is collapsed so a focus can't spoof
  a roster line or inject newlines into the briefing.
- **Shared-cwd warning** — `who` and the briefing flag when 2+ active agents share a
  working tree (the high-collision default config). Dormant for a solo agent or a
  worktree team (distinct cwds).
- **`claims` ledger** — `claim <glob>` / `unclaim` / `claims [--path P]`: a structured
  "I'm editing these files," surfaced to teammates, self-cleaning (a crashed agent's
  claims age out with it). `path_claimed_by` + `_glob_matches` answer "who's editing
  this path?" — the foundation an edit-time hook would consume.
- **Amber dot** — `who` shows `◐` for an agent that's active but has gone quiet (no chat
  within the quiet window) — a soft stuck/heads-down signal between `●` and `○`.

**Deferred (the analysis marks it "optional"):** an edit-time **PreToolUse** hook that
warns on a claimed-file edit. It needs a 4th hook type wired through `install` *and* the
Codex/opencode bridge; the claims ledger + `path_claimed_by` is the substrate it would
read. Left as a clean follow-up (as `kill` was in Phase 3).

## Method

TDD: `tests/observability_test.py` written RED first, implemented to GREEN, then
hardened with the review findings below (34 checks). Full suite via `tests/run_all.py`
(28 modules).

## Adversarial review (fresh eyes) — outcome

A 5-lens / 27-agent review (invariants·fail-open·dormancy, quiet-detection,
claims·glob-matching, adversarial-inputs·perf, backward-compat·tests), each finding
independently verified: **22 findings, 20 confirmed** (5 should-fix, 15 nits). Notably
**refuted**: "the shared-cwd warning fires in every non-worktree room" — that's
*intended* (a shared tree IS the collision risk; a solo/worktree team never triggers
it), and focus is fully decoupled from the barrier status. Fixed, each regression-tested
RED-first:

1. **`_glob_matches` over- AND under-matched.** The old de-globbed-prefix *substring*
   test let `src` match `…/mysrc/…` (and `s` claim the repo), yet a precise
   `src/auth/*.py` *missed* the absolute path an Edit tool presents
   (`/repo/src/auth/handler.py`). **Fix:** fnmatch on path/basename + a leading-`*/` for
   the relative-glob-vs-absolute-path case, plus a **component-anchored** directory
   prefix (never a bare substring). Covered by a `test_glob_matches` table.
2. **`who` N+1 message scan.** `last_chat_age` ran a full `messages` scan per agent.
   **Fix:** `last_chat_ages` does it in one grouped query, threaded into `is_quiet`.
   `who` also computes the active set once now.
3. **Newline injection** in a focus/claim could spoof a roster line or inject raw
   newlines into the briefing. **Fix:** `" ".join(text.split())` at the source.
4. **Quiet-dot noise.** It flagged a legitimately heads-down agent (now suppressed when
   a `focus` is set — focus IS liveness), a solo agent with no consumer (suppressed when
   alone), and a NULL/NaN `first_seen` flipped the fresh-joiner guard the wrong way
   (now fails to not-quiet).

Nits also fixed: the unregistered `send --from` sender is lowercased (so last-chat
lookups match), a `re.split` positional-`maxsplit` DeprecationWarning, a tightened
dormant-claims assertion, and added tests for focus≠status orthogonality and a real
in-place old-db upgrade.

## Deferred

The optional PreToolUse enforcement hook (above), plus Phase 5 (escalation-orphan on
rename, mixed-fleet `done`, decision-rule docs).
