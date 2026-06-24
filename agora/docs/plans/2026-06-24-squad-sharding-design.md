# Squad sharding — sub-teams with independent barriers

*2026-06-24. Item #1 from the Agora vision's build order — the scale keystone. Shards a
big fleet's team barrier into bounded sub-teams without touching the single-machine
transport.*

## Goal

The barrier and lead were global singletons over the whole active set, so a 100-agent
room couldn't decompose: every finished agent waited for the *entire* fleet. Squad
sharding gives each sub-team its **own barrier** so a finished squad tears down
independently — while keeping the lead / `@human` funnel **global** (the human still has
one point of contact). The default (no-squad) room is byte-identical to before.

## What landed

- **`agents.squad`** (guarded ALTER, NULL = the single global room). Set via
  `GROUPCHAT_SQUAD` at launch, the `squad <name>` verb at runtime, or `bootstrap --squad`.
- **`active_in_squad(conn, squad)`** + the barrier functions (`team_done`,
  `startup_guard_satisfied`, `cohort_age_seconds`, `expected_team_size`, `set_team_size`)
  now take a `squad` (default `None` = the default room). The Stop hook reads the agent's
  squad and gates its barrier on it; the re-park "waiting" message names the squad.
- **Per-squad sizes** — `expect --squad <name> N` and `bootstrap --squad` declare a
  squad's size via a `team_size:<squad>` meta key (the default room keeps the original
  `team_size` key). Each squad gets its own startup guard / solo-grace / time-fallback.
- **Surfacing** — `who` shows `·squad:<x>` per agent; `squad` shows your squad + mates.
  Dormant when no squads.

## The byte-identity guarantee (why this is safe)

The keystone insight: in an **unsharded** room every agent has `squad IS NULL`, so
`active_in_squad(conn, None)` is exactly `active_agents(conn)`, and `team_done(conn,
None)` is exactly the old `team_done(conn)`. The default room *is* "all agents." So the
whole barrier behaves bit-for-bit as before until a squad is actually used — verified by
`test_unsharded_room_is_byte_identical` and the full 31-module suite (incl. the existing
barrier/hub-and-spoke tests) staying green. `expected_team_size(None)` still honors
`$GROUPCHAT_TEAM_SIZE` and the global `team_size` meta exactly.

## Deliberate scope: shard the WORK, keep the human-contact funnel global

Only the **barrier** shards. `resolve_lead` / `@human` routing / the escalation gate stay
**global** — a worker in any squad still escalates to the *one* fleet lead, so the human
keeps a single point of contact (the founding goal). Per-squad leads (a council
hierarchy) are a deliberate follow-on, not this phase.

## Method

TDD: `tests/squad_test.py` (13 checks) written RED first — the two load-bearing ones are
the byte-identity guarantee and the e2e discriminator (`test_stop_hook_scopes_to_the_
agents_squad`: an agent released by ITS squad's barrier even while another squad works).
Full suite 31 modules (`doctor.py` EXPECTED updated for `squad`).

## Adversarial review (fresh eyes) — outcome

A 4-lens / 18-agent review (byte-identity, barrier-deadlock, staleness-integration,
fail-open·edges), each finding independently verified: **14 findings, 12 confirmed** (5
should-fix, 7 nits). Two were *positive confirmations*: the **byte-identity guarantee
holds** (an unsharded room is behaviorally identical), and the **global lead/@human
funnel is correctly NOT squad-scoped** (squads shard only the work barrier; the human
keeps one point of contact). No deadlock, no premature teardown. Fixed, each
regression-tested:

1. **`_clear_stale_team_size` wasn't squad-aware** — the headline. A stale
   `team_size:<squad>` was never reaped (a fresh solo squad-agent in a reused room waited
   ~90s), *and* the global reclaim was defeated by an agent in a different squad (the
   sole-active check counted the whole fleet). Now scoped to the agent's squad's keys +
   its own active set.
2. **Runtime `squad` change left a stale `first_seen`** — the new squad's cohort age
   (startup grace / solo settle) was dishonest. Now re-stamps `first_seen` on a change.
3. **Briefing was squad-blind** — a sharded agent saw fleet-wide counts, not its squad's.
   The SessionStart team line now scopes to the agent's squad (byte-identical when
   unsquadded), and `who` adds a per-squad breakdown.
4. **Junk squad name silently routed to the default room** — now refused with feedback.

## Deferred / next

Per-squad leads + a council hierarchy (cross-squad escalation); the AGORA rename (vision
item #2); and the safe frontier of vision item #3 (heterogeneous-model quorum; a
pluggable Transport seam) — distribution and binding votes stay deferred on principle.
