# Gap analysis — seamless multi-Claude-instance coordination

*2026-06-22. Method: 8 code-grounded gap lenses, each finding independently
verified against the source, then synthesized (33 agents). Scope: what's missing
for several Claude Code sessions a user spins up in one repo to coordinate
seamlessly — and whether Claude should spawn instances on its own.*

## Verdict

**A single Claude in the room is essentially seamless and was just hardened. The
system is a great chat *room* — it is not yet a *coordinator*.** Everything an
orchestrator needs is missing: there is no shared goal, no who-owns-what map, no
per-agent task assignment, no structured result fan-in, and no control plane
(pause / redirect / dismiss / kill). So divide-and-conquer still requires a human
alt-tabbing to each terminal to type tasks by hand. The single biggest missing
piece is **a durable work-division + assignment primitive on the bus.**

## What's already seamless (don't rebuild these)

- **Auto-delivery.** SessionStart briefing + UserPromptSubmit injection + Stop-block
  on @mention mean nobody polls; the single `last_read_id` cursor never double-shows
  or drops a message.
- **The team barrier.** A finished agent parks dormant (~0 tokens) until the whole
  team is done, with a startup guard, solo settle grace, declared-size time fallback,
  15-min crash ageout, and a 2h ceiling — the deadlock class is handled.
- **Identity.** Handle pool + recycling, `GROUPCHAT_HANDLE` at launch, `/rename` in
  place — a self-identifying, restart-stable roster.
- **Leadership / `@human` routing.** Emergent floor-lead with free failover, worker
  `@human` funneled to the lead, the lead-done escalation gate, operator
  `questions`/`answer` — N escalators collapse to one human contact.
- **Worktree isolation** (`bootstrap --worktree`), **cross-CLI bus** (Codex reuses
  the byte-identical hooks; opencode/generic adapters), **governance** (constitution),
  and **read-side observability** (`who`/`log`/`tokens`/dashboard).
- **The dormant-until-used discipline** itself — the right substrate to add
  coordinator features onto without disturbing the flat case.

## The gaps (ranked by impact on seamlessness)

### Critical — the chat-room→coordinator gap
1. **No task queue / assignment primitive.** Nothing represents open vs claimed vs
   done work. An agent learns its slice only when a human types it into that window;
   two agents can independently grab the same task. `spawn_agents` broadcasts **one
   identical `--prompt` to every handle** — an orchestrator cannot deal out distinct
   work. *Fix:* a dormant `tasks` table (`id, title, owner, status, path-glob`) +
   `task add/claim/list/done`, surfaced in the briefing and `who`.
2. **No control plane.** Steering is cooperative-only: you `@mention` and hope the
   next driving turn picks it up; a looping/wedged agent is unreachable. No
   `direct @h`, no `@all` broadcast, no `stand-down`/`disband`, no `dismiss` (and the
   symmetric barrier means an orchestrator that stays active **wedges its own finished
   workers** up to the 2h ceiling). `pid` is captured but never used. *Fix:* express
   control as bus state the hooks already read — `direct`, an `@team` token, a
   `standdown` meta flag the park loop honors, a lead-gated `dismiss`.

### High — fan-in, context, and collision
3. **No structured result collection.** No worker→spawner return channel; "results"
   are free-text chat. The orchestrator must prose-grep `log`. *Fix:* a `kind='result'`
   message + `results` query keyed by handle; a read-only `summary` digest.
4. **No shared goal/plan object.** A joiner inherits only the last 15 chat lines;
   bootstrapped agents join idle with zero task context. *Fix:* a dormant `goal` meta
   key (like `lead`/`team_size`), auto-set by `bootstrap`, surfaced in the briefing.
5. **No who-owns-what / per-agent current-task field.** The roster shows liveness +
   tokens but never *what* each instance is doing; a scrolled-off "starting on X" is
   invisible to a late joiner. *Fix:* a `focus` field set via a new verb (NOT the
   barrier `status` column), shown in `who`/dashboard/briefing.
6. **No file-claim / soft-lock ledger, and bootstrap's *default* is the
   highest-collision config** (shared cwd, no worktree, no per-agent task). Worktree
   isolation exists but is opt-in and never applies to hand-launched instances sharing
   a tree. *Fix:* cheap now — a "you share a working tree with @X" briefing line;
   later — a dormant `claims` table + optional fail-open PreToolUse hook.

### Medium — robustness & correctness
7. **No stuck / looping / silent-but-alive detection** — only the binary 15-min
   ageout. *Fix:* last-chat-age (free) then token-rate; light the amber dot.
8. **Escalation orphaned on lead handoff *and* rename** — a question to the human
   silently vanishes (rename breaks the gate, not just observability). *Fix:*
   re-escalate to the new lead / union `lead_history` open queues.
9. **Mixed-fleet done-signal gap** — opencode/generic agents never mark `done`, so
   they can hold a Claude team at the barrier. *Fix:* call `done` from the adapters,
   or a done-capability flag so `team_done` gates only hook-capable agents.
10. **No branch reconciliation for `--worktree` runs** — each agent lands on its own
    `groupchat/<name>` branch with no collect/merge flow. *Fix:* a read-only
    `worktrees`/`harvest` (ahead/behind + diffstat + suggested merge order, diff-only).

## The headline question — should Claude spawn a Claude on its own?

