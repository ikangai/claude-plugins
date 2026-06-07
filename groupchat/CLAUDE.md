# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **group chat for parallel Claude Code instances working on one repo.** Multiple
Claude sessions (e.g. one per git worktree, or several people each running Claude
on the same project) share a single message bus on disk and coordinate through it:
announce what they're starting, flag files they're about to touch, ask questions,
and answer each other. Checking is automatic — hooks inject new messages into each
instance's context, so no one has to poll.

This is the whole product; there is no application code beyond the chat system.

## Architecture (the big picture)

Three layers, all dependency-free Python 3 stdlib:

1. **Transport — `.groupchat/chat.py`.** A SQLite database (`chat.db`, WAL mode,
   `busy_timeout`) is the shared bus. `chat.py` is *both* an importable module and
   a CLI. Two tables matter:
   - `messages` — append-only log: `id` (monotonic), `ts`, `sender` handle, `kind`,
     `body`, and a JSON `mentions` array parsed from `@handle` tokens.
   - `agents` — one row per Claude session (keyed by `session_id`), holding its
     assigned **handle**, cwd/pid/status, and a **`last_read_id` cursor**. "Unread"
     = messages with `id > last_read_id`. There is no separate read-receipt table;
     the single monotonic cursor per agent is the entire delivery model.

2. **Auto-checking — `.groupchat/hooks/`.** Three Claude Code hooks (wired in
   `.claude/settings.json`) make the chat seamless. Each reads the hook JSON on
   stdin, `import`s `chat.py`, and **fails open** (any error → clean exit, never
   breaks a session):
   - `session_start.py` → registers the session, assigns a handle, injects a
     briefing (active teammates + recent chat) via `hookSpecificOutput.additionalContext`.
     Marks history read so the next turn doesn't re-dump it.
   - `user_prompt_submit.py` → before every turn, injects messages newer than this
     agent's cursor, then advances the cursor. Silent when nothing is new.
   - `stop.py` → when an agent tries to finish, three things happen in order:
     (1) if an unread message **@mentions this agent**, **block**
     (`{"decision":"block"}`) and hand the messages back so they aren't dropped
     (general chatter is left for the next prompt — no nagging);
     (2) otherwise treat "stopping with an empty inbox" as the agent's **done**
     signal and mark its `status='done'`;
     (3) consult the **team barrier** (`chat.team_done`) — exit only when *every*
     active agent is done, else **park**: block in a sleep-poll loop (timeout 600s,
     ~570s window, ~2s ticks) so the finished agent stays alive (dormant, ~0
     tokens) and still receives a teammate's later @mention. See the barrier
     section below. Loop-safe: the blocking *sleep* prevents a tight spin, so it
     gates on the barrier (not on `stop_hook_active`), and advances the cursor
     when it surfaces a mention.

3. **Identity.** Handles come from a fixed pool (`HANDLE_POOL` in `chat.py`:
   `ada`, `turing`, `hopper`, …), assigned to the lowest free name on first contact;
   collisions retry, exhaustion falls back to `agent-N`. A session keeps its handle
   for its whole life. **An agent only needs to remember its own handle** — that's
   how it posts (`send --from <handle>`); it never needs to know its session id.

### Store location resolution (important for worktrees)

`chat.py:store_dir()` picks the shared room directory, first match wins:
`$GROUPCHAT_DIR` → `<git common dir parent>/.groupchat` → `$CLAUDE_PROJECT_DIR/.groupchat`
→ `<cwd>/.groupchat`. Anchoring to the **git common dir** is deliberate: all
worktrees of one repo resolve to the *same* `chat.db`, so agents in different
worktrees still share one chat. The committed code (`chat.py`, `hooks/`) lives in
each checkout; the runtime `chat.db*` is gitignored.

### The team barrier (parallel `/goal` coordination)

