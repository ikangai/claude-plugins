#!/usr/bin/env python3
"""Instance-count awareness tests (goal: peers know how many others are working).

Covers the surfaces that tell an agent / the human how many instances are running:
  * ``who`` prints a team summary (active / done / expected / not-yet-joined),
    and says "working solo" for a lone, undeclared agent;
  * the SessionStart briefing shows the same status and posts a one-line join
    notice when (and only when) joining a NON-empty room;
  * the Stop-hook re-park message states the real cause instead of a literal
    "teammates" when there are none.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/awareness_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, hook, init_room, parse_hook_json, tmp_root,
)


def _join_count(root):
    conn = db(root)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE body LIKE '%joined the room%'"
        ).fetchone()[0]
    finally:
        conn.close()


def test_who_summary(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        # Solo, undeclared -> "working solo".
        who = cli(["who"], env).stdout
        c.check("solo roster says working solo", "working solo" in who.lower(), who)

        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["expect", "3"], env)
        who = cli(["who"], env).stdout
        c.check("summary shows active count", "2 active" in who, who)
        c.check("summary shows the expected size", "expecting 3" in who, who)
        c.check("summary shows who hasn't joined", "1 not yet joined" in who, who)

        cli(["done", "--from", "alice"], env)
        who = cli(["who"], env).stdout
        c.check("summary shows the done count", "1 done" in who, who)

        # Undeclared multi-agent: a plain active count, no "expecting", not "solo".
        cli(["expect", "0"], env)  # clear the declared size
        who = cli(["who"], env).stdout
        c.check("undeclared multi shows a plain active count", "2 active" in who, who)
        c.check("undeclared multi omits 'expecting'", "expecting" not in who, who)
        c.check("a multi-agent room is not labelled solo", "working solo" not in who, who)

        # Size == active: show the expectation but no spurious "not yet joined".
        cli(["expect", "2"], env)
        who = cli(["who"], env).stdout
        c.check("size==active shows 'expecting' without 'not yet joined'",
                "expecting 2" in who and "not yet joined" not in who, who)


def test_briefing_and_join_notice(c):
    with tmp_root() as root:
        env = init_room(root)
        out1 = hook("session_start.py", env,
                    {"session_id": "s1", "cwd": root, "hook_event_name": "SessionStart"})
        ctx1 = parse_hook_json(out1.stdout)["hookSpecificOutput"]["additionalContext"]
        c.check("a solo briefing says working solo", "Working solo" in ctx1, ctx1)
        c.check("a solo join posts NO join notice", _join_count(root) == 0)

        out2 = hook("session_start.py", env,
                    {"session_id": "s2", "cwd": root, "hook_event_name": "SessionStart"})
        ctx2 = parse_hook_json(out2.stdout)["hookSpecificOutput"]["additionalContext"]
        c.check("a non-solo briefing does not say working solo",
                "Working solo" not in ctx2, ctx2)
        c.check("joining a non-empty room posts ONE join notice", _join_count(root) == 1)

        # Resuming an existing session must not re-announce a join.
        hook("session_start.py", env,
             {"session_id": "s1", "cwd": root, "hook_event_name": "SessionStart"})
        c.check("resuming an existing session posts no new join notice",
                _join_count(root) == 1)


def test_park_message_states_real_cause(c):
    # PARK_WINDOW=0 makes the Stop hook skip the sleep-loop and emit its re-park
    # message immediately, so we can assert the wording without waiting.
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_PARK_WINDOW"] = "0"
        # Solo, brand-new cohort (< solo settle grace) -> the lone agent is 'done'
        # but the guard isn't satisfied yet, so `waiting` is empty. The message must
        # NOT claim "teammates" (there are none).
        cli(["register", "--session", "s1", "--from", "alice"], env)
        out = hook("stop.py", env,
                   {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False})
        obj = parse_hook_json(out.stdout) or {}
        reason = obj.get("reason", "")
        c.check("solo settle re-park does not claim 'teammates not finished'",
                "teammates not finished" not in reason, reason)
        c.check("solo settle re-park explains it is settling/assembling",
                any(w in reason.lower() for w in ("settl", "assembl", "startup grace")),
                reason)

    with tmp_root() as root:
        env = init_room(root)
        env = dict(env)
        env["GROUPCHAT_PARK_WINDOW"] = "0"
        env["GROUPCHAT_TEAM_SIZE"] = "2"
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        # alice stops; bob is still active and not done -> alice waits, message names bob.
        out = hook("stop.py", env,
                   {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False})
        obj = parse_hook_json(out.stdout) or {}
        reason = obj.get("reason", "")
        c.check("a real wait names the unfinished teammate",
                "bob" in reason and "not finished" in reason, reason)


def main():
    c = Checker("instance-count awareness (who / briefing / join notice / park msg)")
    for name, fn in (
        ("who_summary", test_who_summary),
        ("briefing_and_join_notice", test_briefing_and_join_notice),
        ("park_message_states_real_cause", test_park_message_states_real_cause),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
