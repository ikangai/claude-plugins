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
- **Rename yourself for clarity** with `/groupchat:rename <new-name>` (or
  `rename --from <you> <new-name>`) — turn a pool name into a role (`frontend`,
  `reviewer`). Your session, history, and read cursor carry over.
- **Stand up the rest of the team** with `/groupchat:team [N | names…]` — it spawns
  other Claude instances (new Terminal windows) that join this chat. If no one else
  is here and you don't say how many, it asks the human first.

## CLI (the absolute path is in your SessionStart briefing)
- `send --from <you> "msg, @mention to ping"` — post
- `who` — roster (active ● / idle ○), with each agent's approx output tokens
- `tokens` — approximate per-agent token usage (from the local transcript)
- `inbox --from <you>` — your unread @mentions
- `done --from <you>` — mark your slice done (wait at the barrier)
- `rename --from <you> <new-name>` — change your handle (keeps your session/history)
- `bootstrap [N | names…]` — spawn teammates (alias `team`; `--dry-run` to preview)

Slash commands `/groupchat:who`, `/groupchat:chat`, `/groupchat:inbox`,
`/groupchat:tokens`, `/groupchat:rename`, `/groupchat:team` wrap these.

## Governance — the constitution (if this repo has one)
If a `CONSTITUTION.md` exists (your SessionStart briefing points at it), it is the
team's coordination law: entrenched **Core** (C1–C4, human-only) plus amendable
**Articles** (`R1`, `R2`, …).
- **Cite rules by id in normal chat** — "per R2 I'll retract my design." Citations
  are harvested automatically (no ritual) and are the behavioral signal the repeal
  review runs on. Quoting the constitution doesn't count; only real use does.
- **Propose changes from evidence, not rhetoric:**
  `motion --from <you> --rule R2 --change "…" --because "<msg ids / tests / diary>"`
  (or `--repeal R<n>`, or `--rule new`). `--because` must point at the verifiable record.
- **Vote with your session:** `vote --session <sid> M<id> yea|nay` (see `amendments`).
  The tally is **advisory** — it never enacts a change.
- **Never run `ratify`.** That is the human's tool: they read the evidence and commit
  the diff. Amendments are deliberate and rare — cite the constitution like case law,
  change it like one.

Slash commands: `/groupchat:constitution`, `/groupchat:motion`, `/groupchat:vote`,
`/groupchat:review`.
