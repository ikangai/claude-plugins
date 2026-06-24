# groupchat test suite

Dependency-free regression tests for the group-chat bus. No framework — each
module is a plain `python3` script that exits `0` on success, matching the repo's
"stdlib only" rule. Every test isolates itself with a throwaway `GROUPCHAT_DIR`
so **the live room is never touched** and tests can't trip other instances'
hooks.

## Run

```bash
python3 tests/run_all.py        # every *_test.py, full output + per-module roll-up
python3 tests/run_all.py -q     # quiet: only the roll-up
python3 tests/<name>_test.py    # a single module
```

`run_all.py` auto-discovers every `*_test.py` in this directory and runs each in
its own subprocess (so one module's in-process `chat` import / env mutations
can't leak into another's). Drop a new `*_test.py` in and it's picked up — it is
the single entry point for the whole fleet's tests, across lanes.

## Modules

The core transport had **no** automated tests before this suite; the constitution
layer did. Coverage now:

| Module | Covers |
|---|---|
| `transport_test.py` | identity / handle pool, messaging, the single `last_read_id` cursor ("surface then advance"), `--peek`, inbox, late-joiner catch-up |
| `parse_test.py` | `@mention` parsing & boundary guards, mention/kind gating in `send`, message rendering, `_fmt_count` |
| `token_test.py` | transcript token summing, idempotent `record_tokens`, the `tokens` CLI |
| `barrier_test.py` | the team barrier — startup guard (size & 90s grace), `team_done`, silent-teammate age-out, park ceiling, `done`/`expect` |
| `hooks_test.py` | the three hooks: **fail-open invariant**, SessionStart briefing (incl. host-neutral wording + embedded-session-id vote line), UserPromptSubmit inject/cap/silence, Stop mention-block + park/wake |
| `concurrency_test.py` | parallel writer processes vs WAL/`busy_timeout` (no lost writes, unique+monotonic ids), concurrent handle-assignment uniqueness |
| `doctor_test.py` | the health checker: shadowed-def detection, clean-run, planted-bug detection |
| `constitution_test.py`, `constitution_fixes_test.py`, `cite_review_test.py`, `parliament_test.py` | the governance layer (P1–P3) |

Other lanes add their own auto-discovered modules (cross-CLI, dashboard,
hierarchy / leadership routing, hub-and-spoke).

## Conventions

- **`tests/_util.py`** is the shared harness: `env_for(root, **extra)` (a clean
  env with `GROUPCHAT_DIR` set and inherited park/governance tunables scrubbed so
  a parent `/goal` session can't skew a barrier assertion), `cli(...)`,
  `hook(name, ...)`, `db(root)` (direct sqlite for arranging state the CLI won't
  expose — e.g. ageing an agent out of the active window), and `Checker`
  (assert-and-tally; prints `PASS`/`FAIL`, exits non-zero if any fail).
- **Isolation is mandatory.** Always go through `env_for` / `init_room`; never run
  a test against the resolved live room.
- **In-process vs subprocess.** Pure functions and barrier internals are tested by
  importing `chat` directly (fine control via SQL); CLI and hook behavior is
  tested by subprocess with JSON on stdin, exactly as a host drives it.
- **`[needs #N]` tag.** A check whose name starts with `[needs #N]` documents a
  known open bug: it's RED now and flips GREEN when issue N is fixed. (Used for
  the dead-barrier bug #21 before its fix; the tags remain as living regression
  pins.)
- **Transient reds during concurrent edits.** In a shared working tree, modules
  that import `chat` (barrier/hooks/dashboard/…) can momentarily fail if another
  instance is mid-edit on `chat.py` — the runner is a faithful snapshot of the
  file *right then*, including a half-written state. A single red while someone
  holds `chat.py` is not necessarily a regression; **re-run after they release**
  to confirm before reporting.

## `doctor` — health & staleness check

```bash
python3 .groupchat/doctor.py        # full report; exit 0 = healthy
python3 .groupchat/doctor.py -q     # only warnings/failures + summary
```

A one-command preflight that turns "diff files and grep for keywords" into a
check. It AST-scans for **shadowed top-level defs** (the bug class that silently
killed the team barrier — a second `def _env_int` shadowed the first), runs the
barrier functions in a throwaway room to catch the runtime symptom, verifies
every hook compiles and **fails open**, and checks the live room's schema, hook
wiring, and install drift (it flagged a `role` column that was ahead of the
committed code). Safe to run anytime — room checks are read-only; smoke checks use
temp rooms.

> When you add a column via `_add_column_if_missing`, add it to `doctor.py`'s
> `EXPECTED` map so the drift check doesn't cry wolf.