**Qualified yes.** Mechanically possible today; operationally reckless until a small
safety/handoff layer exists — and, crucially, **the right tool for most cases is the
native Agent/Task or Workflow primitive, not a separate session.**

- **Works today:** Claude can run `chat.py bootstrap N [--prompt …] [--worktree]` via
  Bash to open real sessions; they register, join, optionally get a worktree, park at
  the barrier, and are `@mention`-able. Cooperative redirection of a *responsive*
  agent already works (an `@mention` blocks its Stop and rides its cursor).

- **Decision rule (the important part):** use the **native Agent/Task tool or a
  Workflow** for tightly-scoped *fan-out-then-join that returns structured results
  within this context* — cheaper, carries the goal in-process, returns a structured
  result, no terminal/worktree overhead. Reach for a **groupchat session only** when
  the worker must (a) outlive a single turn, (b) run in its own context window /
  terminal a human can watch and steer, (c) edit files in an isolated worktree, or
  (d) stay reachable for a later `@mention` / barrier / leadership. *"Spawn subtasks,
  get answers back, done" → native. "A persistent peer that co-evolves the repo
  alongside me and the human" → groupchat.* Building groupchat fan-in is about
  reclaiming results for the cases where separate sessions are genuinely warranted —
  **not** a reason to prefer them over the native tools.

- **What's missing for safe autonomous spawning:** per-agent assignment (today: one
  prompt for all); structured result fan-in; a control plane (`dismiss` so the
  orchestrator isn't wedged on its own workers; `standdown`); **a recursion/runaway
  guard** (`BOOTSTRAP_MAX=8` caps one call, but a spawned agent inherits the full CLI
  + the skill that advertises bootstrap — no spawn-depth, lineage, or global
  live-fleet ceiling, so recursive fan-out is unbounded); stuck-detection; and
  decision-rule docs so an agent doesn't reinvent the Agent tool.

- **Design sketch (additive, dormant, fail-open):** (1) **Spawn guard** —
  `GROUPCHAT_SPAWN_DEPTH`/`SPAWNED_BY` threaded through `_spawn_command`; `bootstrap`
  refuses beyond max depth (1–2) and a global-fleet ceiling; record lineage on the
  agent row. (2) **Per-agent assignment** — `bootstrap ada:'do X' turing:'do Y'` to
  resolved handles, and/or `assign <handle> '…'` as a `kind='chat'` @mention the
  briefing surfaces as "Your assignment". (3) **Result fan-in** — `kind='result'` +
  `results`. (4) **Dismiss** — a lead-gated release the park loop reads to drop a
  handle from the barrier so the orchestrator keeps going. (5) **Decision-rule docs**
  in SKILL.md + team.md.

- **Risks:** runaway recursion (the guard is genuinely absent); self-wedge (no
  `dismiss` yet); cost opacity (more idle windows, no stuck-detection); reinventing
  the native tools; silent clobbering in the shared-tree default; OS-bound,
  PID-reuse-prone `kill`.

## Roadmap

- **Phase 1 — make the coordinator real:** `tasks` table + `task add/claim/list/done`;
  per-agent bootstrap prompts; `assign`; a `goal` meta key auto-set by bootstrap.
  **✅ Landed 2026-06-23** — the work-division layer (atomic claim, durable+notified
  `assign`, shared `goal`, `name:'prompt'` bootstrap specs, `who`/briefing surfacing),
  all dormant-until-used; covered by `tests/tasks_test.py`. See the "work-division
  layer" section of `CLAUDE.md`.
- **Phase 2 — close the fan-in loop:** `kind='result'` + `results`; read-only
  `summary`; read-only `worktrees`/`harvest` (diff-only).
- **Phase 3 — control plane + safe autonomous spawn:** spawn-depth/lineage + fleet
  ceiling; `standdown`/`dismiss`; `direct`/`@team`; optional guarded `kill`.
- **Phase 4 — collision-safety & observability defaults:** `focus` field; shared-cwd
  warning; `claims` table + optional PreToolUse hook; make `/groupchat:team` default
  `--worktree` for parallel-edit teams; stuck/silent detection → amber dot.
- **Phase 5 — correctness & mixed-fleet:** fix escalation-orphan on handoff *and*
  rename; mixed-fleet `done`; decision-rule docs.

## Quick wins (small, high-leverage, closeable now)

- `direct <handle> '…'` — sugar over `send(@h, …)` after the active-set check that
  already exists; the command is already printed in `answer`'s error string. **(S)**
- `standdown`/`disband` — a timestamped `meta` flag the existing park loop checks
  alongside `team_done`; releases parked agents within one `POLL_TICK`. **(S/M)**
- Per-agent bootstrap prompts (`ada:'…' turing:'…'`) — closes the single biggest
  manual-glue step in divide-and-conquer; keep identical-prompt as the default. **(M)**
- Spawn-depth/lineage env + ceiling check in `bootstrap` — the backstop that makes
  autonomous spawning safe to even consider. **(S)**
- Shared-cwd warning + last-chat-age in the briefing/`who` — pure reads, surface the
  invisible clobber risk and a stuck agent. **(S)**
- Decision-rule section in SKILL.md + team.md (session vs native Agent/Workflow) —
  pure docs; makes autonomous spawning judicious. **(S)**
- Document `send --from human` as the operator write channel (dashboard says
  read-only). **(zero code)**
