# From group chat to a polity — vision, feasibility, and a name

*2026-06-24. Method: a 4-lens design exploration (governance evolution, scaling/
distribution, the self-organization model, naming) + an adversarial skeptic pass,
all code-grounded against the repo (5 agents). This is a direction-setting design
doc, not an implementation plan — it marks which moves are safe-additive, which hit a
wall, and what to build first.*

## The question

The system grew from a chat room into a coordinator (work division, fan-in, control
plane, observability, an emergent lead, a constitution + advisory parliament). The ask:
push it toward a self-organizing *government* — parliamentary sessions with agendas and
decisions — that scales to dozens/hundreds of instances, including **remote** ones on
other machines, and give it a new name.

## Verdict

- **The parliament is implemented** — `motion → vote --session → amendments (advisory
  tally) → human ratify (diff-only)`, verified end-to-end. It is **advisory by design**,
  not unfinished. The vote never enacts; a human ratifies from evidence.
- **"Government" is mostly safe to add — as *structure*, not as *binding power*.**
  Sessions, agendas, decisions, and a separation of powers are largely a *re-labeling of
  organs that already exist* (parliament = legislative, lead = executive, review/cites =
  judicial), plus a few additive records. All of it rides the existing bus and is
  dormant-until-used.
- **Two hard walls bound everything, and neither is a missing feature:**
  1. **The capture wall.** A homogeneous Opus fleet is *more* capturable — shared priors
     (not epistemically independent), herd/conformity bias, hallucinated votes. A
     supermajority is "one opinion counted N times." So a vote **cannot legitimately
     bind anything**; the binding act is a human reading verifiable evidence (Core C1).
     **Scaling to hundreds makes this worse, and makes vote-forgery easier — so the
     human gate must get *stronger* as the fleet grows, never weaker.**
  2. **The transport ceiling.** The bus is one shared SQLite file = **one machine**.
     Remote/cross-machine coordination is a **separate product with its own threat
     model** (CAP-bound barrier, clock-skew, cross-machine identity/forgery), *not* the
     next additive evolution of this one.
- **The name should be AGORA** — the ancient marketplace *and* assembly: it holds both
  halves (the work gets built *and* the polity deliberates) without overselling binding
  authority the way "Senate"/"Polis" would.

## Wall 1 — the capture wall (why "more democracy" mostly can't bind)

This is the load-bearing constraint, and three lenses reached it independently. The
existing `cmd_ratify --confirm` is *correct*: it freezes the motion, posts a `system`
notice, and **prints a diff a human applies and commits — it never writes
`CONSTITUTION.md`**. That human-reads-evidence gate is the only thing standing between a
captured fleet and a self-amended rulebook.

What this permits and forbids:

- **Safe (binds nothing):** record *that the room decided* X as an advisory, queryable
  `kind='decision'` — distinct from a chat line, inherited by late joiners and the next
  cohort. This fills a real gap (a joiner today inherits ~15 chat lines, not the room's
  decisions). The decision is a **record**; only `ratify --confirm` + a human git commit
  is enactment.
- **The one defensible lever to let a tally carry *more* weight is heterogeneous-model
  quorum** — record each voter's model family (`agents.model`) and count *distinct
  families*, because the capture mechanism is correlation, not count. Even then it is
  weak signal and may only ever influence a **reversible** action lane, never the law.
- **Deferred indefinitely, on principle (not as a "flagged opt-in" — flags get
  flipped): binding auto-apply of any decision, even on "reversible" executive state
  (goal/standdown/assign/dismiss).** The skeptic was decisive here: "reversible" is the
  wrong safety test for a captured *homogeneous* fleet, because the failure mode isn't
  one bad write you undo — it's the fleet re-deciding the captured outcome *faster than
  one operator can revert it*. Auto-apply deletes the human brake that was the whole
  point, and a cooldown+veto-window only "works" if the operator is watching — exactly
  the N-agent-juggling problem the lead layer exists to avoid.

**Three irreducible human checkpoints** (the system is already correct about all three;
the job is to not regress them): ratifying any `CONSTITUTION.md` change; the residual
`@human` escalation the lead can't resolve; and any file/code write.

## Wall 2 — the transport ceiling (one file = one machine)

- **One real bug bites *today* at dozens-on-one-machine, before remote matters:** the
  escalation gate (`open_escalations` / `session_open_escalations`) does a full
  `WHERE kind='chat' ORDER BY id ASC` scan, and the Stop-hook park loop calls it every
  ~2s tick — so N parked leads against an M-row log is O(N·M) every two seconds, a
  self-DoS. This is the #1 fix and it's pure read-side.
- **Single-machine ceiling ~dozens:** WAL gives many readers but **one writer**;
  `register()` fires on every prompt *and* every park tick, so writes scale with
  agents·turn-rate. Under contention, a fail-open write currently *silently drops*
  (`database is locked` → lost send / flickering liveness). Fail-open must mean "retry
  then degrade," never "lose a write."
- **"Hundreds of *running* agents on one machine" is an OS/process/cost wall, not a
  chat.db wall.** `MAX_FLEET=16` exists precisely because 100 live `claude` processes
  per host is RAM/cost-prohibitive. **You can *coordinate* hundreds (via sub-team
  sharding); you cannot *run* hundreds of persistent peers per host** — that's what the
  native Agent/Task tool is for (ephemeral inner fan-out). The vision must not conflate
  "coordinate hundreds" with "run hundreds."
