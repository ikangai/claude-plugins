---
description: Claim / release / list the files you're about to edit (soft collision-safety)
argument-hint: [list | <glob> | --path <file> | release <glob>]
---
A **claim** is a structured "I'm about to edit these files" — teammates see it in
`claims` and their briefing, so two agents don't quietly edit the same file. It's a soft,
advisory signal (no lock), most useful when agents share a working tree. Use the
agora CLI path from your SessionStart briefing as `<cli>` and your handle as `<you>`.

From `$ARGUMENTS` (default to listing when empty):

- **list** → `<cli> claims` — show every active claim. `--path <file>` looks up who has
  claimed a specific path.
- **claim** (a glob like `src/auth/*.py`) → `<cli> claim "<glob>" --from <you>`. If it
  overlaps an existing claim you're told whose.
- **release** → `<cli> unclaim "<glob>" --from <you>` when you're done.

Claim before you start editing a shared area; if `who` shows a **shared working tree**
warning, claiming (or asking the other agent first) is how you avoid a collision.
