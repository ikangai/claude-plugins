# Coordination & bootstrap hardening — design

*2026-06-22*

## Goal

Make parallel coordination and team bootstrapping robust:

1. **Peers do not deadlock.**
2. **Peers are aware of how many other instances are working.**
3. **When working solo, don't wait for others.**
4. **Work doesn't interfere with other instances.**

This is purely a hardening pass on the existing team barrier (`stop.py` +
`chat.team_done`/`startup_guard_satisfied`), the bootstrap path, and the roster
surfaces. It adds nothing that runs in a room that never uses it — the
*dormant-until-used*, *additive*, *fail-open*, *single-cursor* invariants hold.

## Defects (confirmed by an 8-lens adversarial audit, 2026-06-22)

- **D1 — a solo agent waits ~90s.** With no declared team size,
  `startup_guard_satisfied` falls to a 90s cohort-age grace. A lone agent's cohort
  *is itself*, so a short solo task is force-parked until it crosses 90s. Violates
  goal #3.
- **D2 — a declared-but-unmet team size hangs everyone for 2h.** When a size is
  declared the guard is `count >= size` with **no time fallback**; if a teammate
  never registers (a failed bootstrap window, a too-high size) the barrier never
  completes until the 2h `MAX_PARK` ceiling. A real deadlock — and it gets *more*
  likely once bootstrap auto-declares size (D3).
- **Dominant variant of D2 — the guard counts all-time rows.** The size branch used
  `SELECT COUNT(*) FROM agents`, which counts every row ever created. Stale rows
  from prior runs (recycling only deletes a dead row when its *name* is reused)
  inflate the count, so on a reused room the declared-size guard is *trivially
  satisfied by ghosts* → premature exit. Every sibling (`cohort_age_seconds`,
  `team_done`) already scopes to `active_agents`; only this one line didn't.
- **D3 — bootstrap doesn't declare the team size.** Bootstrap is the exact moment
  the size is known, yet it never records it. The fresh team falls back to the 90s
  grace and nobody knows the target count.
- **D4 — weak instance-count awareness.** `who`/the briefing show active handles but
  no expected-vs-active-vs-done counts; joining a room is silent; the park message
  literally says "teammates not finished yet" even when there are zero teammates.
- **D5 — interference is convention-only.** The sole safeguard is the prose "announce
  before you act"; nothing prevents two agents editing the same file, and bootstrap
  puts every spawned agent in one shared cwd.

## Fixes

Ordered smallest-risk / highest-value first.

### 1. `startup_guard_satisfied` — active count, solo grace, size time-fallback

```python
def startup_guard_satisfied(conn):
    n_active = len(active_agents(conn))
    size = expected_team_size(conn)
    if size:
        if n_active >= size:
            return True
        return cohort_age_seconds(conn) >= STARTUP_GRACE_SECONDS   # bounded, not 2h
    if n_active <= 1:
        return cohort_age_seconds(conn) >= SOLO_GRACE_SECONDS      # solo: short settle only
    return cohort_age_seconds(conn) >= STARTUP_GRACE_SECONDS
```

- **Active count** (was all-time `COUNT(*)`) kills the ghost-row premature exit and
  aligns with every sibling.
- **Solo settle grace** (`SOLO_GRACE_SECONDS`, default **10s**, env
  `GROUPCHAT_SOLO_GRACE`): a lone, undeclared agent waits at most ~10s — long enough
  to catch a co-launched teammate that's a second behind registering (which flips it
  into the multi-agent branch and the full 90s grace), but not "waiting for others".
- **Size time-fallback**: a declared team that never fully assembles releases at the
  90s grace, not the 2h ceiling — the D2 deadlock becomes a bounded wait.

The team barrier proper (`team_done`) is unchanged: guard satisfied **and** every
active agent `done`.

### 2. Bootstrap declares the team size + reports who joined

- After a real spawn, `cmd_bootstrap` sets `meta['team_size'] = active_before + ok`
  so the barrier is precise from t=0 and everyone (and `who`) knows the target.
  Skipped for `--dry-run` (nothing spawned). The size time-fallback (#1) means an
  optimistic count can't wedge — a no-show just delays to the 90s grace.
- A best-effort post-spawn poll (≤~6s) of `active_agents` reports each teammate as
  joined `✓` or not-yet `⏳`, catching the phantom-`✓` case (osascript returns 0 but
  `claude` wasn't on the child's PATH). Purely informational; never blocks.

### 3. Instance-count awareness (D4)

- `who` gains a summary footer: `team: N active (K done) · expecting M — P not yet
  joined` (or `working solo` when alone & undeclared).
- The SessionStart briefing shows the same counts, and tells a solo agent it won't
  wait at a barrier.
- On a genuine first registration into a **non-empty** room, SessionStart posts a
  one-line `system` join notice so existing agents become aware via the cursor. Solo
  joins stay silent (byte-identical-when-unused preserved).
- The `stop.py` re-park message states the real cause (assembling / awaiting a
  declared size / specific unfinished handles) instead of a literal "teammates".

### 4. `bootstrap --worktree` — hard filesystem isolation (D5)

Chosen mechanism for non-interference. Each spawned agent gets its own git worktree
(branch `groupchat/<name>`) in a sibling `<repo>-worktrees/<name>` dir and launches
`cd`'d into it. Because `store_dir()` anchors the bus at the **git common dir**, all
worktrees still share one `chat.db` — full chat, zero file collisions.

- `--dry-run`/`--method print` emit the `git worktree add … && cd … && claude`
  commands without creating anything.
- Per-agent fail-open: a worktree that can't be created is reported and skipped (it
  is **not** silently downgraded to the shared cwd — isolation was the point).
