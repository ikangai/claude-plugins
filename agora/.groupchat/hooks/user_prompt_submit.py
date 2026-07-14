#!/usr/bin/env python3
"""UserPromptSubmit hook: surface any new chat messages before the model replies.

Runs on every prompt. If teammates have posted since this agent last looked,
their messages are injected as context (and the read cursor advances so they
aren't shown twice). Silent when there's nothing new. A very large backlog is
capped — only the most recent CAP are injected (older remain available via
`chat.py log`), keeping context bounded.

A typed prompt is also the strongest attendance signal we have: a human is at
THIS terminal, right now. Two escalation duties ride on it (both fail-open):

* **The asker's own prompt answers its escalation.** The lead-done gate keeps an
  asker `active` while it has an open @human escalation — but an escalation only
  cleared via the bus (`answer` / `send --from human`). The natural operator move
  at an attended session — typing the answer straight into the asker's terminal —
  never touched the bus, so the queue stayed open forever and every spawned
  worker sat at the barrier to the park ceiling. Now a prompt in the asker's own
  session posts the operator marker (`sender='human'`, @current-handle,
  `[re #id]`) so the queue batch-clears exactly as an `answer` would: visible on
  the bus, rename-safe, and it also frees a captain (an operator's direct answer
  outranks the chair relay).
* **Other attended terminals learn the `answer` verb.** While questions are open
  elsewhere, an attended (non-spawned) session's injection carries a one-line
  nudge naming `questions` / `answer <id>` — the escalation queue was invisible
  to operators who never knew the verb existed. Spawned workers get no nudge.
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

    path = os.path.abspath(chat.__file__)

    # A prompt in the ASKER's own session is the operator responding: post the
    # operator marker so the open @human queue batch-clears exactly as `answer`
    # would (bus-visible, rename-safe). Posted BEFORE the unread scan so the
    # marker rides this very injection and is advanced past — it must never
    # resurface as an "unanswered mention" that blocks this agent's next Stop.
    try:
        open_ids = chat.session_open_escalations(conn, sid)
        if open_ids:
            refs = " ".join(f"[re #{i}]" for i in open_ids)
            chat.send(conn, "human",
                      f"@{handle} {refs} (the operator answered at {handle}'s "
                      "terminal — escalation closed)")
    except Exception:
        pass

    # Discoverability: an attended terminal is where the operator lives, so while
    # questions await them ELSEWHERE, say how to answer. Spawned workers are not
    # the operator's terminal — their (rare) prompts stay chore-free.
    nudge = ""
    try:
        try:
            spawned = bool(agent["spawned_by"])
        except (IndexError, KeyError):
            spawned = False
        if not spawned:
            queues = chat.all_open_escalations(conn)
            queues.pop(sid, None)  # own queue was just answered above
            n = sum(len(ids) for ids in queues.values())
            if n:
                nudge = (
                    f"❓ {n} open @human question(s) are waiting for the operator — "
                    f'list: python3 "{path}" questions · '
                    f'answer: python3 "{path}" answer <id> "..."')
    except Exception:
        pass

    unread = chat.unread_for(conn, agent)
    if not unread and not nudge:
        return  # nothing new -> inject nothing

    parts = []
    if unread:
        shown = unread[-CAP:]
        omitted = len(unread) - len(shown)
        header = "📨 New agora messages (since your last turn):"
        if omitted:
            header += f"\n…{omitted} older message(s) omitted — see: python3 \"{path}\" log"
        text = header + "\n" + chat.format_messages(shown, highlight=handle)
        if any(mentions_of(m, handle) for m in shown):
            text += (f'\n\n→ You were mentioned. Reply with: '
                     f'python3 "{path}" send --from {handle} "..."')
        chat.mark_read(conn, sid, unread[-1]["id"])
        parts.append(text)
    if nudge:
        parts.append(nudge)
    emit_context("UserPromptSubmit", "\n\n".join(parts))


try:
    main()
except Exception:
    pass  # never block the user's prompt (a non-zero exit would)
sys.exit(0)
