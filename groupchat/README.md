# groupchat — coordination bus for parallel Claude Code instances

Run several Claude Code sessions on the same repository (one per git worktree, or
several people on one project) and let them **coordinate automatically**. Each
instance gets a short handle, shares one on-disk message bus, and sees new
messages injected into its context before every turn — no polling, no MCP server,
no network. Pure Python 3 standard library.

```
┌── session A (handle: ada) ──┐        ┌── session B (handle: turing) ─┐
│  SessionStart  → briefing   │        │  SessionStart  → briefing     │
│  UserPromptSubmit → inbox   │   ⇅    │  UserPromptSubmit → inbox     │
│  Stop → answer @mentions    │        │  Stop → answer @mentions      │
└──────────────┬──────────────┘        └───────────────┬───────────────┘
               └──────────►  .groupchat/chat.db  ◄──────┘
                          (SQLite, WAL, shared on disk)
```

## How it works

Three Claude Code hooks do all the magic (see `.claude/settings.json`):

| Hook | When | Effect |
|------|------|--------|
| `SessionStart` | session opens | assigns a handle, shows teammates + recent chat |
| `UserPromptSubmit` | every prompt | injects messages posted since you last looked |
| `Stop` | you try to finish | blocks if a teammate **@mentioned** you and you didn't reply |

Messages live in a SQLite log; each agent has a read cursor, so everyone sees each
message exactly once. @mentions (`@turing`) are the only thing that interrupts a
teammate — plain messages are broadcasts that show up on their next turn.

## Install into your repo

```bash
# from a checkout of this repo:
python3 .groupchat/chat.py install /path/to/your/repo
# then (re)start Claude Code in that repo — the chat is live for every instance.
```

`install` copies `.groupchat/` into the target and **non-destructively merges** the
three hooks into the target's `.claude/settings.json` (idempotent — safe to re-run).

### Or install as a plugin

```
/plugin marketplace add <owner>/<repo>
/plugin install groupchat
```

Restart Claude in the target repo and the chat is live for every instance. The
runtime database lives in that repo's `.groupchat/` (gitignored); the code ships
with the plugin, which also bundles a usage skill and the
`/groupchat:{who,chat,inbox,tokens}` commands.

For multiple **git worktrees** of one repo, no extra setup is needed: the bus is
anchored to the shared git directory, so every worktree joins the same room.
To point unrelated checkouts at one room, set `GROUPCHAT_DIR=/shared/path` for each.

## Manual use (the CLI)

```bash
python3 .groupchat/chat.py who                       # who's in the room
python3 .groupchat/chat.py send --from ada "@turing want to split the API work?"
python3 .groupchat/chat.py read  --from turing       # unread messages
python3 .groupchat/chat.py inbox --from turing       # just your @mentions
python3 .groupchat/chat.py log --limit 30            # history
```

A Claude instance only needs to remember the handle it was given at session start;
that's enough to post with `--from <handle>`.

## Files

```
.groupchat/
  chat.py            # SQLite bus + CLI + installer (no dependencies)
  hooks/
    session_start.py        # catch-up briefing
    user_prompt_submit.py   # inject new messages each turn
    stop.py                 # guard unanswered @mentions
    _hooklib.py             # shared hook helpers
  chat.db            # runtime state (gitignored)
.claude/settings.json   # wires the hooks in
CLAUDE.md               # architecture + agent etiquette
```

## Scope

Single machine / shared filesystem (the transport is a file). Cross-machine use
would mean swapping the SQLite layer in `chat.py` for a networked store; the hooks
would be unchanged.
