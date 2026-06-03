---
name: groupchat
description: Use when this repo has the group-chat plugin installed and other Claude Code instances may be working the same repo in parallel — how to coordinate via the shared chat (announce work, flag files, @mention teammates, answer mentions, wait at the team barrier).
---

# Group chat for parallel instances

Several Claude Code instances may be working this repo at once. A shared chat bus
(SQLite, managed by the plugin's hooks) is your team channel. New messages arrive
in your context automatically before each turn — never poll.

Your handle is in your SessionStart briefing. Use it to post.

## Do
- **Announce before you act.** "Starting on `src/auth/handler.py`" prevents two
  agents editing the same file.
- **@mention** the specific agent when you need a reply; a plain message is a
  broadcast. Only @mentions block a teammate's Stop, so reserve them for things
  needing a response.
- **Answer mentions.** If your Stop surfaces an unanswered @mention, reply in chat
  before finishing.
- **Stop normally when your slice is done.** You won't exit when *you* finish —
  the Stop hook parks you (dormant, ~0 tokens) at the team barrier and wakes you
  if a teammate @mentions you. Don't poll or spin to stay available.
- **Declare team size early** if you know it: `chat.py expect N` (or launch with
  `GROUPCHAT_TEAM_SIZE=N`). Otherwise a 90s startup grace applies.

## CLI (the absolute path is in your SessionStart briefing)
- `send --from <you> "msg, @mention to ping"` — post
- `who` — roster (active ● / idle ○), with each agent's approx output tokens
- `tokens` — approximate per-agent token usage (from the local transcript)
- `inbox --from <you>` — your unread @mentions
- `done --from <you>` — mark your slice done (wait at the barrier)

Slash commands `/groupchat:who`, `/groupchat:chat`, `/groupchat:inbox`,
`/groupchat:tokens` wrap these.
