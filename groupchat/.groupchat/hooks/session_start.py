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
    # $GROUPCHAT_HANDLE lets a human name this shell's agent at launch
    # (`GROUPCHAT_HANDLE=frontend claude`) so the roster is self-identifying; falls
    # back to the auto-assigned pool name when unset. Honored only while the name is
    # free (recycled from inactive sessions), never stealing an active teammate's.
    handle = chat.register(conn, sid, cwd=cwd, pid=os.getppid(),
                           handle=(os.environ.get("GROUPCHAT_HANDLE") or None))
    agent = chat.agent_by_session(conn, sid)
    path = os.path.abspath(chat.__file__)

    others = [a for a in chat.active_agents(conn) if a["handle"] != handle]
    recent = chat.recent_messages(conn, RECENT)

    lines = [
        f"## Repo group chat — you are **{handle}**",
        "Several AI coding-agent sessions (Claude Code, Codex, opencode, …) may be "
        "working this repo in parallel. "
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
        # Make the hub-and-spoke hierarchy discoverable — otherwise a joining agent
        # never learns @human funnels to a lead, or that it can claim/hand off the
        # lead role. (Wording per tesla, #57; the literal @human token is safe here
        # — the briefing is injected context, not a chat message through send().)
        'Human:   write @human in a message to reach the operator — it funnels to '
        'the lead (the fleet\'s single point of contact)',
        f'Lead:    python3 "{path}" lead   '
        '(show the lead; `lead --claim` to take it, `lead <h>` to hand off)',
        f'Rename:  python3 "{path}" rename --from {handle} <new-name>   '
        '(change your handle; keeps your history)',
    ]
    if recent:
        lines += ["", "Recent chat:", chat.format_messages(recent, highlight=handle)]
        # Only advance the cursor if all unread is within the shown window, so we
        # never mark unshown backlog as read (UserPromptSubmit delivers the rest).
        if agent and len(chat.unread_for(conn, agent)) <= RECENT:
            chat.mark_read(conn, sid, recent[-1]["id"])

    # Point agents at the coordination constitution, if one exists. Best-effort:
    # a corrupt or missing constitution must never disturb the briefing (C2).
    try:
        cpath = chat.constitution_path()
        if os.path.isfile(cpath):
            lines += [
                "",
                "Constitution: this repo has a coordination CONSTITUTION.md — follow "
                "it and cite rules by id (e.g. R2) in chat. "
                f'View: python3 "{path}" constitution',
                # Voting needs a *registered* session, not just a handle. We know
                # this agent's session id (it's in the hook payload), so embed it
                # straight into the one-liner — host-neutral (works for Claude
                # Code, Codex, opencode alike) and more robust than pointing at a
                # Claude-only env var. Otherwise the parliament is unusable by an
                # agent that only knows its handle.
                f'Vote on an open motion (advisory): python3 "{path}" vote '
                f'--session "{sid}" M<n> yea|nay',
            ]
    except Exception:
        pass

    emit_context("SessionStart", "\n".join(lines))


try:
    main()
except Exception:
    pass  # never break a session
sys.exit(0)
