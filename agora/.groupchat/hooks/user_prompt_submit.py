#!/usr/bin/env python3
"""UserPromptSubmit hook: surface any new chat messages before the model replies.

Runs on every prompt. If teammates have posted since this agent last looked,
their messages are injected as context (and the read cursor advances so they
aren't shown twice). Silent when there's nothing new. A very large backlog is
capped — only the most recent CAP are injected (older remain available via
`chat.py log`), keeping context bounded.
"""
import sys

CAP = 40  # max messages to inject at once


def main():
    import os
    from _hooklib import load_chat, read_input, emit_context, mentions_of

    data = read_input()
    sid = data.get("session_id")
    if not sid:
        return
    cwd = data.get("cwd")

    chat = load_chat()
    conn = chat.connect()
    handle = chat.register(conn, sid, cwd=cwd, pid=os.getppid())
    agent = chat.agent_by_session(conn, sid)
    if not agent:
        return

    unread = chat.unread_for(conn, agent)
    if not unread:
        return  # nothing new -> inject nothing

    path = os.path.abspath(chat.__file__)
    shown = unread[-CAP:]
    omitted = len(unread) - len(shown)
    header = "📨 New group-chat messages (since your last turn):"
    if omitted:
        header += f"\n…{omitted} older message(s) omitted — see: python3 \"{path}\" log"
    text = header + "\n" + chat.format_messages(shown, highlight=handle)
    if any(mentions_of(m, handle) for m in shown):
        text += (f'\n\n→ You were mentioned. Reply with: '
                 f'python3 "{path}" send --from {handle} "..."')

    chat.mark_read(conn, sid, unread[-1]["id"])
    emit_context("UserPromptSubmit", text)


try:
    main()
except Exception:
    pass  # never block the user's prompt (a non-zero exit would)
sys.exit(0)
