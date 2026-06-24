---
description: Release the whole team from the barrier — the fleet teardown switch
argument-hint: ["optional reason" | --clear]
---
`standdown` tells every agent it may stop now: each parked teammate is released from the
team barrier within a poll tick, instead of waiting for the whole team to finish. Use
the agora CLI path from your SessionStart briefing as `<cli>`.

- **Declare it** (optionally with a reason from `$ARGUMENTS`) → `<cli> standdown "<reason>"`.
- **Lift it** → `<cli> standdown --clear` (the barrier goes back to normal).

It auto-expires after the active window, so a stale standdown can't haunt a later run.
Use this to wrap up a whole fleet at once. To release just *one* agent (e.g. a finished
worker while you keep orchestrating), use `dismiss <handle>` instead.
