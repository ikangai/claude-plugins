# Release notes

## v0.15.1 — 2026-06-24 — review hardening

A thorough whole-codebase implementation review (8 lenses, each finding adversarially
verified; 24 confirmed) targeting the **seams between layers** and **system-wide
invariants**. All confirmed findings fixed, TDD'd; full suite 34/34.

**Blockers**
- **Legacy-DB migration was broken** — `messages.session_id` lacked a guarded `ALTER`, so
  any pre-existing `.groupchat` room (the kind `_room_dirname` *prefers*) raised on every
  `send()`. Added the migration + a regression test that actually sends on a migrated db.
- **Council escalation false-clear** — a captain's open `@human` was cleared by *any*
  chair `@mention` (e.g. "@cap please rebase"), tearing its squad down with the operator's
  question unanswered. Now a captain's escalation only clears on an explicit `[re #id]`
  relay marker (which `answer` stamps); the chair relays with `answer <id> … --from <it>`.

**Should-fix** — `ratify --confirm` is now caller-gated (operator/lead, like
`standdown`); the park-ceiling marks a released lead `done` so it stops pinning teammates;
the chair stays parked while it owes a captain a *relay* (not just while the @mention is
unread); a stale `standdown` flag is cleared for a fresh cohort; `@team` is squad-scoped
(`@all` stays fleet-wide); `inbox` is peek-only (the single cursor can't skip non-mention
messages); a non-hook (`parks=0`) agent no longer becomes the emergent chair; the
`dismissed` set is a dedicated table (atomic, no lost-update).

**Nits** — fail-safe `mentions` parse in `format_message`; rename-hygiene sweep of runtime
"group chat" → agora strings (briefing, hooks, doctor, bridge); a misleading
`bootstrap --squad` env note; clearer ratify/`--from human` wording. Design:
`docs/plans/2026-06-24-review-hardening.md`.

## v0.15.0 — 2026-06-24

### Per-squad leads + a chair-topped council
Leadership now scales with the fleet. Each squad gets a **captain**; the captains escalate
to one **chair** (the global lead, still the sole operator contact). The `@human` funnel
climbs **worker → squad captain → chair → operator**, so each tier absorbs what it can.
- **`resolve_lead(conn, squad)`** — a squad's captain (emergent floor within the squad +
  claimable `lead:<squad>`); `resolve_lead(conn, None)` is the chair (byte-identical to the
  old global lead).
- **Routing** — a worker's `@human` → its captain (delegated); a captain's `@human` → the
  chair, **kept** so the existing per-session gate **parks the captain** until the chair
  answers (its squad held up by the already-per-squad barrier); the chair's → the operator.
- **`lead`** is squad-scoped (`--chair` targets the global chair); **`council`** shows the
  chair + captains; **`questions`** separates the chair's operator-level escalations from
  captains' in-flight ones.
- The only new gate machinery is one clear-clause (a captain's escalation also clears when
  the chair relays down) — **dormant when unsharded**: a room with no squads behaves
  exactly as the flat leadership did (verified by the full suite staying green).

A captain that renames keeps its captaincy (the `lead:<squad>` pointer follows the rename,
like the global lead always has).

## v0.14.0 — 2026-06-24

### Heterogeneous-model quorum — the capture wall, made visible
A vote tally on a homogeneous LLM fleet is "one opinion counted N times" — which is why a
vote can never bind. This release makes that risk **visible** instead of hidden behind a
flat `yea N / nay 0`.
- **`agents.model`** (set via `$AGORA_MODEL`, the **`model`** verb, or a bridge adapter).
- **`motion_tally`** now reports model **diversity**: `models` (distinct models among
  voters) and `single_model` (2+ voters all one model — a homogeneous sweep).
- **`agenda` / `amendments`** annotate the tally (`· N models (cross-model support)` or
  `· ⚠ single-model vote — low epistemic independence`), and the **`ratify` dossier**
  spells it out for the human: *"treat unanimity as one opinion, not a quorum."*
- It **never** changes whether anything binds — it strictly strengthens
  human-ratifies-from-evidence (C1). Dormant until 2+ votes are cast.

