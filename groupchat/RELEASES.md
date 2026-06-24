# Release notes

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