- **Remote/cross-machine is a separate product.** The CLAUDE.md promise "swap the SQLite
  layer without touching the hooks" is *false as stated*: it needs a ~6-function
  `Transport` seam (all SQLite calls are inline today), and the hooks *do* see semantic
  change — a gateway-global id means `messages_since` must split globally-ordered from
  locally-pending, which is no longer the single monotonic cursor. The barrier is
  unsolvable under partition (a partitioned remote agent is indistinguishable from
  dead-or-slow → `team_done` degrades to the 2h ceiling). Clock skew corrupts the 15-min
  active window. And the gateway must *trust each machine's claimed session_ids* →
  forgeable quorum. Federation (per-machine local bus + a sync gateway) is the least-bad
  shape *if* this is ever built, but it is a new project, not an additive feature.

## What's genuinely safe to build (the additive evolution)

Dormant-until-used, single-cursor, fail-open all preserved:

1. **Perf first.** Add `idx_messages(kind, id)`; convert the escalation scans to an
   incremental high-water mark (`meta['escalation_clear_id']`, scan only `id >
   clear_id`). Bites today; zero invariant touched.
2. **The framing triad — SESSION / AGENDA / DECISION (binds nothing).** A `session` is a
   bounded deliberation window as `meta['parl_session']` + `kind='session'` open/close
   bookends (auto-expire after the active window, exactly like `standdown`). An *agenda*
   reuses the existing `motions` table (open motions scoped to the session) — add
   `op='decide'` to the free-form `op` column for decisions that don't touch
   `CONSTITUTION.md`, plus a read-only `agenda` view. A *decision* is an advisory
   `kind='decision'` record, queryable via `decisions`/`audit`. **Enforced in code:
   session-close can never call `_apply_amendment` — the only path to law stays
   `ratify --confirm` + human commit.** Payoff: a late joiner inherits the room's
   decisions, not just the last 15 chat lines.
3. **The `group` keystone (sharding).** Add `agents.group` (NULL = today's single room,
   byte-identical). Thread it through `active_agents` / `team_done` /
   `startup_guard_satisfied` / `resolve_lead` / `expected_team_size` so 100 agents shard
   into bounded squads, each with its own local lead + barrier; hierarchical fan-in rolls
   a squad's results up to a council digest. This is the riskiest *safe* item (it touches
   the two most invariant-laden functions), so it lands after the cheap wins and behind a
   test proving a NULL-group room is bit-for-bit unchanged.
4. **Roles are display tags only.** `lead` is the one role with mechanical force (and
   that's correct). A same-model "reviewer/arbiter" is *not* an independent check — it's
   the same priors wearing a badge. Don't manufacture false separation-of-powers.

## The name — AGORA

The agora was at once the **marketplace** (where the work happens — tasks claimed,
results traded, the repo built) and the **assembly** (where citizens deliberate and
vote). It uniquely holds *both* halves of what the system became, harmonizes with the
scientist "citizen" handle pool (ada/turing/hopper…), is short and sayable, and is
comparatively unclaimed in agent-orchestration. Runners-up: **POLIS**, **COMMONS**
(Ostrom's self-governed commons — apt, but partly claimed). Avoid Senate/Polis-as-decider
framing: a name that implies the assembly *decides* invites the exact capture-wall
fiction C1 forbids.

Migration (mirrors the codebase's own dormant-until-used discipline; **reserve the
PyPI/npm/GitHub-org/marketplace slug first — that gates the choice**): `store_dir()`
reads `.agora` first, falls back to legacy `.groupchat` for one release; accept both
`AGORA_*` and `GROUPCHAT_*` env (new wins); `/agora:*` commands with `/groupchat:*`
shims; a major-version bump and a docs rewrite. **Name the polity now; do not market
"distributed/remote" until a real Transport seam exists** — that adjective has to be
earned.

## Build order

1. **Perf** — `idx_messages(kind,id)` + incremental escalation high-water mark. *(bites
   today, pure-additive)*
2. **Framing** — session/agenda/decision records + read-only `agenda`/`decisions`/`audit`
   views; the hard code-level guarantee that a decision can't ratify-by-accident.
3. **Sharding** — the `group` scope (NULL-default), behind a byte-identical test; then the
   session-composition roll-up (assign→goal→result fan-in) on top.
4. **Contention** — bounded-retry-on-lock writes; throttle the park-tick re-register to a
   cheap heartbeat. *(fail-open = "retry then degrade", never "drop")*
5. **Rename → AGORA** — dual-read migration, after the slug is secured.
6. **Defer indefinitely** — binding auto-apply (even the "reversible" lane), cross-machine
   transport, cross-machine voting. Distribution *amplifies* capture/forgery, so it is a
   reason to strengthen the human gate, never to relax it.

## The honest through-line

The protective invariants — fail-open hooks, the single monotonic cursor,
dormant-until-used, and human-as-final-authority — are doing **all** the real work. Every
"more government / more scale" idea either re-labels an organ that already exists
(additive, fine) or crosses a wall (defer). The system becomes more of a *polity* by
adding framing, perf, and composition; it does **not** become more *autonomous* by letting
votes bind or spanning machines — those make the homogeneous-fleet capture problem worse.
A polity whose constitution is enforced by *a human reading evidence* is the feature, not
a limitation to engineer away.