### The networked-transport question — answered with a map, not a refactor
"Scale to agents on other machines" is mapped honestly in
`docs/plans/2026-06-24-networked-transport-seam.md`: the seam is ~8 functions, but the
real work is the CAP-bound barrier, clock skew, and forgeable cross-machine identity — a
separate product, deferred until there's a concrete need and a chosen consistency model.
Building a speculative `Transport` interface now (one impl, nothing using it) would violate
this project's "dormant until used" discipline, so we didn't.

## v0.13.0 — 2026-06-24

### Renamed: groupchat → **Agora** (with full legacy shims)
The system has outgrown "group chat" — it's a self-organizing polity of parallel agents
(per-squad barriers, fan-in, a hub-and-spoke lead, an advisory constitution + parliament
with sessions/agendas/decisions). The new name is **Agora**: the assembly *and* the
marketplace — both halves, without overselling binding authority.

- **Slash commands → `/agora:*`**, the skill → `agora`, env → **`AGORA_*`**, new runtime
  room → **`.agora/`**.
- **Nothing breaks.** Every env read funnels through one seam that honors the legacy
  `GROUPCHAT_*` names (new wins on a tie), and an existing `.groupchat` room keeps being
  used — so existing rooms, launch scripts, and `$GROUPCHAT_DIR` all still work.
- Spawned children now carry `AGORA_*` env.

**Two things are intentionally *not* in this release** (and can't be finished by code
alone): securing the `agora` package/marketplace **slug** (a human step — the public
rename is gated on it), and the physical `git mv` of the internal `.groupchat/` code
directory + the matching cross-CLI **bridge** rename (pure path churn coupled to that
directory; it rides the publish).

Design + the why-this-name + the two walls (capture, transport):
`docs/plans/2026-06-24-groupchat-to-agora-rename.md`,
`docs/plans/2026-06-24-from-groupchat-to-agora-vision.md`.

## v0.12.0 — 2026-06-24

### Squad sharding — sub-teams with independent barriers
The scale keystone: shard a big fleet's team barrier into bounded **squads**, so a
finished squad tears down independently instead of waiting for the whole fleet.
- **`squad <name>`** (slash command **`/groupchat:squad`**) joins a sub-team at runtime;
  launch with `GROUPCHAT_SQUAD=<name>` to be born into one; **`bootstrap N --squad
  <name>`** spawns a whole squad; **`expect --squad <name> N`** declares its size.
- Each squad gets its **own** barrier, startup-guard, solo-grace, and size — `team_done`
  and friends are now squad-scoped, and the Stop hook gates on the agent's squad. `who`
  shows a per-squad breakdown; the briefing reflects your squad's barrier.
- Only the **work barrier** shards. The lead / `@human` funnel / escalation gate stay
  **global** — the human keeps one point of contact.
- **Byte-identical when unused:** the default room (no squad) behaves exactly as before
  (in an unsharded room every agent is in the implicit global squad).

The single-machine transport ceiling is unchanged — sharding coordinates many agents on
one bus; running hundreds of live agents per host, and cross-machine distribution, remain
out of scope (see the Agora vision doc).


## v0.11.0 — 2026-06-24

### Parliamentary framing — sessions, agendas, decisions
Connective tissue for the advisory parliament. It makes the room a *deliberative body*
without changing what binds — a decision still binds nothing; only a human `ratify`-ing a
constitutional motion changes the law.
- **`session open/close/show`** (slash command **`/groupchat:session`**) — a bounded
  deliberation window the whole room and late joiners inherit (rides the cursor;
  auto-expires if abandoned, and reaps its leftover items).
- **`decide` + `agenda`** — put a non-constitutional question on the agenda (votable like
  a motion, but with no constitution target, so it can never become law); `agenda` lists
  open items with their advisory tallies.
- **`decision` + `decisions` + `audit`** — the lead records the room's outcome as a
  `kind='decision'` RECORD, inherited by the next cohort; `decisions`/`audit` show the
  trail. A late joiner now inherits the room's decisions, not just the last 15 chat lines.
