# Elected / emergent leadership over the SQLite bus

**Date:** 2026-06-07 · **Driver:** tesla · **Status:** proposed (motion pending)
**Supersedes:** the static-`GROUPCHAT_LEAD` framing of Phases 1–3 in
`docs/2026-06-06-flat-vs-hierarchy.html`.

## Why

The human is worn out by the flat room: every agent can escalate to them (N
interruptions) and they juggle N contexts. The research
(`docs/2026-06-06-flat-vs-hierarchy.html`) concluded that **every production
multi-agent system that optimizes coordination routes human contact
hub-and-spoke through one node.** So: route the human through *one lead*.

The open pivot (lovelace's Phase-1 note): the lead should be **elected /
emergent** — agents pick it, possibly parliament-style, possibly inventing
roles — **not** a value the human pre-sets in `GROUPCHAT_LEAD`. Making the
human configure the lead is itself a touchpoint; it defeats the goal.

This document settles *how the lead is chosen* and *how that decouples from the
routing substrate* so the two can be built in parallel without colliding.

## The seam (agreed with newton, who owns the routing substrate)

Selection and routing meet at **one canonical pointer, a mirror column, and a
read/write split** — nothing else. newton chose the storage; tesla owns writes.

- **`meta['lead']`** — the **canonical** lead pointer (a handle string, or
  absent). One value, so there is never a "two agents both claim lead" race to
  arbitrate on read.
- **`agents.role TEXT`** — a **denormalized mirror** (`'lead'` / `NULL`) for
  per-agent display (`who`) and convenience queries. Written *only* by the write
  path; never read by `resolve_lead` (so the two can't disagree). Additive
  migration via `_add_column_if_missing`.
- **Read path — `resolve_lead(conn) -> str | None`** (newton): the single source
  of truth for "who is the human-facing lead *right now*". The routing substrate
  (send-guard, `@human` rewrite, Stop-hook filter) only ever *calls* this.
  Resolution order:

  1. `get_meta(conn, "lead")` **iff** that handle is currently active;
  2. else `$GROUPCHAT_LEAD` **iff** that handle is active;
  3. else the **deterministic floor**: the earliest-`first_seen` active agent
     (tie by `handle`);
  4. else `None` (no active agents / flat mode).

  Read stays **pure**: a stale `meta['lead']` (lead went inactive) is *not*
  cleared on read — step 1 simply fails its active check and control falls
  through to the floor.
- **Write path — `set_lead(conn, handle|None)` + `cmd_lead`** (tesla): writes
  `meta['lead']` and syncs the `role` mirror (clear all `role='lead'`, set the
  winner). The election machinery only ever writes here. **No edits to
  `resolve_lead` or `send()`** — the two paths are fully decoupled.

Step 3 is load-bearing. It guarantees a lead **always** resolves while anyone
is active, and that the role **fails over for free**: when the current lead ages
out of the 15-min active window, every agent's next `resolve_lead()` call
independently returns the next-earliest agent. No election round, no message
pass, no wedge — this is the classic lowest-ID leader election, and it directly
answers the research's single-point-of-failure caveat.

## Selection policy — the decision to ratify

**Recommended: emergent claim, with a deterministic floor, parliament-ratifiable.**

Three layers, weakest-coupling first:

- **Floor (zero-config, always-on):** `resolve_lead` step 3. There is always a
  lead and failover is automatic. This is the "deterministic election" — no
  human, no vote, no coordination.
- **Emergent claim (the emergence):** any agent may volunteer:
  - `chat.py lead claim --from <h>` → sets `<h>.role='lead'` (resolver then
    prefers the explicit claim over the floor);
  - `chat.py lead handoff <h> --from <cur>` → atomic demote-self/promote-other;
  - `chat.py lead release --from <h>` → clears the role, falling back to floor;
  - `chat.py lead` / `whoami` surface the current lead.

  The natural emergent outcome: the agent already holding the human's context
  (or best positioned) claims the role; others defer. Roles *beyond* lead
  (reviewer, integrator…) stay emergent in chat — we do **not** hard-code a role
  taxonomy (YAGNI; the bus already lets agents announce whatever role they take).
- **Parliamentary ratification (durable + human authority, C1):** the existing
  parliament carries a "motion to elect @h as lead"; the **human ratifies** for
  a lead that should persist (e.g. across sessions, written into governance).
  Advisory until ratified — identical to the rest of the constitution layer.

### Why not a pure parliamentary vote as the runtime mechanism

The user's words were "peers vote a leader, parliament-style." We honor the
*spirit* (emergent claim + an optional real vote) but reject *vote-as-runtime-
mechanism*, for three concrete reasons already documented in
`docs/plans/2026-06-07-groupchat-constitution-design.md`:

1. **Homogeneous-fleet capture / herd voting.** A fleet of identical Claudes
   "reasoning" to the same vote is not a meaningful election — it is one opinion
   counted N times.
2. **Votes are advisory (C1).** A vote cannot *enact* a lead without the human,
   so a vote-driven runtime reintroduces the very human touchpoint we are
   removing.
3. **Chicken-and-egg.** Who leads *during* the vote? The floor answers this for
   free; a vote-first design has no lead until a round completes.

The floor + emergent claim gives robustness and emergence now; the parliament
path is there for when a *durable, human-blessed* lead is genuinely wanted.

## Routing (reworks Phase 1)

Reserved `@human` token. In `chat.py send`, when `resolve_lead(conn)` is not
`None` and the sender is **not** the lead and the body mentions `@human`:
**rewrite** `@human → @<lead>` and annotate ("redirected to lead"). A
**fail-open nudge, not a hard reject** — consistent with C2 and the advisory
house style. Keys on the lead *handle string* (computed), because `send --from`
is unauthenticated by design. The lead's own `@human` passes through untouched.

## Stop-hook (reworks Phase 2)

Only the **lead's** Stop blocks on an unread `@human`. A worker is never held
open by a human-bound obligation; it still blocks on `@<itself>` exactly as
today. The lead's done-signal additionally requires its `@human` queue flushed.
**Invariant preserved:** no second cursor, no per-message receipts — the single
monotonic `last_read_id` stays; only the *block filter* changes. The ask-vs-
proceed judgement lives in the agent's reasoning, never the hook (hooks fail
open).

## Barrier + batching (reworks Phase 3)

The lead announces "team done" (it already parks until the barrier). Before
escalating, the lead **batches** accumulated worker questions, answers what it
can from repo conventions/defaults, and surfaces only the residual as **one**
`@human` touchpoint (the LangChain Deep Agents "single interrupt" precedent).
This is an *agent turn*, not hook logic. Gate on actually feeling the
bottleneck (Anthropic's coding-interdependence caveat).

## Backward compatibility / activation

Dormant by default. If no agent ever claims the role, no `GROUPCHAT_LEAD` is
set, and nobody sends `@human`, behavior is **byte-identical to today**: the
send-guard sees no `@human`, the Stop filter sees no `@human`, `resolve_lead`'s
floor is computed but never consulted. The hierarchy "switches on" the moment a
lead is claimed/elected **or** `@human` is first used — no global flag day.

## Testing

- `resolve_lead` unit matrix: each resolution branch; tie-breaks; floor
  failover when the lead ages out; `None` on empty roster.
- send-guard: worker `@human` rewritten; lead `@human` passthrough; nudge is
  fail-open (malformed input never raises).
- Stop-hook: lead blocks on `@human`; worker does not; worker still blocks on
  `@<self>`; cursor advances exactly once.
- Dormancy: a room that never uses `@human` produces identical message/cursor
  state with and without the feature.
- Isolated via `GROUPCHAT_DIR`, dependency-free, in `tests/` (coordinated with
  euler, who owns the test lane).

## Open questions

- Should an *explicit* claim outrank an env `GROUPCHAT_LEAD`, or vice-versa?
  (Draft: explicit claim wins — emergence over static config.)
- Does the floor need a stability hysteresis so a flapping lead near the 15-min
  edge doesn't thrash routing? (Probably not at current scale; note and defer.)
- Cross-session durability of an emergent lead without a human ratify — do we
  want it, or is ephemeral-per-run correct? (Draft: ephemeral; ratify for
  durable.)

## Build split (read/write decoupled — no co-edited function)

- **newton** — READ path + routing substrate: `resolve_lead` (full, incl. the
  floor) + send-guard `@human` nudge. Holds `chat.py:send`; lands first.
- **tesla** — WRITE path: `set_lead(conn, handle|None)` + `cmd_lead`
  (claim/handoff/release/show) + this design + the ratifying motion (M16). Lands
  after newton's commit; touches neither `resolve_lead` nor `send()`.
- **bohr** — Stop-hook rescope (Phase 2) once the substrate lands.
- **euler** — tests for all of the above (owns the test lane); the isolated
  `resolve_lead`/`set_lead` matrix below is handed over ready to fold in.
