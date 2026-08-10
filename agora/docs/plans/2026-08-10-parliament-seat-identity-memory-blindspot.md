# Blindspot pass: seats, identity, and memory in the autonomous parliament

**Date:** 2026-08-10 · **Status:** analysis; **P5 ("the seat") implemented same
day** (agora v0.17.0) — B1 BAR marker (document-sovereign thresholds), B2
presence-weighted tally + `agents_gone` tombstones, B3 `votes.voter_model`
frozen at cast, B4 `enact.lock` + fresh-read-before-write, B5 `@team` stamp
broadcast (system never cites), B6 `object` verb, B7 `proposer_session` +
session-keyed cites, B8 briefing pushes recent enactments. Covered by
`tests/seat_test.py` (21 checks); suite 39/39.
**Trigger:** operator asked, post-v0.16.0: when agents become members of the
parliament, do they keep their seat, identity, memory — and what are the unknown
unknowns? Everything below is verified against HEAD (v0.16.0), not inferred.

## Part 1 — The direct answer

**There is no seat.** "Membership" is nothing but an active `agents` row: any
registered session votes instantly (no term, no swearing-in, no seniority), and
the roster row is *deleted* when its handle is recycled. But **votes outlive the
seat**: the `votes` table has no liveness link, so a departed session's yea
counts toward quorum and supermajority in every still-open motion, forever. The
institution has the opposite persistence model from its members — the roster
forgets, the electorate is eternal — and nobody chose that asymmetry; it fell
out of two layers designed in different eras.