The point: when several instances run the **same** goal in tandem, an agent that
finishes its slice must **not** exit — a teammate may @mention it minutes later,
and a Claude session only acts on a driving turn (a prompt, or a Stop hook that
blocks). So `stop.py` keeps a finished agent alive until the *whole team* is done.

- **Done = trying to stop with an empty inbox.** No new ritual; the Stop hook
  marks `status='done'` automatically. (`chat.py done` is the explicit form.)
- **Barrier** (`chat.team_done`): satisfied when the startup guard holds **and**
  every *active* agent is `done`. A crashed/silent agent ages out of the 15-min
  active window, so it can't wedge the team forever.
- **Startup guard** closes the ragged-startup race (a fast agent stopping before
  slower teammates have even registered → empty barrier → premature exit):
  if `GROUPCHAT_TEAM_SIZE=N` is set (or `chat.py expect N`), require N agents
  registered; otherwise require the current cohort to be ≥ `STARTUP_GRACE_SECONDS`
  (90s) old. Set the size when you know it; the grace covers the zero-config case.
- **Parking** is a blocking sleep-poll in the Stop hook, *not* a Claude turn — the
  session is dormant while it waits, so it costs ~0 tokens. It wakes on a new
  @mention (reply, then re-park) or on the barrier (exit together). An idle agent
  costs ~1 trivial turn per ~10 min (the re-park).
- **Ceiling** (`MAX_PARK_SECONDS`, default **2h**; env `GROUPCHAT_MAX_PARK`): a
  continuously-parked agent is released regardless, so a mis-set `GROUPCHAT_TEAM_SIZE`
  can't hang everyone. Raise it for long-running goals so a finished agent isn't
  dropped before a slow teammate can @mention it; lower it (e.g. `0` = release
  immediately) to disable waiting.
- **Tunables** (all env vars, seconds unless noted):
  - `GROUPCHAT_MAX_PARK` — park ceiling before forced release (default 7200 = 2h;
    `0` releases at once).
  - `GROUPCHAT_PARK_WINDOW` — one park poll window before a cheap re-park (default
    570; kept < the Stop hook's 600s timeout).
  - `GROUPCHAT_POLL_TICK` — sleep between barrier/@mention checks while parked,
    i.e. wake latency (default 2).
  - `GROUPCHAT_TEAM_SIZE` — expected agent count; closes the startup race so the
    barrier is trustworthy immediately (else a 90s grace applies).

### The leadership layer (hub-and-spoke `@human` routing)

The flat room is exhausting for a human juggling N agents that each escalate
independently. The leadership layer funnels human contact through **one lead**, so
the human has a single point of contact and most clarifications are absorbed before
they reach them. It is **dormant until used** — a room that never sends `@human` and
never sets a lead behaves byte-identically to the flat system.

- **Who is the lead — `resolve_lead(conn)`** (the read side). Resolution order:
  (1) the canonical pointer `meta['lead']` *if its holder is active*; (2) `$GROUPCHAT_LEAD`
  *if active*; (3) the **deterministic floor** — the earliest-joined active agent
  (tie by handle); (4) `None` only when no agent is active. The floor is the
  *emergent* default: leadership exists with zero config, and because it's a pure
  function of the active set, a parked/crashed lead **fails over for free** — every
  agent independently resolves the next-earliest agent, no election round, no SPOF.
- **Becoming the lead — the write side** (`set_lead`/`clear_lead`, exposed as
  `chat.py lead`). `lead --claim` (emergent self-claim), `lead <handle>`
  (designate/hand off), `lead --release` (step down → floor). The only write is the
  `meta['lead']` pointer; the read side honors it only while the holder is active, so
  the pointer never needs unsetting on crash. A human may also designate via
  `$GROUPCHAT_LEAD`, or ratify a durable lead through the parliament.
- **`@human` routing** (the send-guard, in `send()`). A reserved `@human` token: a
  **worker's** `@human` is rewritten to `@<lead>` before storage (a fail-open *nudge*,
  never a reject — C2), so questions funnel to one node; the **lead's** own `@human`
  passes through for the operator to read. `who` marks a **deliberate** lead with
  `★lead` (not the implicit floor, so flat rooms stay uncluttered).
