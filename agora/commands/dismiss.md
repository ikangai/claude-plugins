---
description: Release ONE agent from the barrier (lead/operator action)
argument-hint: <handle>
---
`dismiss` releases a single teammate from the team barrier, so a long-running
orchestrator doesn't pin its finished workers in place until the park ceiling. Use the
agora CLI path from your SessionStart briefing as `<cli>` and your handle as
`<you>`.

Run `<cli> dismiss <handle> --from <you>`. It marks that agent done (so it no longer
holds the rest of the team) and releases it to stop within a poll tick.

Only the **lead** (or the operator) may dismiss — if you're not the lead you'll be told
so (`<cli> lead` shows who is). To wrap up the *whole* fleet at once, use `standdown`
instead.
