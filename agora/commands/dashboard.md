---
description: Render the agora room to a live HTML dashboard and open it
---
Render the read-only room dashboard. The renderer is `dashboard.py`, which lives
in the **same directory** as the `chat.py` whose absolute path is in your
SessionStart agora briefing (the line showing `send --from <handle>`) — take
that path and replace the trailing `chat.py` with `dashboard.py`.

Run it with `python3 "<that dashboard.py path>" --open` (add `--watch 5` if I ask
for a live, auto-refreshing view). It writes `room.html` next to `chat.db` and, with
`--open`, opens it in my browser. Tell me the path it wrote and that it's a
read-only view (it never writes the bus) — roster, conversation, parliament, and
the team-barrier state on one page. Show me the command's output verbatim.