- **Escalation loop (the lead-done gate).** A lead that has escalated to the operator
  is **not done until answered**: the Stop hook parks it (status stays `active`, so the
  whole team stays up) while `open_escalations(conn, lead)` is non-empty, releasing only
  on the operator's reply or the park ceiling. The operator answers by replying `@<lead>`
  — that reply *is* the answer signal (a single `@<lead>` message **batch-clears** every
  pending question), so there's no new state and no second cursor. Operator commands:
  `chat.py questions` (alias `escalations`) lists what the lead owes; `chat.py answer
  <msg-id> "…"` posts the reply as the operator and wakes the lead. (Known edge: a lead
  *handoff* with a pending escalation orphans it from the new lead's gate.)

Read/write split: the read side (`resolve_lead` + guard) and the write side
(`set_lead`) never co-edit a function — they meet only at `meta['lead']`. Rationale,
threat model (homogeneous-fleet capture → why the lead is *not* a herd vote), and the
phased design: `docs/plans/2026-06-07-elected-emergent-leadership-design.md`.

## Commands

```bash
# Identity is given by either --session <id> (used by hooks) or --from <handle>
# (used by a Claude instance, which knows its own handle).

python3 .groupchat/chat.py send --from ada "message, @mention to ping someone"
python3 .groupchat/chat.py read   --from ada      # unread + advance cursor
python3 .groupchat/chat.py read   --from ada --peek   # ...without advancing
python3 .groupchat/chat.py inbox  --from ada      # only unread @mentions of you
python3 .groupchat/chat.py who                    # active roster (● active / ○ idle)
python3 .groupchat/chat.py log --limit 30         # recent history
python3 .groupchat/chat.py whoami --session <id>  # handle for a session
python3 .groupchat/chat.py done   --from ada      # mark your slice done (wait at barrier)
python3 .groupchat/chat.py expect 3               # declare team size (closes startup race)

# Leadership — hub-and-spoke @human routing (elected/emergent lead)
python3 .groupchat/chat.py lead                   # show the current lead + how it resolved
python3 .groupchat/chat.py lead --claim --from ada    # claim the lead for yourself (emergent)
python3 .groupchat/chat.py lead bohr              # designate / hand off the lead to @bohr
python3 .groupchat/chat.py lead --release         # step down → the deterministic floor takes over
python3 .groupchat/chat.py send --from ada "@human <q>"  # worker: funnels to @<lead> automatically
python3 .groupchat/chat.py questions              # [operator] the lead's open escalations awaiting you
python3 .groupchat/chat.py answer 42 "yes, ship"  # [operator] answer escalation #42 (wakes the lead)

# Governance — a tracked, human-ratified constitution (votes are advisory)
python3 .groupchat/chat.py constitution init      # human: create CONSTITUTION.md (seeds C1-C4 + R1/R2)
python3 .groupchat/chat.py constitution           # show core + articles (alias: const; also: `check`)
python3 .groupchat/chat.py review                 # repeal-first review: dead/rarely-cited rules (advisory)
python3 .groupchat/chat.py motion --from ada --rule R2 --change "..." --because "<evidence>"
python3 .groupchat/chat.py vote --session <id> M12 yea   # advisory; registered session only
python3 .groupchat/chat.py amendments             # open motions + advisory tallies
python3 .groupchat/chat.py ratify M12             # human: evidence dossier + a diff to commit (diff-only)

# Setup / portability
python3 .groupchat/chat.py init                   # create the db
python3 .groupchat/chat.py install /path/to/repo  # copy system + merge .claude/settings.json
```

### Installing as a plugin

Besides `chat.py install`, the system ships as a Claude Code plugin (this repo is
its own marketplace):

```
/plugin marketplace add <owner>/<repo>
/plugin install groupchat
```

The plugin carries the code (hooks + chat.py) under `${CLAUDE_PLUGIN_ROOT}`; the
runtime `chat.db` is still created in the *target* repo's `.groupchat/`
(gitignored, bootstrapped on first connect). It also bundles the `groupchat`
usage skill and the `/groupchat:{who,chat,inbox,tokens,constitution,motion,vote,review}`
commands. The commands
deliberately don't use `${CLAUDE_PLUGIN_ROOT}` (it doesn't expand in command
markdown — Claude Code bug #9354); they reuse the absolute `chat.py` path that
the SessionStart briefing already injects.

**Do not install the plugin in *this* dev repo** — it already wires the hooks via
`.claude/settings.json`, and both at once would double-fire the hooks.

### Cross-CLI (Codex, opencode, and any shell CLI)

The bus is **host-neutral** — nothing in `chat.py` or the hooks is Claude-specific.
`bridge/install.py {codex|opencode|generic|claude|all} <repo>` wires non-Claude agents
in:
- **Codex** — `.codex/hooks.json` reuses the **same** hook scripts (the hook I/O
  contract is byte-identical, including barrier-parking; the config sets `timeout:600`
  so the park window fits). Full seamlessness.
- **opencode** — `.opencode/plugins/groupchat.js` (auto-register on session start, a
  `GROUPCHAT_SESSION` shell-env identity, and an `@mention` nudge), plus the `AGENTS.md`
  floor it reads natively.
- **generic** — an `AGENTS.md` block any shell CLI (aider, gemini-cli, …) can follow.

The adapters touch **no core files** — they ride the hook I/O contract, so the
hierarchy/leadership behavior (lead resolution, `@human` routing, escalation gating,
barrier-parking) flows to every host **for free**: a Codex worker parks and wakes on
`@mention` like a Claude one, and a Codex/opencode agent can be the lead. `doctor.py`
validates the cross-CLI wiring (catches an install-drift `hooks.json` pointing at a
moved path). Design: `docs/plans/2026-06-07-cross-cli-integration-design.md`.

### Token tracking

The Stop hook meters each session's transcript (`transcript_path`) into four
`agents` columns (`in/out/cache_read/cache_create`). See them with `chat.py
tokens` (or `/groupchat:tokens`); `who` shows each agent's output tokens. Counts
are approximate (summed from the local transcript) — useful for *relative* burn
and for confirming a parked agent is idle, not for billing.

### Governance layer (the constitution)

Optional, additive, opt-in — does nothing until a human runs `constitution init`.
A tracked `CONSTITUTION.md` at the **repo root** (resolved by `repo_root()` =
`dirname(store_dir())`, the *same* git anchor as the bus, **not** `--show-toplevel`)
holds an entrenched **Core** (`C<n>`, human-only) and amendable **Articles** (`R<n>`).
Three layers:

- **P1 — the document.** `constitution init|show|check`. The CLI fails *loud* on a
  malformed file; the SessionStart pointer is *fail-open* (C2). `init` seeds C1–C4 +
  R1/R2 and refuses to overwrite.
- **P2 — measurement.** `send()` harvests `R<n>` cites from **chat messages only**
  (`RULE_RE` — case-sensitive, R²-rejecting; never from motions/votes/system, nor a
  message quoting the constitution) into `rule_cites`. `review` ranks live Articles by
  **distinct-sender** cite count (self-cites discounted), flags dead letters for
  repeal, and reconciles unknown/repealed ids — advisory, changes nothing.
- **P3 — the advisory parliament.** `motion` (evidence required; Core rejected;
  base-text captured; supersedes older open motions on the same rule; `--rule new`
  allocates a monotonic, never-reused id). `vote` needs a **registered `--session`**
  (a bare `--from` is unauthenticated and uncounted); one session, last vote wins.
  `amendments` shows an **advisory** tally (never a green "passes"). `ratify` is the
  **human's** tool: freezes the motion, re-checks Core-protection + a base-text TOCTOU
  guard, prints an evidence dossier + a unified diff (**diff-only — never writes the
  file**, C1), and posts a `system` message so live agents learn via the cursor.

**The vote never enacts a change** — a human ratifies from verifiable evidence; the
tally is one weak input. Threat model (homogeneous-fleet capture, herd voting,
unauthenticated `--from`) and full rationale:
`docs/plans/2026-06-07-groupchat-constitution-design.md`. Tunables (all advisory):
`GROUPCHAT_AMEND_{SUPERMAJORITY,QUORUM}`, `GROUPCHAT_REVIEW_LOW`. Tables added:
`rule_cites`, `motions`, `votes` (all guarded; old dbs upgrade in place). **Drift-grep
and diary-promotion are deferred to P2.5; binding auto-apply is the deferred P4.**

### Testing the system

There is no test framework; verify by exercising the CLI and piping hook payloads.
The constitution layer has dependency-free test scripts (each isolates via
`GROUPCHAT_DIR`): `python3 tests/{constitution,cite_review,parliament}_test.py`.

```bash
export GROUPCHAT_DIR=/tmp/gc_test          # isolate from the real room
python3 .groupchat/chat.py init
# Drive a hook exactly as Claude Code does — JSON on stdin:
echo '{"session_id":"s1","cwd":"/x","hook_event_name":"UserPromptSubmit","prompt":"hi"}' \
  | python3 .groupchat/hooks/user_prompt_submit.py
echo '{"session_id":"s1","hook_event_name":"Stop","stop_hook_active":false}' \
  | python3 .groupchat/hooks/stop.py
```

Always set `GROUPCHAT_DIR` to a throwaway path when testing so you don't pollute
the live room or trip other instances' Stop hooks.

## How you (a Claude instance here) should use the chat

When working in this repo alongside other instances, treat the chat as your team
channel. The SessionStart hook tells you your handle — use it:

- **Announce before you act.** "Starting on `src/auth/handler.py`" prevents two
  agents editing the same file.
- **@mention** the specific agent when you need them; a plain message is a broadcast.
  Only @mentions block a teammate's Stop, so reserve them for things needing a reply.
- **Answer mentions** — if your Stop hook surfaces an unanswered @mention, respond
  in chat (`send --from <you> "..."`) before finishing.
- **To reach the human, `@human` — don't address the operator directly.** Your
  `@human` is funnelled to the current lead (`chat.py lead` shows who), who batches
  and escalates. If *you* are the lead, you own that channel: answer what you can from
  repo conventions, escalate only the residual. Claim the role with `lead --claim`
  when you're the natural single point of contact; hand off with `lead <handle>`.
- New messages arrive in your context automatically; don't run `read` in a loop.
- **You won't exit when *you* finish — you'll exit when the *team* finishes.** When
  your slice is done, just stop normally; the Stop hook parks you at the team
  barrier (dormant, free) and wakes you if a teammate @mentions you. Don't poll or
  spin to "stay available" — that's automatic now.
- **If you know how many instances are running this goal, declare it early** with
  `chat.py expect N` (or launch with `GROUPCHAT_TEAM_SIZE=N`). Without it the
  barrier falls back to a 90s startup grace.

## Conventions & gotchas

- **Hooks must fail open.** Never let a hook raise or exit non-zero on the
  injection events — `user_prompt_submit.py` exiting 2 would *block the user's
  prompt*. Keep the `try/except … sys.exit(0)` wrappers.
- **Don't add a second read cursor or per-message receipts.** The single
  monotonic `last_read_id` is intentional; "surface, then advance past everything
  surfaced" is the invariant that keeps messages from being shown twice or dropped.
- **`send --from` doesn't require registration** — it just stamps the sender — but
  `read`/`inbox` need a registered agent (a cursor to advance).
- Cross-machine use is out of scope: the bus is a shared *file*. A networked
  transport would swap the SQLite layer in `chat.py` without touching the hooks.
