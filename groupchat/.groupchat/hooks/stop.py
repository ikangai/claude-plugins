#!/usr/bin/env python3
"""Stop hook: keep an agent alive until the *team* is done, not just its slice.

Order of business when Claude tries to stop:

1. **Unanswered @mention?** Block and hand the messages back so the agent
   replies. Answering a teammate always wins — never wait while owing a reply.
2. **Nothing pending?** Trying to stop with an empty inbox *is* the "my slice is
   done" signal, so we mark this agent ``done``.
3. **Team barrier.** If every active agent is done, allow the stop — the whole
   team tears down together. Otherwise **park**: block in a sleep-poll loop so
   the finished agent stays alive (dormant, ~0 tokens) and can still receive a
   teammate's later @mention.

The park loop runs a bounded window (< the Stop hook ``timeout``) then returns a
cheap "still waiting" re-park block; Claude spends one trivial turn and re-parks.
A hard ceiling (``MAX_PARK_SECONDS``) releases a forever-parked agent so a
mis-set team size can't hang everyone.

Loop safety: the blocking *sleep* is what prevents a tight spin, so we gate on
the **barrier**, not on ``stop_hook_active``. Everything is wrapped to fail open.
"""
import os
import sys
import json
import time

CAP = 40
# Poll for this long, then re-park (< the Stop hook timeout of 600s). Env-tunable
# (and shrunk in tests). Tick is the barrier / @mention detection latency.
PARK_WINDOW_SECONDS = int(os.environ.get("GROUPCHAT_PARK_WINDOW") or 570)
POLL_TICK_SECONDS = float(os.environ.get("GROUPCHAT_POLL_TICK") or 2)


def _block_on_mention(chat, conn, sid, path) -> bool:
    """If unread @mentions for this agent exist, surface them as a block and
    advance the cursor past all unread. Returns True if it printed a block."""
    from _hooklib import mentions_of
    agent = chat.agent_by_session(conn, sid)
    if not agent:
        return False
    unread = chat.unread_for(conn, agent)
    if not unread or not any(mentions_of(m, agent["handle"]) for m in unread):
        return False
    handle = agent["handle"]
    shown = unread[-CAP:]
    omitted = len(unread) - len(shown)
    chat.mark_read(conn, sid, unread[-1]["id"])
    # The agent is about to do real work again — it's no longer "done".
    chat.set_status(conn, sid, "active")
    body = chat.format_messages(shown, highlight=handle)
    extra = f"\n…{omitted} older message(s) omitted — see `log`." if omitted else ""
    reason = (
        "A teammate mentioned you in the group chat and you haven't replied:\n"
        + body + extra
        + "\n\nAddress the message(s). If a response is warranted, reply with:\n"
        + f'python3 "{path}" send --from {handle} "..."\n'
        + "If no reply is needed, you may stop."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return True


def main():
    import os
    from _hooklib import load_chat, read_input

    data = read_input()
    sid = data.get("session_id")
    if not sid:
        return

    chat = load_chat()
    conn = chat.connect()
    agent = chat.agent_by_session(conn, sid)
    if not agent:
        return  # not registered -> nothing to coordinate, allow stop
    chat.register(conn, sid)  # refresh last-seen

    # Meter token usage for this session (best-effort; never blocks a stop).
    try:
        tp = data.get("transcript_path")
        if tp:
            chat.record_tokens(conn, sid, chat.sum_transcript_tokens(tp))
    except Exception:
        pass

    path = os.path.abspath(chat.__file__)
    park_key = f"park:{sid}"

    # 1. Owe a teammate a reply? Surface it and stop here (don't park).
    if _block_on_mention(chat, conn, sid, path):
        chat.del_meta(conn, park_key)  # doing real work -> reset the park clock
        return

    # 2. P2 — a lead that escalated to the operator is NOT done until answered.
    #    The operator's reply (@<lead>) wakes it via _block_on_mention above; until
    #    then it parks so the team never tears down with a question still open. No
    #    new state — the open queue is derived from the message log; the ceiling
    #    still releases it as a fail-safe if the operator never replies.
    lead = chat.resolve_lead(conn)
    awaiting_operator = bool(
        lead and agent["handle"] == lead and chat.open_escalations(conn, lead))

    # Trying to stop with an empty inbox == "my slice is done" — unless we're the
    # lead still owing the operator a reply.
    chat.set_status(conn, sid, "active" if awaiting_operator else chat.DONE_STATUS)

    # 3. Barrier: exit only when the whole team is done AND we owe the operator
    #    nothing. An awaiting lead keeps the team up so no answer is lost.
    if not awaiting_operator and chat.team_done(conn):
        chat.del_meta(conn, park_key)
        return  # allow stop — everyone is finished

    # Park: block in a sleep-poll loop until something happens or the window ends.
    if not chat.get_meta(conn, park_key):
        chat.set_meta(conn, park_key, chat.now_iso())  # start the continuous-wait clock

    deadline = time.monotonic() + PARK_WINDOW_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_TICK_SECONDS)
        chat.register(conn, sid)  # stay inside the active window while parked

        # A teammate pinged us -> wake, hand it back, let the agent reply.
        if _block_on_mention(chat, conn, sid, path):
            chat.del_meta(conn, park_key)
            return

        # The last teammate finished -> release together.
        if chat.team_done(conn):
            chat.del_meta(conn, park_key)
            return  # allow stop

        # Ceiling: parked too long (e.g. a mis-set team size) -> give up waiting.
        if chat.iso_age_seconds(chat.get_meta(conn, park_key)) >= chat.max_park_seconds():
            chat.del_meta(conn, park_key)
            try:
                chat.send(conn, agent["handle"],
                          f"(left the barrier — waited {chat.max_park_seconds() // 60}m "
                          "with the team still unfinished)", session_id=sid, kind="system")
            except Exception:
                pass
            return  # allow stop

    # Window elapsed with nothing new: cheap re-park so Claude doesn't busy-spin.
    if awaiting_operator:
        n = len(chat.open_escalations(conn, lead))
        reason = (
            f"Parked: you're the lead awaiting the operator's reply on {n} open "
            "@human escalation(s). You'll wake when an operator message @mentions "
            "you; until then the team stays up. You may stop (you'll keep waiting)."
        )
    else:
        waiting = [a["handle"] for a in chat.active_agents(conn)
                   if (a["status"] or "") != chat.DONE_STATUS]
        who = ", ".join(waiting) if waiting else "teammates"
        reason = (
            f"Still waiting at the team barrier — {who} not finished yet. "
            "Nothing for you to do; you may stop (you'll keep waiting)."
        )
    print(json.dumps({"decision": "block", "reason": reason}))


try:
    main()
except Exception:
    pass  # on any error, allow the stop (fail open)
sys.exit(0)
