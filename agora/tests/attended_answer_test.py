#!/usr/bin/env python3
"""An operator's typed prompt at the asker's terminal answers its escalation.

The lead-done gate keeps an asker `active` (never `done`) while it has an open
@human escalation, so the whole team stays up until the operator answers. But an
escalation only cleared via the bus (`answer <id>` / `send --from human "@lead …"`).
The natural operator move at an *attended* session — typing the answer straight
into the asker's own terminal — never touched the bus, so the escalation stayed
open forever: the asker could never go `done`, and every spawned worker sat at
the barrier to the 2h ceiling. (Observed in the wild as a 16-deep escalation
queue nobody knew how to clear.)

Fix: a UserPromptSubmit in the asker's OWN session *is* the operator responding
(same philosophy as attended-never-park: attended == the human channel is this
terminal). The hook posts the operator marker (`sender='human'`, @current-handle,
`[re #id]`) so the queue batch-clears exactly as an `answer` would — visible on
the bus, rename-safe, captain-safe. Attended non-asker sessions instead get a
one-line nudge naming `questions` / `answer` (discoverability: the operator can't
run a verb nobody ever told them about); spawned workers get no nudge.

Run:  python3 tests/attended_answer_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (Checker, GROUPCHAT, cli, db, env_for, hook,  # noqa: E402
                   tmp_root, worker_env)

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402


def room(root):
    """Point the in-process chat module at the isolated room and connect (to
    arrange state — e.g. an @human escalation — that the CLI can't stamp cleanly)."""
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    return chat.connect()


def _prompt(env, sid, root, text="here's my answer: rotate the tokens weekly"):
    return hook("user_prompt_submit.py", env,
                {"session_id": sid, "cwd": root,
                 "hook_event_name": "UserPromptSubmit", "prompt": text})


def _stop(env, sid):
    return hook("stop.py", env,
                {"session_id": sid, "hook_event_name": "Stop",
                 "stop_hook_active": False})


def _context(result) -> str:
    """The additionalContext a UserPromptSubmit hook injected ('' if silent)."""
    out = (result.stdout or "").strip()
    if not out:
        return ""
    try:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def _escalate(root, sid, handle):
    """Open an @human escalation authored by (sid, handle)."""
    conn = room(root)
    chat.send(conn, handle, "@human need a decision on the token rotation",
              session_id=sid)
    conn.close()


_FAST = dict(GROUPCHAT_PARK_WINDOW=1, GROUPCHAT_POLL_TICK=0.1)


def test_prompt_at_asker_clears_escalation(c):
    """A typed prompt in the asker's own session batch-clears its open queue and
    leaves the operator marker on the bus (visible + rename/captain-safe)."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)  # attended lead
        _escalate(root, "s1", "alice")
        c.check("(precondition) the escalation is open for s1",
                len(chat.session_open_escalations(room(root), "s1")) == 1)
        _prompt(env, "s1", root)
        c.check("the prompt cleared the asker's escalation queue",
                chat.session_open_escalations(room(root), "s1") == [])
        conn = db(root)
        marker = conn.execute(
            "SELECT sender, body, mentions FROM messages WHERE sender='human' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        c.check("an operator marker from 'human' @mentioning the asker exists",
                marker is not None and "alice" in (marker["mentions"] or ""),
                f"marker={dict(marker) if marker else None}")
        c.check("...carrying the [re #id] marker (audit + captain-relay shape)",
                marker is not None and "[re #" in marker["body"])


def test_cleared_asker_reaches_done(c):
    """The point of the fix: once the terminal answer cleared the queue, the
    asker's next stop marks it done — it stops pinning teammates at the barrier."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        _escalate(root, "s1", "alice")
        _stop(env, "s1")
        conn = db(root)
        st = conn.execute("SELECT status FROM agents WHERE session_id='s1'").fetchone()
        c.check("(precondition) stopping with an open escalation stays 'active'",
                st and st["status"] != chat.DONE_STATUS, f"status={st and st['status']}")
        _prompt(env, "s1", root)
        _stop(env, "s1")
        conn = db(root)
        st = conn.execute("SELECT status FROM agents WHERE session_id='s1'").fetchone()
        c.check("after the terminal answer, the asker's stop marks it done",
                st and st["status"] == chat.DONE_STATUS, f"status={st and st['status']}")


def test_prompt_elsewhere_nudges_but_never_clears(c):
    """A prompt in a DIFFERENT attended session must not clear the asker's queue
    (that operator may be answering something else) — it gets the discoverability
    nudge naming `questions`/`answer` instead."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)  # the asker
        _escalate(root, "s1", "alice")
        cli(["register", "--session", "s2", "--from", "bob"], env)    # attended peer
        r = _prompt(env, "s2", root)
        c.check("the asker's escalation is still open",
                len(chat.session_open_escalations(room(root), "s1")) == 1)
        ctx = _context(r)
        c.check("the attended peer is told how to answer (questions/answer nudge)",
                "questions" in ctx and "answer" in ctx, f"ctx={ctx!r}")


def test_spawned_worker_gets_no_nudge(c):
    """A bootstrap-spawned worker isn't the operator's terminal — no nudge (its
    initial spawn prompt must not open with operator chores)."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        _escalate(root, "s1", "alice")
        cli(["register", "--session", "s3", "--from", "carol"], worker_env(root))
        r = _prompt(env, "s3", root)
        ctx = _context(r)
        c.check("no operator nudge on a spawned worker's prompt",
                "questions" not in ctx, f"ctx={ctx!r}")
        c.check("...and the asker's escalation is untouched",
                len(chat.session_open_escalations(room(root), "s1")) == 1)


def test_clear_survives_rename(c):
    """The marker targets the asker's CURRENT handle, so a rename between asking
    and answering can't orphan the queue (the v0.10 orphan, attended edition)."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        _escalate(root, "s1", "alice")
        cli(["rename", "--from", "alice", "boss"], env)
        c.check("(precondition) still open after the rename",
                len(chat.session_open_escalations(room(root), "s1")) == 1)
        _prompt(env, "s1", root)
        c.check("the terminal answer clears the renamed asker's queue",
                chat.session_open_escalations(room(root), "s1") == [])


def main():
    c = Checker("attended answer clears the escalation (operator at the terminal)")
    tests = [
        ("prompt_at_asker_clears_escalation", test_prompt_at_asker_clears_escalation),
        ("cleared_asker_reaches_done", test_cleared_asker_reaches_done),
        ("prompt_elsewhere_nudges_but_never_clears",
         test_prompt_elsewhere_nudges_but_never_clears),
        ("spawned_worker_gets_no_nudge", test_spawned_worker_gets_no_nudge),
        ("clear_survives_rename", test_clear_survives_rename),
    ]
    for name, fn in tests:
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
