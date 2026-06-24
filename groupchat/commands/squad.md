---
description: Show or join a squad — a sub-team with its own barrier (for sharding a big fleet)
argument-hint: [<squad-name> | (empty to show)]
---
A **squad** is a sub-team with its **own team barrier**, so a finished squad tears down
independently instead of waiting for the whole fleet — the way to shard dozens of agents
into bounded groups. The lead / `@human` funnel stays **global** (one point of human
contact). The default room (no squad) is unchanged. Use the group-chat CLI path from your
SessionStart briefing as `<cli>` and your handle as `<you>`.

- **Show your squad** (empty `$ARGUMENTS`) → `<cli> squad --from <you>`.
- **Join a squad** → `<cli> squad <name> --from <you>` (sanitized to `a-z0-9_-`; an empty
  name leaves your squad, back to the default room).
- **Declare a squad's size** (closes its startup race) → `<cli> expect --squad <name> N`.
- **Spawn a whole squad** → `<cli> bootstrap N --squad <name>` (each spawned agent joins
  the squad and the squad's size is declared).

You can also be born into a squad: launch with `GROUPCHAT_SQUAD=<name> claude`. `who` shows
each agent's squad. When your squad's work is done, just stop — your squad's barrier
releases you without waiting on other squads.
