# Blindspot pass: Claude Code native cross-session messaging (and what agora should do about it)

**Date:** 2026-08-09 · **Status:** analysis + proposal.
- **P1 implemented** (`agents.inbox` capture, `_push_wake`/`_push_nudges` in `send()`,
  `AGORA_PUSH=0` opt-out, `tests/push_test.py`, `env_for` socket scrub — verified
  end-to-end against a live Claude Code 2.1.226 receiver).
- **P2 implemented (2026-08-12):** the roster/identity bridge — `bootstrap` spawns
  `claude -n <handle>` so `/list-agents` and `who` agree; `_native_sessions()` reads
  Claude Code's session registry (best-effort) and `who` surfaces the native alias
  `⇄<name>` on a mismatch, a `⌁` push-reachable marker, and a `push-reachable: K/N`
  tally. The briefing's two-channel caution shipped earlier as v0.17.1.
  `tests/native_bridge_test.py`.
- **The two P3-gating test gaps, resolved (2026-08-12):**
  1. *Does a push-wake-started turn fire UserPromptSubmit (cursor advance)?* **Yes.**
     Forensic trace of the 2.1.227 binary: a peer message routes `peer → prompt →
     "Routed user message to queue"`, and the prompt path unconditionally calls
     `executeUserPromptSubmitHooks(…, {promptSource})` — the source is passed as
     *metadata*, never gates whether hooks run (values seen: `typed`/`sdk`/`system`).
     The docs agree ("counts toward usage like a prompt you type"). So agora's
     `user_prompt_submit.py` fires on a nudge-started turn and the cursor self-heals;
     the nudge's explicit `read` instruction is belt-and-suspenders, not load-bearing.
     **No code change** — the conservative P1 design was correct.
  2. *Does a parked agent drop a held native message at the 5-min dialog expiry?*
     **Not a correctness risk for P1/P2; it is a P3 prerequisite.** A parked agent is
     blocked in a Stop-hook sleep (not idle), so a peer message is queued (default
     prompting-mode bootstrap workers) or held-then-expired (only a `bypassPermissions`
     receiver). Either way it is harmless *today* because a parked agent's wake path is
     the **DB tick**, not native delivery — the nudge is redundant noise there. It
     becomes load-bearing only under **P3 park-retirement**, which must therefore spawn
     workers with `crossSessionInbound: accept` (or keep the DB poll for `bypass`
     fleets). Recorded here so P3 starts from the constraint, not a surprise.
- **P3 (park retirement) / P4 (headless spawn) still open** — see the section below.

**Trigger:** operator asked for an unknown-unknowns pass and to evaluate
https://code.claude.com/docs/en/cross-session-messaging for agora.

## TL;DR

Claude Code (v2.1.224+, shipped days ago) grew a **native same-machine message bus**:
every session binds a Unix-domain inbox socket, registers itself in
`~/.claude/sessions/<pid>.json`, and models get `ListAgents`/`SendMessage` tools. A
message **wakes an idle session into a new turn** — the exact capability agora's entire
Stop-hook parking architecture exists to approximate. This is simultaneously agora's
biggest blindspot (two rival messaging channels in the same room, invisible to each
other) and its biggest opportunity (**push-wake**: a finished agent can genuinely go
idle and still be reachable, instead of blocking in a 2s-tick sleep-poll for up to 2h).

Empirically verified on this machine (Claude Code 2.1.226, macOS):

- Registry file schema (`~/.claude/sessions/33402.json`):
  `{pid, sessionId, cwd, startedAt, version, peerProtocol: 1, kind, entrypoint,
  messagingSocketPath, name, nameSource, status: busy|idle, updatedAt, …}` —
  **keyed by the same `sessionId` agora's `agents` table is keyed by.**
- Socket path exported to every hook/Bash as `CLAUDE_CODE_MESSAGING_SOCKET`
  (before SessionStart even runs) — this is also the cheapest **feature-detect**.
