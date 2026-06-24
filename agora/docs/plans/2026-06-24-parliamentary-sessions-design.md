# Parliamentary framing — sessions, agendas, decisions

*2026-06-24. The first build from `2026-06-24-from-groupchat-to-agora-vision.md` — the
"framing before binding" layer. Adds the connective tissue of a deliberative body to the
advisory parliament WITHOUT changing what binds.*

## Goal

The parliament (motion → vote → human ratify) existed, but a flat chat had no *structure*
for deliberation and a late joiner inherited only ~15 chat lines, not the room's
decisions. Add sessions, agendas, and decisions — all additive, dormant-until-used, and
binding nothing.

## What landed

- **SESSION** — a bounded deliberation window. `parl_session` reads `meta['parl_session']`
  (+ `_at`, `_title`) and **auto-expires after the active window** (mirrors
  `standdown_active`, so a crashed opener can't leave one open). `open_parl_session` posts
  a `kind='session'` bookend (rides the cursor → every agent and late joiner learns it);
  `close_parl_session` posts the closing bookend, expires the session's leftover open
  items, and clears the pointer. One session at a time (a second `session open` is
  refused unless the prior is stale).
- **AGENDA** — the session's open items. Reuses the **`motions` table** (it already has
  the lifecycle, evidence, supersede, and advisory tally), scoped via a new guarded
  nullable `motions.session_id`. Constitutional items are the existing `op=amend/repeal/
  add`; **non-constitutional questions are a new `op='decide'`** (`add_decision_item`) — a
  question with **no `CONSTITUTION.md` target**. `agenda` lists them with tallies; voting
  is the existing `vote --session`.
- **DECISION** — `record_decision` posts a `kind='decision'` RECORD of the room's outcome
  (the motion id, the frozen advisory tally, the outcome text), marks the item `decided`,
  and is inherited via the cursor. `decisions` lists them; `audit` is the full
  session/motion/vote/decision trail. Recording is lead/operator-gated
  (`_control_caller_ok`).
- **Surfacing** — `who` and the SessionStart briefing (inside the existing fail-open try)
  show the open session + agenda count + recent decisions. Dormant when none.

## The load-bearing guarantee (mechanical, not documentary)

**A decision can never reach the law.** A decision item is `op='decide'` with no rule
target; **`cmd_ratify` refuses any `op='decide'` motion** (a guard placed before the
constitution is even read), and `record_decision` only writes a `kind='decision'` message
+ flips `motions.status='decided'` — it is structurally incapable of calling
`_apply_amendment`. The only path to the constitution stays `ratify --confirm` + a human
git commit (C1). The decision lane (advisory record) and the law lane (human ratify) are
kept cleanly separate, and `cmd_decision` refuses a constitutional motion ("resolve it
with `ratify`").

This is exactly what the vision doc's skeptic demanded: "session-close PHYSICALLY CANNOT
call `_apply_amendment`; if that hard separation isn't enforced in code, the abstraction
is a loaded gun." It is enforced in code.

## Method

TDD: `tests/sessions_test.py` (23 checks) written RED first, implemented to GREEN. Full
suite via `tests/run_all.py` (30 modules; `doctor.py` EXPECTED updated for
`motions.session_id`).

## Adversarial review (fresh eyes) — outcome

A 4-lens / 17-agent review (the law/decision safety guarantee, parliament integration,
fail-open·dormancy, gating·adversarial-inputs), each finding independently verified: **13
findings, all 13 confirmed** — but two are *verified-safe confirmations* that the
load-bearing guarantee holds **mechanically** (a decide item cannot reach the
constitution; `ratify` refuses it; `record_decision` structurally can't call
`_apply_amendment`). No hole in the safety guarantee. Fixed, each regression-tested
RED-first:

1. **`session show` errored out** — the subparser only registered open/close though
   help+docstring advertised `show`. Added a `show` subparser.
2. **Cross-lane supersede** — `cmd_motion`'s supersede `UPDATE … WHERE target=? AND
   status='open'` had no `op` filter, so a `motion --rule R2` superseded a `decide` item
   whose question literally normalized to "R2". Scoped it with `AND op!='decide'` (the
   law and decision lanes share the `motions` table but must not collide).
3. **Auto-expired session orphaned its open items** — the time-fallback released the
   pointer but left the session's `op='decide'` items `open`, leaking into the unscoped
   agenda. `parl_session` now **reaps** on staleness (expire items + clear meta), a lazy
   GC mirroring `_clear_stale_team_size`.
4. **`record_decision` re-decided a resolved item** — no status guard, so a second
   `decision M<id>` wrote a duplicate record. Now returns `already-resolved` and
   `cmd_decision` refuses.
5. **`decide` items showed in `amendments`** with the "worth a human's ratify look" flag,
   inviting the ratify mistake. `amendments` (the constitutional view) now excludes
   `op='decide'`; they live in `agenda`/`decisions`.

All nits resolved or accepted; the gating note (a bare/`--from human` invocation passes
the lead/operator check for `decision`) is the same accepted unauthenticated-bus property
as `dismiss`/`standdown`, and recording a decision binds nothing.

## Deferred (still, on principle)

Binding auto-apply of any decision — even on "reversible" executive state — remains
deferred indefinitely (a captured homogeneous fleet re-decides faster than an operator
reverts; auto-apply deletes the human brake). Heterogeneous-model quorum (recording each
voter's model family) is the only defensible way to let a tally carry more weight, and
only ever for a reversible lane — not built here.
