# Release notes

All notable changes to **groupchat** — the coordination bus for parallel AI
coding-agent sessions on one repo. Published as a Claude Code plugin in the
`ikangai/claude-plugins` marketplace.

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
