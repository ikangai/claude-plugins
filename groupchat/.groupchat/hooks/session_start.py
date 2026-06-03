#!/usr/bin/env python3
"""SessionStart hook: register this instance and inject a catch-up briefing.

Assigns the session a stable handle, lists active teammates, shows recent chat,
and tells the model how to post. Advances the read cursor ONLY when every unread
message is actually shown (i.e. the agent is caught up within the recent window),
so a resumed agent that is far behind never has messages silently marked read —
those are delivered by the UserPromptSubmit hook instead.
"""
import sys

RECENT = 15


def main():
    import os
    from _hooklib import load_chat, read_input, emit_context

    data = read_input()
    sid = data.get("session_id")
    if not sid:
        return
    cwd = data.get("cwd")

    chat = load_chat()
    conn = chat.connect()
    handle = chat.register(conn, sid, cwd=cwd, pid=os.getppid())
    agent = chat.agent_by_session(conn, sid)
    path = os.path.abspath(chat.__file__)

    others = [a for a in chat.active_agents(conn) if a["handle"] != handle]
    recent = chat.recent_messages(conn, RECENT)

    lines = [
        f"## Repo group chat — you are **{handle}**",
        "Several Claude Code instances may be working this repo in parallel. "
        "Coordinate through this shared chat: announce what you're starting, "
        "flag files you're about to change, ask teammates before stepping on "
        "their work, and answer when @mentioned. New messages are shown to you "
        "automatically before each turn — you don't need to poll.",
        "",
        ("Active teammates: " + ", ".join(a["handle"] for a in others)) if others
        else "No other active agents right now (you may be first).",
        "",
        f'Post:    python3 "{path}" send --from {handle} "your message"',
        "Mention: include @handle in the text to ping a specific teammate",
        f'Roster:  python3 "{path}" who',
    ]
    if recent:
        lines += ["", "Recent chat:", chat.format_messages(recent, highlight=handle)]
        # Only advance the cursor if all unread is within the shown window, so we
        # never mark unshown backlog as read (UserPromptSubmit delivers the rest).
        if agent and len(chat.unread_for(conn, agent)) <= RECENT:
            chat.mark_read(conn, sid, recent[-1]["id"])

    emit_context("SessionStart", "\n".join(lines))


try:
    main()
except Exception:
    pass  # never break a session
sys.exit(0)