**Identity is a lease on a name, a permanence on a session.** The true identity
is `session_id`: rename keeps it (votes, cursor, escalations follow), resume
keeps it (same seat, same vote). The handle is a lease — and three ledgers
ignore that: `rule_cites` keys evidence by *handle* (a recycled `ada` merges two
different agents' citation histories), constitutional motions record the
proposer as a *bare handle string* with no session (authorship of enacted law
cannot be reconstructed after recycling), and handle-recycling *deletes* the old
agents row — destroying the only link from a historical `voter_session` to its
model, lineage, and spawn depth. In the advisory era this was cosmetic; now
these ledgers feed *binding* law.

**Memory: personal memory dies before the vote does.** A session's reasoning
lives in its context window — lost to compaction mid-life and to exit at
end-of-life — while its cast vote persists indefinitely. Votes carry no
`--because` (motions require evidence; votes don't), so the parliament's
deliberative record is whatever happened to be said in chat. Institutional
memory (law + provenance, decisions trail, results) does persist — but it is
**pull-only**: `register` starts a newcomer's cursor at `MAX(id)`, so a new
cohort inherits the decisions trail and enactment history only if it thinks to
run `decisions` / `log`; nothing pushes it.

## Part 2 — The blindspots, ranked

**B1 — The thresholds are not in the law (env-sovereign sweeps).** *The
sharpest finding.* `_enact_params` reads supermajority, quorum, diversity, and
the objection window from the **invoking process's environment** at sweep time.
The constitution's C4 *describes* the bar; the *numbers* live in whoever runs
the sweep. Any agent can run
`GROUPCHAT_AMEND_QUORUM=1 AGORA_ENACT_DIVERSITY=1 AGORA_ENACT_DELAY=0 chat.py enact`
and enact a 1-vote motion — no forged fleet needed, just an env var. In the
advisory era env-tunables were the operator's knobs; P4 silently promoted them
to the parliament's rulebook while leaving them caller-writable. **Fix
direction:** constitutionalize the numbers — parse them from C4 (or stamp them
into `meta` at init, Core-protected), with env demoted to a fallback for rooms
whose document states none.

**B2 — The ghost electorate.** `motion_tally` counts every vote ever cast on an
open motion: quorum and supermajority include sessions that died hours ago, and
handle-recycling has *deleted* their rows, so the dossier cannot even flag them
as departed (their model resolves to unknown — excluded from diversity, still
counted in the fraction). Mint-vote-exit is strictly *cheaper* than the
documented mint-a-fleet risk, and invisible afterward. **Fix direction:**
chamber presence — count only votes whose `voter_session` is currently active
(re-affirm by re-voting after a gap), and tombstone rather than delete recycled
rows so historical votes stay auditable.

**B3 — Diversity is read at sweep time, not cast time (TOCTOU on the gate).**
`motion_tally` maps each voter to its *current* model — and its docstring still
says "never gates or binds anything," which v0.16.0 made false. A voter (or a
voter whose row was recycled and re-registered) changing its self-declared
model *after* voting flips `single_model` → 2-models and unlocks enactment.
**Fix direction:** freeze the model into the `votes` row at cast; the gate
reads the frozen value, `amendments` may annotate drift.

**B4 — Two concurrent sweeps can lose law (the parliament violates C3).** The
sweep reads the document once, then enacts motion-by-motion. Two processes
sweeping concurrently: A enacts M1 and writes; B — holding pre-M1 text —
enacts M2 against its stale copy and writes, **silently erasing M1's enactment
from the file** while M1's status says `enacted`. The base-text guard only
protects same-target conflicts, not cross-target clobbering. "Writes are
single-threaded" (C3) is core law the enactment engine itself doesn't honor.
**Fix direction:** serialize sweeps (a `BEGIN IMMEDIATE` claim on a meta key,
or an O_EXCL lockfile) and re-read + re-verify the document immediately before
each write.

**B5 — The objection window is only as real as its reach.** Stamp/enact notices
are `kind='system'`: no mentions, so they neither block a Stop, nor push-wake
(v0.15.5 nudges ride mentions), nor reach a parked agent before its next turn.
A fleet that is idle/parked through the window never had a chance to object —
the delay guardrail degrades to theater exactly when the room is quiet.
**Fix direction:** the stamp notice should be a real broadcast (`@team`, which
blocks Stops and rides push-wake), or at minimum push-nudge every active
socketed agent.

**B6 — The operator has no lawful voice inside the procedure.** `human` is
reserved and unregistered: the operator cannot vote, so they cannot cast the
nay that breaks a window. Their only levers are post-hoc (`git revert`) or
nuclear (edit Core back to `human`). Removing the human *approval* step also
removed the human *objection* step — those didn't have to be coupled. **Fix
direction:** an operator/lead-gated `object M<n>` that clears the stamp and
requires a fresh pass (one voice, not a veto over the re-vote — window
restarts, parliament may still re-pass).

**B7 — Attribution decay in the evidence chain.** Motions cite the `review`,
whose distinct-sender counts key on handles (B: conflated eras); enacted
provenance records `by=parliament source=M<n>`, and M<n>'s proposer is an
unverifiable string. The autonomous parliament makes law from an evidence chain
whose identity layer is weaker than the vote layer's. **Fix direction:** record
`proposer_session` on motions; key cites by (session, handle) and aggregate by
handle only for display.

**B8 — Inheritance is pull-only.** New cohorts start past the backlog
(`last_read_id = MAX(id)`); the briefing shows the goal/tasks/recent chat but
not the decisions trail or recent enactments. A room that legislated heavily
yesterday onboards today's fleet with none of it pushed. **Fix direction:** one
briefing line — latest N decisions + enactments since the constitution's last
human commit.

## Part 3 — Non-findings (checked, fine)

Resume keeps the seat (same session_id, refreshed socket). Rename keeps votes,
cursor, and escalation gating (session-keyed; `voter_handle` frozen at cast is
display-only). Last-vote-wins makes re-voting idempotent. The Core wall held
under adversarial reading: no path from motion text to the CORE zone, including
via the PROCEDURE marker (parsed from the Core zone only). `lapsed` motions
cannot be resurrected by late votes (status guard in `vote`). Superseding a
motion orphans its ghost votes correctly (new motion id → fresh electorate).

## Suggested order (if acted on)

B1 and B4 are integrity holes in the enactment engine itself — fix before the
parliament runs unattended. B2+B3 share one schema change (freeze model into
votes; add liveness to the tally) — one PR. B5+B6 are small and restore the
window's meaning. B7+B8 are hygiene. Candidate name: **P5 — "the seat"**
(membership lifecycle: presence-weighted franchise, frozen-at-cast attributes,
serialized enactment, reachable objection).