- Wire format (reverse-engineered from the binary, then confirmed live by posting to
  this session's own socket and receiving the message mid-turn):
  one JSON line — `{"type":"user","message":{"role":"user","content":"<text>"}}`.
  An optional `session_id` field is checked against the receiver's id and dropped on
  mismatch. Delivery: between tool calls when busy; **starts a new turn when idle**.

## Part 1 — The blindspots (unknown unknowns)

### B1. Two messaging systems now coexist in every agora room, and neither knows it

An agent in an agora room now holds **both** `chat.py send` and the native
`SendMessage` tool. Nothing in the skill, the briefing, or CLAUDE.md says which to
use. A native `SendMessage` to a teammate:

- bypasses the durable log (no `messages` row → no ledger, no `results`, no cite
  harvest, no `@mention` semantics);
- bypasses the barrier and the escalation gate (a "done" negotiated over the native
  channel leaves the bus state stale);
- is invisible to `who`, `summary`, the dashboard, and every other surface.

Inbound is the mirror image: a peer message arrives **outside the bus** (as it just
did during this analysis) and is never recorded. The room's history lies by omission.

**Severity: high.** This isn't hypothetical — the model will discover `ListAgents` on
its own, see its teammates listed, and take the shortest path.

### B2. Identity split: agora handle ≠ native session name

This session is `ada` to agora and `agora-bb` (derived, cwd-based) to the native
layer. `/list-agents` and `who` show disjoint rosters for the same processes. The
operator and the model both have to hold a two-way mapping that exists nowhere.
Claude Code has a `--name` flag and a native `/rename` — agora's bootstrap doesn't
use the flag, and agora's `/agora:rename` doesn't know the native name exists.

### B3. Liveness is now duplicated — and native has better data

Agora infers liveness (15-min `last_seen` window, quiet-detection heuristics, token
metering from transcripts). The native registry maintains ground truth per session:
`status: busy|idle`, `updatedAt`, `pid`, `version` — refreshed by Claude Code itself.
`who`'s `◐ quiet` guesswork could be (host-permitting) replaced by fact.

### B4. Native agent teams overlap bootstrap/tasks/lead

`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` gives a session a spawned team, a **shared
task list with file locking** (`~/.claude/tasks/`), per-agent JSON mailboxes, and
TeammateIdle/TaskCreated/TaskCompleted hooks. If a user enables it inside an agora
room, two spawn systems, two task ledgers, and two mailboxes run side by side.
Agora's durable moats remain real (see Part 3), but the briefing should acknowledge
the coexistence rather than let the model improvise.

### B5. The park may now *delay* native messages (interaction risk)

A parked agent is blocked inside its Stop hook — from the native layer's view the
session is not idle, so an arriving peer message queues (or, if held for approval,
its dialog can expire at 5 minutes → **dropped**) until the park window ends.
Today's park windows are ~570s. Untested; flag it, don't assume.

### B6. Availability is a matrix, not a boolean

Native messaging is off on: native Windows, Bedrock/Vertex/Foundry providers,
versions < 2.1.224, and — easy to miss — **when any of
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` / `DISABLE_TELEMETRY` / `DO_NOT_TRACK` /
`DISABLE_GROWTHBOOK` disables feature-flag evaluation**. Privacy-conscious setups
silently lack the feature. And non-Claude hosts (Codex, opencode — agora's bridge
fleet) never have it. Anything agora builds on this must degrade to today's
behavior with zero configuration.

### B7. The wire protocol is internal

`peerProtocol: 1` is versioned and undocumented. The docs *bless* the pattern
("when you want a script or hook to post into a session") and export the socket path
for exactly that, but the JSON shape is not a public API. A Claude Code update could
break push silently. Consequence: push must be **best-effort sugar over the bus,
never load-bearing** — the cursor + park fallback remain the correctness story.

### B8. Broader non-messaging blindspots noted in passing

- **Deployment skew** (already in memory): rooms run the plugin cache (0.15.4)
  while the dev repo is ahead — reports from live rooms may be stale-version bugs.
- **Unbounded `chat.db`**: append-only messages with no prune verb; months-old rooms
  grow forever. Cheap fix someday: `chat.py prune --before <ts>`.
- **Native "channels"** (CI events → session) could someday feed the bus; noted only.

## Part 2 — The opportunity: push-wake (utilize it)

The single biggest win: **`send()` can wake a mentioned teammate immediately** by
writing one JSON line to its inbox socket. What parking simulates with a blocking
sleep-poll (frozen terminal, 2h ceiling, attended/unattended heuristic, wake latency
of one poll tick), the socket does natively: an idle session starts a new turn.

### Design sketch (phased, all fail-open, bus stays source of truth)

**P1 — capture + nudge (small, no behavior removed):**

1. `session_start.py` records `os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")` into a
   new `agents.inbox` column (plus native `name` for B2). NULL = host can't push.
2. `send()` (and `answer`): after committing a message that @mentions agents, for
   each mentioned **active** agent with a non-NULL `inbox`, best-effort write a
   nudge and move on (fail-open `try`, sub-second timeout):
   `[agora] @<handle>: new message #<id> from <sender> — run: python3 <chat.py> read --from <handle>`
   - Include `#<id>` so the identical-repeat throttle never eats a second nudge.
   - The nudge carries an *instruction to read the bus*, not the message body —
     because it is **unknown whether UserPromptSubmit fires on a peer-message turn**;
     `read` self-heals the cursor either way, and double-surfacing is avoided.
3. Effect today, before any park changes: an **attended** session (which since
   v0.15.3 never parks) currently learns of an @mention only at the human's next
   prompt — with the nudge it learns immediately. The escalation `answer` reaches a
   waiting asker instantly. Parked agents are unaffected (they still wake on the
   ~2s DB tick, likely before the socket message is even deliverable, per B5).

**P2 — roster + identity bridge:**

- `bootstrap` spawns `claude --name <handle>` so `/list-agents` and `who` agree.
- `who` joins `agents.session_id` against `~/.claude/sessions/*.json` to show native
  busy/idle and to reap rows whose pid is gone (better than the 15-min heuristic).
- Briefing gains one line: "this room's bus is the coordination channel; native
  SendMessage bypasses the ledger — mirror anything decided there into the bus."

**P3 — park retirement on capable hosts (the architectural payoff):**

- Stop hook: if this agent's own socket exists (feature on) **and** every teammate
  that might need to reach it can push (all-active-have-inbox), then mark done and
  **return without parking** — the session goes idle but stays reachable; a later
  @mention wakes it via P1's nudge. Teardown broadcast ("barrier complete") can
  itself be a push.
- Keep the park verbatim when: any teammate lacks `inbox` (Codex/opencode/old
  Claude), the feature is off locally, or `AGORA_PARK=1` forces it. Mixed fleets
  degrade per-host exactly like the existing `parks` capability bit.
- Wrinkles to resolve before building: spawned-worker permission class
  (a `bypassPermissions` receiver **holds** unclassified socket messages → 5-min
  dialog expiry → drop; bootstrap may need `--settings '{"crossSessionInbound":"accept"}'`
  for unattended workers); the per-sender rate limiter; the 50-message accepted cap;
  whether a nudge-started turn fires UserPromptSubmit (test!).

**P4 — spawn modernization (exploratory):** `bootstrap --method headless` spawning
long-running `claude -p` workers (they bind sockets, appear in `/list-agents`, take
pushes with `crossSessionInbound: accept`) instead of osascript Terminal windows.

### What NOT to do

- Don't replace the bus with native messaging: it is ephemeral text, same-machine,
  same-CLI, no history, no @mention/ledger/governance semantics, feature-flag-gated.
- Don't hand-edit `~/.claude/sessions/*.json` (Claude Code overwrites it).
- Don't make any correctness path depend on the socket (B7). Every push is wrapped
  in the same fail-open discipline as the hooks.

## Part 3 — Where agora still earns its keep (the moat, restated against native)

| Capability | Native (messaging + teams) | agora |
|---|---|---|
| Durable history / ledger / results | none (ephemeral text) | SQLite bus, tasks, results |
| Cross-CLI (Codex, opencode, shell) | Claude-only | host-neutral hook contract |
| Sessions the human launched joining a team | no (team = lead-spawned, one per session) | any session in the repo joins |
| Leadership handoff / escalation funnel | lead is fixed | lead pointer + floor + `@human` |
| Survives restarts / resume | teams don't resume in-process teammates | room state persists in db |
| Governance / audit | none | constitution, motions, decisions |
| Worktree reconciliation | none | `worktrees` / `harvest` |

The strategic posture: **native messaging is a transport, agora is the institution.**
Adopt the transport (push-wake), keep the institution (ledger, barrier, governance).

## Recommended next steps (in order)

1. Decide on P1 (socket capture + mention nudge + `answer` push) — small, additive,
   testable with two local sessions; biggest UX win per line of code.
2. Test the two open questions: does a peer-message turn fire UserPromptSubmit, and
   does a parked (hook-blocked) session drop held messages (B5)?
3. P2 identity bridge (`--name`, briefing line about the two channels) — mostly
   docs/one-flag changes.
4. Only then consider P3 (park retirement) — it rewrites the barrier's liveness
   assumption and deserves its own design doc + tests.