- Cleanup is the operator's (`git worktree remove`); documented, not automated.

Rationale for worktree over a claim-ledger: the operator picked physical isolation —
it removes the collision substrate entirely rather than warning about it, and needs
no new table, hook, or per-write check.

### 5. `stop.py` — clamp the park sleep to the deadline

`time.sleep(min(POLL_TICK, max(0, deadline - now)))` so a large `GROUPCHAT_POLL_TICK`
(or lock contention) can't overshoot the 600s Stop-hook timeout — an overshoot lets
the host kill the hook with no block emitted, releasing the agent early and dropping
a later @mention.

## Deferred (found by the audit, out of this goal's scope — reported, not fixed)

- **Escalation orphan on lead rename/handoff/floor-failover.** `open_escalations`
  matches the *current* lead handle, but a message's sender is frozen at author time;
  a rename/handoff makes an in-flight `@human` invisible to the gate (and `answer`
  @mentions the dead handle). Fix = resolve by author session_id. Leadership-layer,
  larger blast radius.
- **Mixed-fleet `done` signal.** opencode/generic agents have no Stop hook and never
  mark `done`, so they can hold a Claude/Codex team at the barrier until they age out
  (15 min) or the ceiling. The size time-fallback (#1) does **not** cover this (the
  all-done check is separate). Proper fix is adapter-side `chat.py done` at end of
  task — assessed against the bridge; documented if not wired here.
- **Perf:** `open_escalations` full-table scans per park tick (dormant in flat rooms).

## Adversarial review outcome

An 17-agent multi-dimension review (deadlock-freedom, backward-compat, fail-open,
logic, worktree, goal) of the diff returned **go-with-fixes, zero must-fix
blockers**: no deadlocks (every wait bounded by the 90s grace and/or 2h ceiling), no
fail-open violations (all touched code is in the bootstrap/CLI path, not the
injection/Stop hooks), and the isolation invariant holds (a worktree failure always
reports and skips — never a silent downgrade to the shared cwd). Three should-fix
items — all the same *team_size lifecycle* theme, each partially regressing goal #3 —
were fixed before finishing:

1. **Stale `team_size` never cleared.** A solo session in a *reused* room read a
   prior team's size and waited ~90s. Fixed at the root: `register()` clears
   `meta['team_size']` when a fresh cohort's first agent registers into an otherwise
   empty active set (not a read-path mask, which would re-open the ragged race).
2. **`--method print` persisted a phantom size** for never-launched agents. Gate
   changed from `not dry_run` to `not only_printing`.
3. **`$GROUPCHAT_TEAM_SIZE` env silently shadowed the declared size.** Bootstrap now
   prints a note that the env overrides the declared number.

Plus a nit: `_create_worktree` now concatenates both git stderrs on a double-failure.

Deferred nits (near-zero reachability, bounded, reported not fixed): a corrupt/NULL
`first_seen` on the sole active agent would defer its solo release to the 2h ceiling
(only reachable via a hand-corrupted/legacy-NULL row); `--worktree` from *inside* a
worktree nests trees one level deeper (layout only — isolation holds); re-bootstrap
`--worktree` onto a still-live same-named worktree hard-fails with verbose git
stderr (correctly reported + skipped).

## Fresh-eyes review (round 2) — a self-inflicted blocker, fixed

A second, deliberately *unprimed* review (reviewers given only the current code + the
goal, not the first review's conclusions; lenses added for **concurrency/TOCTOU** and
**test integrity**) found a real **blocker introduced by review-fix #1 itself**: the
unconditional `register()` `team_size`-clear couldn't tell a *stale* size from a
*fresh* one, so it silently erased a legitimately-declared size on the documented
paths — `expect N` (meta), bare-CLI `bootstrap` (no anchoring agent), and concurrent
co-launch — re-opening the very ragged-startup race the size guard closes. (The
Claude-driven `/groupchat:team` path was safe — the bootstrapper agent anchors the
cohort.) Non-deadlocking, but a silent loss of the coordination signal.

Fix (not a revert — dropping the clear would leave a quick solo task waiting ~90s in
*any* repo that ever ran a team): **stamp the size when declared** (`set_team_size`
writes `team_size` + `team_size_at`, used by `expect`/`bootstrap`), and clear only a
**provably-stale** one — the newcomer is the sole active agent **and** the size was
declared more than one active-window (15 min) ago, so its cohort has certainly aged
out. A fresh declaration (recent stamp) is never erased; a missing stamp (old db)
reads as stale. The age gate also makes the read-then-delete **race benign** — only
provably-stale sizes are ever cleared. `expect 0` resets manually.

Also fixed: the worktree re-bootstrap **stale-branch fallback** removed (it silently
checked out a leftover `groupchat/<name>` branch = stale base; now reports + skips,
and drops the `makedirs` so a failure leaves no empty dir); a **negative
`GROUPCHAT_POLL_TICK`** is floored so it can't reach `time.sleep()` (it would
silently kill that agent's barrier); the solo wording softened (a ~10s settle still
applies); and test gaps closed (fresh-declaration-survives regression, worktree
error/skip + no-stale-reuse, discriminating silent-teammate test, `who` summary
branches, de-flaked the stubbed bootstrap test). The undeclared-multi-cohort 10s
tradeoff is now documented.

## Testing

Dependency-free, `GROUPCHAT_DIR`-isolated, in the existing harness style:
`barrier_test.py` (solo grace, size fallback, active-count vs ghost rows),
`bootstrap_rename_test.py` (size declaration), a new `worktree_test.py`
(dry-run/print only — nothing launches), plus hook/CLI checks for awareness. Full
suite via `tests/run_all.py`.
