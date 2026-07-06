#!/usr/bin/env python3
"""Attended terminals never park.

The Stop-hook park loop is a *blocking* ``time.sleep`` — while it runs, Claude
Code is waiting for the hook to return, so the human's typed prompt is queued and
the terminal is frozen. That freeze only earns its keep for an *unattended*,
bootstrap-spawned worker (freezing is invisible; the payoff is waking it on a
later @mention). For the session a human is sitting at, freezing is pure downside
— and for a solo lead parking to wait for the operator's answer to its *own*
``@human`` it's an outright deadlock (the frozen terminal is exactly where that
answer would be typed).

So a human-launched session (``spawned_by IS NULL``) must never park: it sets its
barrier status as before, then returns control. Only a spawned worker parks. The
tri-state ``AGORA_PARK`` env overrides the heuristic in either direction.

Drives the real ``stop.py`` via the hook harness. Run:

    python3 tests/attended_no_park_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (Checker, GROUPCHAT, cli, env_for, hook,  # noqa: E402
                   parse_hook_json, tmp_root)

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402


def room(root):
    """Point the in-process chat module at the isolated room and connect (to
    arrange state — e.g. an @human escalation — that the CLI can't stamp cleanly)."""
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    return chat.connect()


def _stop(env, sid, timeout=20):
    return hook("stop.py", env,
                {"session_id": sid, "hook_event_name": "Stop",
                 "stop_hook_active": False}, timeout=timeout)


def _parked(result) -> bool:
    """The hook parked iff it emitted a block (a re-park / still-waiting message)."""
    j = parse_hook_json(result.stdout)
    return bool(j and j.get("decision") == "block")


# Small park window so that IF the fix regresses (an attended agent parks), the
# test fails in ~1s instead of hanging out the full 570s window.
_FAST = dict(GROUPCHAT_PARK_WINDOW=1, GROUPCHAT_POLL_TICK=0.1)


def test_attended_agent_never_parks(c):
    """A human-launched agent (spawned_by NULL) with the team NOT done must allow
    the stop, not freeze the terminal in the park loop."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)  # attended
        cli(["register", "--session", "s2", "--from", "bob"], env)    # keeps team not-done
        t0 = time.monotonic()
        r = _stop(env, "s1")
        dt = time.monotonic() - t0
        c.check("attended agent allows the stop (no park block)",
                not _parked(r), f"stdout={r.stdout!r}")
        c.check("...and returns promptly (didn't sit in the park loop)",
                dt < 3.0, f"took {dt:.1f}s")


def test_attended_solo_escalation_never_parks(c):
    """The reported deadlock: a solo lead with an open @human escalation parks to
    wait for the operator's answer — but the operator answers by typing into this
    very terminal. Attended => never park, so the human can answer."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)  # solo, attended
        conn = room(root)
        # Solo agent is the emergent lead, so its @human passes through and opens
        # an escalation keyed to session s1.
        chat.send(conn, "alice", "@human need a decision on the token rotation",
                  session_id="s1")
        conn.close()
        c.check("(precondition) the escalation is open for s1",
                len(chat.session_open_escalations(room(root), "s1")) == 1)
        t0 = time.monotonic()
        r = _stop(env, "s1")
        dt = time.monotonic() - t0
        c.check("attended lead with an open @human allows the stop (no deadlock)",
                not _parked(r), f"stdout={r.stdout!r}")
        c.check("...promptly", dt < 3.0, f"took {dt:.1f}s")


def test_spawned_worker_still_parks(c):
    """A bootstrap-spawned worker (spawned_by set) with the team not done must
    still park — the barrier is intact for unattended fleets."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator"))  # spawned worker
        cli(["register", "--session", "s2", "--from", "bob"], env)  # team not-done
        r = _stop(env, "s1")
        c.check("spawned worker still parks at the barrier",
                _parked(r), f"stdout={r.stdout!r}")


def test_agora_park_forces_parking_on_attended(c):
    """AGORA_PARK=1 restores the old behavior: an attended agent parks anyway."""
    with tmp_root() as root:
        env = env_for(root, AGORA_PARK=1, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)  # attended
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = _stop(env, "s1")
        c.check("AGORA_PARK=1 forces an attended agent to park",
                _parked(r), f"stdout={r.stdout!r}")


def test_agora_park_off_forces_no_park_on_spawned(c):
    """AGORA_PARK=0 is the inverse override: a spawned worker never parks (e.g. a
    human who deliberately drives a spawned session as their primary terminal)."""
    with tmp_root() as root:
        env = env_for(root, AGORA_PARK=0, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator"))
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = _stop(env, "s1")
        c.check("AGORA_PARK=0 forces a spawned worker to allow the stop",
                not _parked(r), f"stdout={r.stdout!r}")


def main():
    c = Checker("attended terminals never park (interactive session not frozen)")
    tests = [
        ("attended_agent_never_parks", test_attended_agent_never_parks),
        ("attended_solo_escalation_never_parks",
         test_attended_solo_escalation_never_parks),
        ("spawned_worker_still_parks", test_spawned_worker_still_parks),
        ("agora_park_forces_parking_on_attended",
         test_agora_park_forces_parking_on_attended),
        ("agora_park_off_forces_no_park_on_spawned",
         test_agora_park_off_forces_no_park_on_spawned),
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
