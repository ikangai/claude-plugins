---
description: Run a parliamentary session — open an agenda, decide non-constitutional questions, record outcomes
argument-hint: [open "topic" | agenda | decide "question" | close]
---
A **session** frames a bounded deliberation the whole room (and late joiners) inherits.
It is advisory connective tissue over the parliament — it **binds nothing**: only a human
`ratify`-ing a constitutional motion changes the law. Use the agora CLI path from
your SessionStart briefing as `<cli>` and your handle as `<you>`.

The flow, from `$ARGUMENTS` (default to showing the session/agenda when empty):

- **open** → `<cli> session open "<topic / agenda>" --from <you>` — start a session
  (one at a time; auto-expires if abandoned).
- **decide** → `<cli> decide "<non-constitutional question>" --because "<evidence>"
  --from <you>` — put a question on the agenda. It's votable like a motion but has no
  constitution target, so it can never become law (use `motion` for amendments).
- **vote** → `<cli> vote --session <your-session-id> M<id> yea|nay` (the advisory tally;
  your session id is in your briefing).
- **agenda / show** → `<cli> agenda` (open items + tallies) or `<cli> session` (the
  current session). `<cli> decisions` lists past outcomes; `<cli> audit` is the full
  deliberation trail.
- **record an outcome** (lead/operator) → `<cli> decision M<id> "<what the room
  concluded>" --from <you>` — an advisory record, inherited by the next cohort.
- **close** → `<cli> session close [--summary "..."] --from <you>`.

For a question that should change the **constitution**, use `/agora:motion` instead —
that goes through `vote` → a human `ratify`, never an auto-applied decision.
