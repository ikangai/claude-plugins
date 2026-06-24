---
description: Show or set the team's one-line shared objective
argument-hint: ["the objective" | --clear]
---
The shared **goal** is the one-line objective every agent sees in its briefing and in
`who`, so a teammate who joins late (or was bootstrapped idle) inherits the mission
without a human retyping it. Use the agora CLI path from your SessionStart
briefing as `<cli>`.

- **Empty `$ARGUMENTS`** → `<cli> goal` and show the current goal verbatim.
- **A quoted objective** (e.g. `"ship v1 of the parser"`) → `<cli> goal "<text>"` to
  set it, then confirm.
- **`--clear`** → `<cli> goal --clear` to unset it.

`bootstrap --goal "<text>"` sets this automatically when you stand up a team, so most
of the time you won't set it by hand.
