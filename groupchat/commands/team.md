---
description: Bootstrap teammates — open other Claude instances into this repo's group chat
argument-hint: [N | name...]
---
Stand up the rest of the team. Each teammate is spawned as a new Claude instance
(a new Terminal window on macOS) that joins **this repo's** group chat under its
own handle, then waits for you to direct it. Use the group-chat CLI path from your
SessionStart briefing as `<cli>`.

Do this:

1. Run `<cli> who` to see who is already active.
2. Decide the team from `$ARGUMENTS`:
   - **A number** (e.g. `3`) → spawn that many auto-named teammates from the pool.
   - **Names** (e.g. `frontend backend qa`) → spawn exactly those, named so.
   - **Empty `$ARGUMENTS`:**
     - If there are **no other active agents**, ask me **how many teammates this
       repo needs**, then use that number.
     - If teammates already exist, tell me who's active and ask how many *more* to
       add (it's fine to add none).
3. Briefly confirm the plan (spawning real Claude sessions costs tokens), then run:

       <cli> bootstrap <N | names...>

   Add `--dry-run` first if I want to preview the exact launch commands without
   opening anything, or `--method print` to just get commands to paste myself.
4. Report the spawned roster. Tell me the new Terminal windows **are** my
   teammates — I can switch to any of them and start giving instructions, and each
   can `/rename` itself to something meaningful (e.g. `frontend`, `reviewer`).
