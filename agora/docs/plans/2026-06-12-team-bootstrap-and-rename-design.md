# Team bootstrap + runtime rename — design

*2026-06-12*

## Problem

Standing up a parallel-agent team is manual: the human opens N terminals by hand
and either lets the pool auto-name each session or remembers to prefix
`GROUPCHAT_HANDLE=…`. And once a session is running there is **no way to rename
it** — the handle it got at launch is the handle it dies with. Two gaps:

1. **Bootstrap.** One command should spawn the rest of the team — open other
   Claude instances and map each to a free team-member handle — and, when the room
   is empty, ask the human how many teammates the repo needs.
2. **Rename.** A `/rename` command should let a running session change its handle,
   so a roster of `ada, turing, hopper` can become `frontend, backend, reviewer`
   without restarting.

Both must stay true to the system's invariants: dependency-free stdlib, additive,
fail-open, and **dormant when unused** (a repo that never bootstraps or renames
behaves byte-identically to today).

## Decisions (from the human)

- **Spawn mechanism: Terminal.app via `osascript`** — one new window per agent,
  each running `claude` in the repo. (`tmux` and `print` kept as alternative
  `--method`s; `print` is the always-safe fallback.)
- **Spawned agents launch idle** — they join the chat (register + briefing) and
  wait at their REPL for the human to direct them. No seeded prompt by default.
- **Auto-name from the handle pool** — `bootstrap 3` → the next three free pool
  names. Explicit names override: `bootstrap frontend backend qa`.

## Feature 1 — runtime rename

### Core: `rename_agent(conn, session_id, new_handle) -> (old, new)`

Mirrors `register`'s identity rules so renames can't violate the handle invariants:

- **Sanitize** `new_handle` with the same rule as `_assign_handle`
  (`re.sub(r"[^a-z0-9_-]", "", lower)`); empty result → error.
- **Idempotent**: new == current → no-op, return `(cur, cur)`.
- **Reserved** (`human`) → reject.
- **Collision**: if another row holds `new`:
  - held by an **active** agent → reject (`handle in use`); the human picks
    another. (Unlike auto-assign we do *not* silently suffix `-2`; an explicit
    rename should be honoured or refused, not quietly altered.)
  - held by an **inactive** agent → **reclaim**: the same TOCTOU-guarded
    `DELETE … WHERE last_seen < cutoff` as `register`, so a holder that revived
    mid-reclaim keeps its name and the rename surfaces a clean "taken" error.
- `UPDATE agents SET handle = ? WHERE session_id = ?` (catch `IntegrityError` from
  a concurrent grab → clean error).
- **Leadership follows the rename**: if `meta['lead']` pointed at the old handle,
  repoint it at the new one (the pointer stores a handle, so a rename would
  otherwise orphan the lead until the floor re-elected).
- Post a `system` message (`old → new`) so teammates learn the rename through the
  normal cursor (kind=`system` carries no mentions → no nagging, no barrier gate).

### CLI: `chat.py rename [--from H | --session S] <new>`

Resolves the acting agent via the shared `_resolve_for_cli`, renames, and prints
`renamed: old → new` plus a reminder to post as `--from <new>` from now on. The
hooks key off `session_id`, so the read cursor, token counters, and message
delivery are untouched by the handle change.

### Command: `/groupchat:rename <new-name>`

Markdown command that runs `<cli> rename --from <your-handle> "$ARGUMENTS"` (CLI
path from the SessionStart briefing, the same convention every other command uses)
and confirms the new identity.

## Feature 2 — team bootstrap

### Spawner: `spawn_agents(names, cwd, method, prompt, dry_run) -> [result]`

Per name, build the inner shell command
`cd <shlex-quoted cwd> && GROUPCHAT_HANDLE=<name> <claude>` (+ optional
`<shlex-quoted prompt>`), then:

- **`terminal`** (default on macOS): wrap the inner command in
  `tell application "Terminal" to do script "<applescript-escaped inner>"` and run
  it with `osascript` (no shell → only AppleScript-level escaping of `\` and `"`).
  A final `activate` brings Terminal forward. Non-darwin → error pointing at
  `--method print`.
- **`tmux`**: first name → `tmux new-session -d -s groupchat -n <name> '<inner>'`,
  rest → `tmux new-window -t groupchat -n <name> '<inner>'`; print
  `tmux attach -t groupchat`.
- **`print`**: spawn nothing; return the runnable command lines for the human to
  paste. Always-safe fallback; also what `--dry-run` uses for any method.

`claude` is resolved with `shutil.which("claude") or "claude"` so a login shell
with a different PATH still finds it. `cwd` defaults to the invoking directory (the
repo/worktree the human is in) — the spawned sessions resolve the *same* `chat.db`
via the git-common-dir anchor regardless, so they always share the room.

### Name resolution + safety

- One numeric positional → count → `_pick_free_handles(conn, n)` walks
  `HANDLE_POOL` skipping the active/reserved/already-picked set, then `agent-N`.
- Word positionals → explicit names, each sanitized and collision-suffixed against
  the live set (so `bootstrap ada` when `ada` is active yields `ada-2`).
- Cap at `BOOTSTRAP_MAX` (8) unless `--force`, so a fat-fingered `bootstrap 50`
  can't open 50 windows.

### CLI: `chat.py bootstrap [N | name…] [--method] [--cwd] [--prompt] [--dry-run] [--force]`

Resolves names, enforces the cap, spawns, and prints a per-agent ok/fail summary
plus the right next step (attach hint for tmux, paste list for print). The spawned
agents register themselves under their `GROUPCHAT_HANDLE`, so they appear in `who`
mapped to exactly the handles bootstrap chose.

### Command: `/groupchat:team [N | name…]`

The "ask the human" half lives here (Claude drives it; `chat.py` stays
non-interactive):

1. `<cli> who` to see who's already active.
2. If `$ARGUMENTS` present → pass through (number or names).
3. Else if no other active agents → **ask the human how many teammates the repo
   needs**, then `bootstrap <N>`.
4. Else (teammates already exist) → report the roster, ask how many *more*.
5. Confirm, run `<cli> bootstrap …`, report the spawned roster, note the new
   Terminal windows are teammates the human can `/rename`.

## Non-goals / YAGNI

- No cross-machine spawning (the bus is a local file; out of scope as ever).
- No auto-`expect`/team-size wiring — spawned agents are idle, not running a shared
  goal, so the barrier stays opt-in via `expect`/`GROUPCHAT_TEAM_SIZE`.
- No process supervision — bootstrap opens windows and forgets; the human (or each
  agent's own Stop barrier) owns their lifecycle.

## Testing

`tests/bootstrap_rename_test.py` (isolated via `GROUPCHAT_DIR`, dependency-free,
auto-collected by `run_all.py`):

- **rename**: changes handle; rejects active collision; reclaims an inactive
  holder; rejects `human`; sanitizes; idempotent on self; lead pointer follows the
  rename; read cursor preserved across rename.
- **bootstrap**: `--dry-run`/`--method print` picks N distinct free pool names and
  emits the right `GROUPCHAT_HANDLE=… claude` commands without spawning; explicit
  names are sanitized + collision-suffixed; the cap blocks an oversized request and
  `--force` overrides.