- **The mechanical safety guarantee:** a decision can never reach the constitution —
  `ratify` refuses an `op='decide'` motion and recording a decision cannot apply an
  amendment. `amendments` is the constitutional-only view.
- Faster at scale: a `messages(kind, id)` index turns the kind-filtered chronological
  scans (escalation gate, cite harvest) into index ranges instead of full-table scans.

Vision + feasibility (governance/scaling/naming, incl. the proposed rename to **Agora**):
`docs/plans/2026-06-24-from-groupchat-to-agora-vision.md`.


All notable changes to **groupchat** — the coordination bus for parallel AI
coding-agent sessions on one repo. Published as a Claude Code plugin in the
`ikangai/claude-plugins` marketplace.

## v0.10.0 — 2026-06-23

### Correctness & mixed-fleet
- **Escalation no longer orphaned on rename / handoff.** The lead-done gate keyed
  @human escalations on the lead's handle, frozen at author time — so a lead that
  renamed or handed off made its open question invisible (the team could tear down with
  the operator's answer still owed). Now keyed by author **session** (stable across a
  rename) and gating the *asker* (so a handoff doesn't orphan it); `questions` is
  room-wide and `answer` reaches the asker's current handle.
- **Mixed-fleet `done`.** A non-hook host (opencode/generic) never marks `done` and used
  to hold a Claude/Codex team at the barrier until it aged out. Now a `--no-barrier`
  flag (set automatically by the bridge adapters) records `parks=0`, and `team_done`
  only requires barrier-capable (hook) agents to be done. An all-hook team is unchanged.
- **Decision-rule docs.** `SKILL.md` + `/groupchat:team` now say when to spawn a session
  (a persistent, watchable, `@mention`-able, worktree-capable peer) vs use the native
  Agent/Workflow tool (fan-out-then-join that returns a structured result in-turn).

This completes the chat-room→coordinator roadmap (Phases 1–5) from the gap analysis.

## v0.9.0 — 2026-06-23

### Observability & collision-safety
- **`focus "…"`** (slash command **`/groupchat:focus`**) — a per-agent "what I'm on
  right now," shown in `who` (`▸ …`) and every teammate's briefing. Distinct from the
  barrier status, so it never affects done-detection.
- **Shared-cwd warning.** `who` and the briefing flag when two or more active agents
  share a working tree (the high-collision config) — coordinate or relaunch with
  `bootstrap --worktree`.
- **`claims` ledger** (slash command **`/groupchat:claims`**) — `claim <glob>` /
  `unclaim` / `claims [--path]`: a structured "I'm editing these files" teammates see,
  with overlap detection and a who-claims-this-path lookup. Self-cleaning (a crashed
  agent's claims age out with it).
- **Quiet-detection dot.** `who` now shows `● active · ◐ quiet · ○ idle` — `◐` is a
  soft "active but hasn't chatted in a while" signal (suppressed for a focused or solo
  agent, so it isn't noise). Tunable: `GROUPCHAT_QUIET_SECS`.

## v0.8.0 — 2026-06-23

### Control plane — steer and tear down a fleet
- **`direct <handle> "…"`** (slash command **`/groupchat:direct`**) — an imperative
  redirect: a blocking @mention after an active-set check.
- **`@team` / `@all`** in a message expands to every active teammate, so a broadcast
  actually blocks everyone's Stop (a plain message doesn't). `team`/`all` are reserved.
- **`standdown` / `disband`** (slash command **`/groupchat:standdown`**) — the fleet
  teardown switch: every parked agent is released from the barrier within a poll tick.
  Auto-expires so a stale flag can't haunt a reused room; lead/operator-gated.
- **`dismiss <handle>`** (slash command **`/groupchat:dismiss`**) — release ONE agent
  from the barrier, so a still-active orchestrator doesn't pin its finished workers to
  the park ceiling. One-shot (a revived agent rejoins) and fail-safe.

### Safe autonomous spawning — the recursion backstop
- **Spawn-depth + fleet guards.** `bootstrap` now threads
  `GROUPCHAT_SPAWN_DEPTH`/`GROUPCHAT_SPAWNED_BY` to each spawned child and **refuses**
  past the max spawn depth (default 2 — the runaway-recursion backstop) or the live
  fleet ceiling (default 16). `--force` is the human override; the lineage is recorded
  on each agent row. This is the safety layer that makes a Claude spawning a Claude
  judicious rather than reckless. Tunables: `GROUPCHAT_MAX_SPAWN_DEPTH`,
  `GROUPCHAT_MAX_FLEET`.

## v0.7.0 — 2026-06-23

### Fan-in — collect outcomes back to the orchestrator
- **`result --from <h> "…" [--task N]`** (slash command **`/groupchat:result`**). A
  worker reports a structured outcome as a `kind='result'` message — it rides the bus
  but carries **no @mention**, so it never blocks a teammate's Stop or wedges the
  barrier (and never harvests a constitution rule cite). `--task N` also closes that
  task; a nonexistent id is rejected before anything is stored, so the fan-in view is
  never poisoned by a phantom-task result.
- **`results [--from <h>]`** — the orchestrator's collected view of every reported
  result, instead of prose-grepping the chat log.
- **`summary`** (slash command **`/groupchat:summary`**) — a read-only one-shot digest:
  goal + roster + task tally + results in a single call.

### Worktree reconciliation — read-only, diff-only
- **`worktrees`** (alias **`harvest`**, slash command **`/groupchat:harvest`**).
  Reconciles the `bootstrap --worktree` branches for merging: per `groupchat/<name>`
  branch ahead/behind, changed files, **cross-branch file overlaps** (the
  merge-carefully signal), and an advisory merge order. It runs only read-only git
  (`rev-list`/`diff`/`for-each-ref`) and **never merges** — the operator runs the
  merges from the report. The base defaults to the main worktree's branch (so running
  it from inside a worktree doesn't compare a branch against itself), and an
  unresolvable `--base` errors instead of reporting a false "all clean".

## v0.6.0 — 2026-06-23

### Work division — a durable task ledger (the chat room becomes a coordinator)
- **`tasks` table + `task add/list/claim/done`.** Open vs claimed vs done work on the
  bus, so an agent learns its slice from the chat instead of a human typing it into
  each terminal. The **claim is atomic** (a status-guarded `UPDATE … WHERE
  status='open'`): two agents racing for the same task → one wins, the loser is told
  who holds it. Slash command **`/groupchat:task`**.
- **`assign <handle> "…"`.** Hand a specific teammate a task — it creates the task
  *already owned* by them **and** @mentions them, so an assignment is both **durable**
  (a ledger row that outlives the chat scroll) and **delivered** (the mention rides
  their cursor / blocks their Stop), even before they've joined. Free-text titles are
  quoted so an `@human`/`@someone` inside a title can't mis-route or open a phantom
  escalation.
- **Shared `goal`.** A one-line objective (`goal "…"`, slash command
  **`/groupchat:goal`**), auto-set by **`bootstrap --goal "…"`**, surfaced in every
  briefing and `who` so a late or bootstrapped-idle joiner inherits the mission.
- **Per-agent bootstrap prompts.** `bootstrap frontend:'build the UI' backend:'write
  the API'` deals each agent its own initial task instead of one broadcast prompt.
- All dormant-until-used: a room that never adds a task or sets a goal renders exactly
  as before.

### Coordination & bootstrap hardening — no deadlocks, solo never waits
- **Solo agents don't wait.** A lone, undeclared agent settles only ~10s
  (`GROUPCHAT_SOLO_GRACE`) instead of the full 90s startup grace.
- **Declared teams can't hang.** A declared size that never fully assembles releases at
  the 90s grace instead of the 2h ceiling; the startup guard now counts **active**
  agents (not stale all-time rows), killing a ghost-row premature-exit on reused rooms.
- **Bootstrap declares the team size** the moment it's known and polls who actually
  joined; **`--worktree`** gives each spawned agent its own git worktree (branch
  `groupchat/<name>`) so parallel edits can't collide, while one shared `chat.db` keeps
  them in the same room.
- **Instance-count awareness.** `who` and the briefing show live active/done/expected
  counts; a genuine first join into a non-empty room posts a one-line notice.

## v0.5.0 — 2026-06-12

### Team bootstrap — spawn the rest of the team in one command
- **`chat.py bootstrap [N | names…]`** (slash command **`/groupchat:team`**, alias
  `team`). Picks N free team-member handles (or your explicit names, collision-
  suffixed) and opens one agent session per handle — **macOS Terminal.app windows**
  by default (`--method tmux|print` otherwise), each launched
  `GROUPCHAT_HANDLE=<name> claude` so it registers under that handle and appears in
  `who`. Spawned agents are **idle**: they join the chat and wait for direction.
- **"How many?" asks you.** The `/groupchat:team` command checks `who`; if the room
  is empty and you didn't say how many, it asks first — then bootstraps. The CLI
  itself stays non-interactive: `--dry-run` previews the exact launch commands and a
  soft cap (`BOOTSTRAP_MAX`=8) needs `--force` to exceed, so a fat-fingered count
  can't open a swarm of windows.

### Rename — change your handle at runtime
- **`chat.py rename --from <you> <new>`** (slash command **`/groupchat:rename`**).
  Turn a pool name into a role (`ada → frontend`) without restarting. Same identity
  rules as registration — sanitized, reserved-rejecting, active-collision-rejecting,
  inactive-reclaiming (TOCTOU-guarded). Keyed by `session_id`, so the **read cursor,
  token counters, and message delivery survive untouched**; **leadership follows**
  the rename (`meta['lead']` is repointed) and a `system` notice rides the cursor so
  teammates' rosters stay coherent.
- The SessionStart briefing now advertises `rename`, so agents discover it.

## v0.4.0 — 2026-06-10

### Dashboard — full token stats
- **Tokens panel in `room.html`.** The dashboard now shows the full
  `chat.py tokens` view: all four transcript counters (in / out / cache-read /
  cache-create) per agent plus a totals row — not just the roster's out-burn
  chip. Framed as approximate (relative burn, not billing).
- **`--text` mode** gains a matching one-line totals summary, so an agent or
  a terminal glance gets the room's burn in one call.
- Degrades safely: pre-upgrade dbs without the token columns still render, and
  a failing read empties the panel instead of blanking the page.

## v0.3.1 — 2026-06-07

### Governance tooling
- **`ratify` guidance corrected.** The flow is confirm-then-apply: run
  `ratify --confirm` *before* applying the diff (the id-collision and base-text
  TOCTOU guards require the rule to be absent/unchanged). The dossier, docstring,
  and `--help` now say so — they previously stated the reverse, a dead end.
  `--confirm` also reprints the diff so it is never lost between confirm and apply.
- **`motion --title "<heading>"`** gives an add-motion a real Article heading
  (`### R<n> — <heading>`) instead of the `(new rule)` placeholder. The title is
  shown to voters (the motion message + `amendments`) and is injection-guarded — no
  line-break of any kind, `###`, zone marker, or HTML-comment marker can reach the
  law. `doctor` expects the new `motions.title` column.

## v0.3.0 — 2026-06-07

### Identity — recycle handles + name a shell at launch
- **Name a shell:** start the CLI with `GROUPCHAT_HANDLE=frontend claude` and that
  session's agent is born `frontend`, so the roster (`who` / the dashboard) tells you
  which terminal is which. Honored only while the name is free — it never steals an
  active teammate's handle (falls back to `name-2`).
- **Recycling:** "taken" now means *currently active* only, so a closed/idle
  session's handle is reclaimed for the next one. The pool no longer marches
  `ada → … → agent-N` and the `agents` table no longer grows unbounded across
  restarts; a restarted shell with the same `GROUPCHAT_HANDLE` keeps its name.
- The **"an active session keeps its handle for life"** invariant is preserved by a
  TOCTOU-guarded reclaim: the delete re-asserts staleness, so a holder that revives
  mid-reclaim survives and the newcomer retries a different name. Reclaiming the
  lead's handle clears the `meta['lead']` pointer so a name-reuser can't inherit
  leadership. (Caveat: a lead pinned via the `$GROUPCHAT_LEAD` *env var* can't be
  cleared from code.)

## v0.2.0 — 2026-06-07

Everything added since the initial marketplace cut (v0.1.0). All additive and
dependency-free Python 3 stdlib — a room that uses none of it behaves exactly like
the v0.1.0 flat bus.

### Leadership — hub-and-spoke `@human` routing
- A single **lead** is the fleet's one point of human contact. Lead resolution is
  an emergent, deterministic floor (earliest-joined active agent) with an optional
  canonical pointer / `$GROUPCHAT_LEAD` override — a parked/crashed lead fails over
  for free, no election, no single point of failure.
- A worker's `@human` is rewritten to `@<lead>` (a fail-open nudge); the lead's own
  `@human` passes through to the operator.
- **Escalation loop:** a lead with open escalations is not "done" until the
  operator replies `@<lead>` (which batch-clears the queue). Operator tools:
  `chat.py questions` and `chat.py answer <id> "…"`.
- New: `chat.py lead` (`--claim` / `<handle>` / `--release`).

### Cross-CLI bridge (`bridge/`)
- The bus is host-neutral. `bridge/install.py {codex|opencode|generic|claude|all}`
  wires non-Claude agents onto the **same** `chat.db`:
  - **Codex** — `.codex/hooks.json` reuses the identical hook scripts (full
    seamlessness, barrier-parking included).
  - **opencode** — an auto-register plugin + `@mention` nudge + the `AGENTS.md`
    floor it reads natively.
  - **generic** — an `AGENTS.md` block any shell CLI can follow.
- Adapters touch no core files, so leadership, escalation gating, and barrier
  behavior flow to every host for free.

### Dashboard (`dashboard.py`)
- Renders the whole room — roster, conversation, parliament, team-barrier state —
  to a single **read-only** HTML page (`--open`, `--watch N`, `--text`). Exposed in
  Claude Code as `/groupchat:dashboard`.

### Doctor (`chat.py doctor` / `doctor.py`)
- Health check: code integrity, schema, hooks compile + fail-open, barrier smoke,
  and cross-CLI wiring (catches an install-drift `hooks.json` pointing at a moved
  path).

### Governance — constitution P2 + P3
- **P2 — measurement.** `send()` harvests `R<n>` rule cites from chat messages into
  `rule_cites`; `review` ranks live Articles by distinct-sender cite count and flags
  dead letters for repeal (advisory, changes nothing).
- **P3 — advisory parliament.** `motion` / `vote` / `amendments` / `ratify`. The
  vote never enacts a change — a human ratifies from evidence; `ratify` is diff-only.

### Tests
- A dependency-free suite under `tests/` (each isolates via `GROUPCHAT_DIR`):
  transport, barrier, hierarchy, hub-and-spoke, escalation, cross-CLI, dashboard,
  doctor, tokens, hooks, parsing. Run all with `python3 tests/run_all.py`.

### New commands
- `/groupchat:{dashboard,constitution,motion,vote,review}`.

## v0.1.0 — 2026-06-03

The initial marketplace cut.

- **Shared SQLite bus** (`chat.py`, WAL + `busy_timeout`): append-only `messages`
  log, one `agents` row per session, a single monotonic `last_read_id` cursor as
  the entire delivery model.
- **Three Claude Code hooks** that wire it in seamlessly and **fail open**:
  `session_start` (handle + briefing), `user_prompt_submit` (inject new messages),
  `stop` (guard unanswered @mentions).
- **Handles** from a fixed pool; an agent only ever needs to remember its own.
- **Worktree-aware store resolution** — all worktrees of one repo share one
  `chat.db` (anchored to the git common dir).
- **Team barrier** — a finished agent parks (dormant, ~0 tokens) until the whole
  team is done, so a teammate can still @mention it; startup guard + park ceiling
  prevent wedges.
- **Token tracking** — the Stop hook meters each session's transcript into four
  `agents` columns; see `chat.py tokens`.
- **Constitution P1** — a tracked `CONSTITUTION.md` with `constitution init|show|check`.
- **Packaging** — ships via `chat.py install <repo>` and as a Claude Code plugin,
  bundling the usage skill and `/groupchat:{who,chat,inbox,tokens}` commands.
