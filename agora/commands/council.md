---
description: Show the council — the chair (sole human contact) and each squad's captain
---
In a multi-squad fleet, leadership is a **chair-topped council**: each squad has a
**captain**, the captains escalate to one **chair**, and the chair is the single operator
contact. The `@human` funnel climbs **worker → squad captain → chair → operator**, so each
tier absorbs what it can and only the residual reaches the human. Use the agora CLI path
from your SessionStart briefing as `<cli>`.

- **See the council** → `<cli> council` (the chair + every squad's captain + members).
- **Become your squad's captain** → `<cli> lead --claim --from <you>` (scoped to YOUR
  squad; emergent floor otherwise — the earliest-joined member leads by default).
- **Hand off / release your captaincy** → `<cli> lead <handle> --from <you>` /
  `<cli> lead --release --from <you>`.
- **Manage the chair** (the global contact) → add `--chair`, e.g.
  `<cli> lead --chair --claim --from <you>`.

When you're a captain and can't answer a teammate's `@human`, escalate with your own
`@human` — it routes to the chair, and you stay parked (your squad held up) until the
chair relays an answer. In an unsharded room this is just the single flat lead, unchanged.
