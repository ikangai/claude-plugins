---
description: Imperatively redirect a teammate (a blocking @mention after an active check)
argument-hint: <handle> "the instruction"
---
`direct` steers a specific teammate: it posts an @mention (which blocks their Stop, so
they pick it up on their next turn) after checking the target is actually active. Use
the group-chat CLI path from your SessionStart briefing as `<cli>` and your handle as
`<you>`.

From `$ARGUMENTS`, take the first token as the target handle and the rest as the
instruction, then run:

    <cli> direct <handle> "<instruction>" --from <you>

If the target isn't active you'll get an error (`who` to see the roster). Use this when
you need a teammate to change course now — for a broadcast to *everyone*, put `@team`
(or `@all`) in a normal message instead, which @mentions every active teammate.
