# Per-squad leads + the council hierarchy (chair-topped)

*2026-06-24. The leadership layer's scaling step: in a multi-squad fleet, each squad has
a captain; the captains escalate to one chair, who is the sole human contact. Worker →
squad lead → chair → operator.*

## Why

Squad sharding (#1) deliberately kept the lead **global** — but at fleet scale, one lead
absorbing every worker's `@human` is the bottleneck the leadership layer was meant to
remove, now just one level up. The council adds an intermediate tier: a **per-squad lead**
(captain) absorbs its squad's questions; only the residual escalates to the **chair** (the
global lead), who remains the single operator contact. The human still talks to one node.

## The shape (chosen over a flat council)

```
worker(sq A) --@human--> lead(A) --@human--> CHAIR --@human--> operator
worker(sq B) --@human--> lead(B) --@human--> CHAIR --@human--> operator
```

A flat council (each captain contacts the operator directly) was the simpler option but
relaxes "one human contact" to K contacts; the chair-topped shape preserves the founding
single-contact goal.

## How it composes with what's already there (minimal new machinery)

- **`resolve_lead(conn, squad=None)`** — resolves the lead *within* `active_in_squad(conn,
  squad)`: the `lead:<squad>` pointer if its holder is active in the squad, else the floor
  (earliest-joined active member). `squad=None` is the chair (the existing global
  resolution — `meta['lead']` / `$AGORA_LEAD` / global floor). Emergent + claimable, same
  as the global lead, now per squad.
- **Routing (`human_redirect_target`):** by the sender's role —
  - a **worker** (in a squad, not its lead) → its squad lead, `@human` **stripped**
    (delegated; not gated — same as today's worker→lead);
  - a **squad lead** (not the chair) → the **chair**, `@human` **kept** (so the existing
    per-session gate parks it until answered) and the chair @mentioned for delivery;
  - the **chair** → the operator, `@human` passes through (today's lead behavior);
  - a **default-room worker** (no squad) → the chair, stripped (exactly today).
- **The gate is reused, not rebuilt.** A squad lead's kept `@human` makes
  `session_open_escalations` park it — and because the **barrier is already per-squad**, a
  parked captain keeps *its* squad up automatically. The chair's `@human` parks the chair
  (and its squad) until the operator answers. The **only** generalization: an escalation
  clears on the operator's reply **or the chair's reply** to the asker (so the chair
  relaying the answer down releases the captain). Dormant when unsharded — no squad leads
  exist, so the new clause never fires and the existing leadership/escalation tests stay
  green byte-for-byte.
- **`questions` (operator view)** flags the **chair's** escalations as the operator-level
  ones; a squad lead's in-flight escalation is the chair's to relay (a soft funnel,
  consistent with the system's other fail-open nudges).

## Surfacing

`lead` is squad-scoped (claim/designate/release your squad's captain); a new **`council`**
view shows the chair + each squad's captain; `who` marks each squad's `★lead`.

## Invariants preserved

Dormant-until-used (unsharded == today, byte-identical); single cursor; the chair is the
sole operator contact; the per-squad barrier is untouched (it composes); hooks fail open.

## Adversarial review (fresh eyes) — outcome

A 4-lens / 21-agent review (dormancy, gate-deadlock, routing-edges, integration), each
finding independently verified: **17 findings, 16 confirmed**, including **4 blockers that
were all one root cause** — found independently by all four lenses (a strong signal). My
chair-relay clear-clause keyed on the **live** chair (`resolve_lead(conn, None)` at query
time) while replaying immutable history: a **time-varying predicate**. It (a) broke
flat-room dormancy (the current chair @mentioning any agent cleared its owed *operator*
escalation → premature teardown) and (b) **re-opened** an already-answered captain
escalation the moment the chair changed (rename / hand-off / floor failover → the captain
got dragged back to the barrier). Fixed, each regression-tested:

1. **Blockers — the clear is now TIME-INVARIANT and captain-scoped.** A captain's
   escalation clears on a reply from a **frozen addressee** (a handle it actually
   escalated to — its escalation @mentions the chair-at-that-time, recorded immutably) OR
   the current chair; and the whole clause only fires for a **squad-having** asker, so a
   flat room is a strict no-op (byte-identical — verified by `test_flat_handoff_does_not_
   clear_owed_operator_escalation` and the chair rename/hand-off tests).
2. **`cmd_questions` partition** now keys on the asker being a captain (has a squad), not
   on "author == current chair" — so a handed-off former chair's owed escalation stays
   under "awaiting you" and a flat room's in-flight section is empty.
3. **`register` reclaim & `rename`** repoint/clear `lead:<squad>` captaincy pointers, not
   only the global `lead` (a captain renaming keeps its captaincy; a handle-reuser doesn't
   inherit a stale one) — rename scoped to the renamer's own held scopes.
4. Nits: the relay-clear ignores a reply that is itself an `@human` ([8]); `cmd_answer`
   notes when answering a captain bypasses the funnel ([10]); the captain redirect is
   idempotent ([13]); `who` crowns designated `★captain`s ([15]).

Accepted/deferred: an *asker* renaming after being answered can re-open its own escalation
([6]) — a **pre-existing** latent in the operator-clear, not introduced here; the
per-send linear active-set rescans ([11][14]) are on a cold path. Full suite 34/34.
