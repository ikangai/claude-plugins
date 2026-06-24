---
description: Bootstrap teammates — open other Claude instances into this repo's agora bus
argument-hint: [N | name...]
---
Stand up the rest of the team. Each teammate is spawned as a new Claude instance
(a new Terminal window on macOS) that joins **this repo's** agora bus under its
own handle, then waits for you to direct it. Use the agora CLI path from your
SessionStart briefing as `<cli>`.

**Before spawning, sanity-check it's the right tool.** A spawned session is a
*persistent peer* (its own context/terminal, outlives a turn, `@mention`-able, can take
a worktree). For a tightly-scoped *fan-out-then-join that returns a structured result
within this turn*, prefer the native **Agent/Task tool or a Workflow** — it's cheaper
and carries the goal in-process. Spawn a session only when the worker must outlive the
turn, be human-watchable/steerable, edit in an isolated worktree, or stay reachable for
a later @mention / the barrier.

Do this:

1. Run `<cli> who` to see who is already active.
2. Decide the team from `$ARGUMENTS`:
   - **A number** (e.g. `3`) → spawn that many auto-named teammates from the pool.
   - **Names** (e.g. `frontend backend qa`) → spawn exactly those, named so.
   - **Names with per-agent prompts** (e.g. `frontend:'build the UI' backend:'write
     the API'`) → spawn each with its OWN initial task instead of one shared prompt,
     so the team starts divided-and-conquering immediately.
   - **Empty `$ARGUMENTS`:**
     - If there are **no other active agents**, ask me **how many teammates this
       repo needs**, then use that number.
     - If teammates already exist, tell me who's active and ask how many *more* to
       add (it's fine to add none).
3. Briefly confirm the plan (spawning real Claude sessions costs tokens), then run:

       <cli> bootstrap <N | names...>

   Add `--dry-run` first if I want to preview the exact launch commands without
   opening anything, or `--method print` to just get commands to paste myself.
   Add `--worktree` if the teammates will edit code in parallel — each gets its own
   git worktree (branch `groupchat/<name>`) so their edits can't collide, while one
   shared chat keeps them coordinating. Add `--goal "<objective>"` to record the
   team's shared mission (every teammate sees it in their briefing and `who`).
   Bootstrap records the team size so the barrier is precise and nobody waits on a
   teammate who never came.
4. Report the spawned roster. Tell me the new Terminal windows **are** my
   teammates — I can switch to any of them and start giving instructions, and each
   can `/rename` itself to something meaningful (e.g. `frontend`, `reviewer`).
