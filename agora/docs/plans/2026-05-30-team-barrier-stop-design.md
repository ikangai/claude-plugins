# Team-barrier Stop hook — design

**Date:** 2026-05-30
**Problem:** When several Claude instances run the same `/goal` in tandem, an
agent that finishes its slice **exits**. A Claude session is not a daemon — it
only acts on a driving turn (a user prompt, or a Stop hook that *blocks* the
exit). Today `stop.py` blocks only when an unread **@mention** is pending at the
exact moment of stopping. So a finished agent dies, and a teammate who
@mentions it minutes later is talking to a corpse — work stalls waiting for a
reply that can never come.

**Goal:** A finished agent stays alive and keeps watching the chat (idle, near
zero cost) until the **whole team** is done — a barrier — instead of exiting on
its own slice.

## Mechanism

A barrier built into the **Stop hook**, plus a **blocking-poll park** so a
waiting agent costs almost nothing while dormant.

### Verified Claude Code hook facts (this is what makes it work)

- Stop hook default timeout = **600s** (10 min), settable per-command, no hard
  cap.
- On timeout the stop is **allowed** (non-blocking). ⇒ the poll loop must
  **return on its own before the timeout**, never rely on being killed.
- `register()` refreshes `last_seen` on every call; the "active" window is
  **15 min** > a 10-min park ⇒ parked agents stay counted as active.
- `statusMessage` in `settings.json` shows the user a spinner line while the
  hook blocks.

### Stop hook flow

On every Stop (wrapped in the existing fail-open try/except):

1. **Mentions first (unchanged).** If unread messages @mention me → block, hand
   them back, advance cursor. Answering a teammate always wins; never park with
   a question owed.
2. **Auto-done (new).** No mention pending ⇒ the agent is trying to stop ⇒ treat
   that as *"my slice is complete."* Set this agent's `status='done'`. No new
   ritual for agents to remember; "trying to stop with nothing pending" *is* the
   done signal. (A manual `chat.py done` may exist for clarity but isn't
   required.)
3. **Barrier (new).** Compute `team_done` (below).
   - **True** → allow the stop. Whole team tears down together.
   - **False** → **park** via the poll loop instead of exiting.

### The parking loop (approach A — blocking poll)

Set the Stop hook `timeout: 600` in settings. The loop runs a bounded window
(~**570s**, safely under 600) ticking every ~2s. Each tick:

1. **Refresh `last_seen`** (`register`) so the parked agent stays active.
2. **New unread @mention for me?** → stop polling, return
   `{"decision":"block", reason:<messages>}`. Claude wakes, replies in chat,
   hits Stop, re-parks. *This is the whole point.*
3. **Barrier now satisfied?** → return clean (allow stop). Every parked agent
   notices within ~2s of the last teammate finishing → team tears down together.

If neither fires before ~570s, return a terse **re-park block** ("Still waiting
— N teammates active; nothing for you, you may stop."). Claude spends one cheap
turn, tries to stop, re-parks. Idle cost ≈ **1 trivial turn / 10 min**; Claude
is fully dormant (0 tokens) during each sleep — not a spin loop.

**`stop_hook_active` change:** today the hook bails when this flag is set, to
break loops. For re-parking we must keep blocking while the barrier is unmet —
the blocking *sleep itself* is the loop-safety (no tight loop), so we gate on
the **barrier**, not on `stop_hook_active`.

## Barrier definition

```
team_done := startup_guard_satisfied AND (all active agents have status='done')
```

`startup_guard_satisfied` closes the **ragged-startup race** (a fast agent
stopping before slower teammates have even registered, trivially satisfying an
empty barrier and exiting — the same bug, relocated to startup):

- **If `GROUPCHAT_TEAM_SIZE=N` is set** → require `registered_agents >= N`
  *and* all active done. Exact when you know the team size.
- **Else** → require room age `>= 90s` (a `meta` row `room_created` stamped at
  init). Zero-config; covers staggered launches. Cost: a solo/finished run
  waits out the grace window once.

The two are **paired**: honor `GROUPCHAT_TEAM_SIZE` when present, else fall back
to the 90s grace. Common `/goal` use is zero-config; exact when you care.

### Ceiling (safety release)

A mis-set `GROUPCHAT_TEAM_SIZE` (too high) would park everyone with no age
fallback. So a parked agent is released regardless after `MAX_PARK_SECONDS`
(30 min, env `GROUPCHAT_MAX_PARK`) of *continuous* waiting — measured from a
per-session `park:<sid>` timestamp in `meta`, reset whenever the agent wakes to
answer a mention. On release it posts a `system` note so teammates see it left.

## Why the edges are covered

- **Crashed/silent teammate** → ages out of the 15-min active window → drops
  from the barrier → survivors finish. No permanent wedge.
- **Simultaneous finish** → each marks done then checks; the last to commit sees
  all-done and exits clean, the rest release on their next ~2s tick. No
  deadlock.
- **Late joiner** → a new active not-done agent re-opens the barrier; parked
  agents keep waiting. Correct.
- **Solo run** → one agent, barrier trivially all-done (after the guard) →
  exits. No regression beyond a one-time grace wait.

## Files to change

- **`.groupchat/chat.py`** — `team_done(conn)` + barrier helpers; `mark_done`;
  stamp `meta.room_created` at init; optional `done` / `expect N` CLI commands.
- **`.groupchat/hooks/stop.py`** — the 3-branch flow + poll loop; gate on the
  barrier rather than `stop_hook_active`.
- **`.claude/settings.json`** — Stop hook `timeout: 600` + a `statusMessage`.
- **`CLAUDE.md`** — document the barrier behaviour and `GROUPCHAT_TEAM_SIZE`.

## Tunables

| Knob | Default | Note |
|------|---------|------|
| Stop `timeout` | 600s | hook kill line |
| Park window | ~570s | must stay < timeout |
| Poll tick | ~2s | barrier/mention latency |
| Active window | 15 min (existing) | must stay > park window |
| Startup grace | 90s | used when team size unset |
| `GROUPCHAT_TEAM_SIZE` | unset | exact barrier when set (or `chat.py expect N`) |
| `GROUPCHAT_MAX_PARK` | 1800s | ceiling: release after this much continuous waiting |
| `GROUPCHAT_PARK_WINDOW` | 570s | poll window per park (must stay < timeout) |
| `GROUPCHAT_POLL_TICK` | 2s | barrier/mention latency while parked |
