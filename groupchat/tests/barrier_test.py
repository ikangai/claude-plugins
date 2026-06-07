#!/usr/bin/env python3
"""Team-barrier tests: the rule that a finished agent waits until the *whole
team* is done. This is the most subtle logic in the system and had no test.

Covered: the startup guard (size-based and grace-based), ``team_done`` only
firing when every *active* agent is done, a silent teammate ageing out so it
can't wedge the team, the ``GROUPCHAT_TEAM_SIZE`` > meta precedence, the park
ceiling, and the ``done`` / ``expect`` CLI surfaces.

Uses in-process imports of ``chat`` for fine control of barrier internals (with
direct db edits to age agents), plus subprocess CLI checks. Run:

    python3 tests/barrier_test.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, cli, env_for, tmp_root  # noqa: E402

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402

_TUNABLES = ("GROUPCHAT_TEAM_SIZE", "GROUPCHAT_MAX_PARK",
             "GROUPCHAT_PARK_WINDOW", "GROUPCHAT_POLL_TICK")


def scrub():
    for k in _TUNABLES:
        os.environ.pop(k, None)


def room(root):
    """Point the in-process chat module at an isolated room and connect."""
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    return chat.connect()


def _old(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def set_seen(conn, handle, last_seconds_ago=None, first_seconds_ago=None):
    if last_seconds_ago is not None:
        conn.execute("UPDATE agents SET last_seen=? WHERE handle=?",
                     (_old(last_seconds_ago), handle))
    if first_seconds_ago is not None:
        conn.execute("UPDATE agents SET first_seen=? WHERE handle=?",
                     (_old(first_seconds_ago), handle))
    conn.commit()


def test_empty_and_guard(c):
    scrub()
    with tmp_root() as root:
        conn = room(root)
        c.check("team_done False with no agents", chat.team_done(conn) is False)

        # One agent, unknown size, fresh cohort: the startup grace must BLOCK the
        # barrier even though the lone agent is 'done' (prevents premature exit).
        chat.register(conn, "s1", handle="alice")
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        c.check("fresh cohort + unknown size -> guard blocks barrier",
                chat.startup_guard_satisfied(conn) is False)
        c.check("...so team_done is False", chat.team_done(conn) is False)
        conn.close()


def test_grace_satisfied_by_age(c):
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="alice")
        set_seen(conn, "alice", first_seconds_ago=chat.STARTUP_GRACE_SECONDS + 30)
        chat.set_status(conn, "s1", chat.DONE_STATUS)  # refreshes last_seen -> active
        c.check("aged cohort satisfies the startup grace",
                chat.startup_guard_satisfied(conn) is True)
        c.check("team_done True once grace met and all active done",
                chat.team_done(conn) is True)
        conn.close()


def test_all_active_done_by_size(c):
    scrub()
    os.environ["GROUPCHAT_TEAM_SIZE"] = "2"
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="alice")
        chat.register(conn, "s2", handle="bob")
        c.check("size guard satisfied once N registered",
                chat.startup_guard_satisfied(conn) is True)
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        c.check("not done while a teammate is still active",
                chat.team_done(conn) is False)
        chat.set_status(conn, "s2", chat.DONE_STATUS)
        c.check("done when every active agent is done",
                chat.team_done(conn) is True)
        conn.close()
    scrub()


def test_silent_teammate_ages_out(c):
    scrub()
    os.environ["GROUPCHAT_TEAM_SIZE"] = "2"
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="alice")
        chat.register(conn, "s2", handle="bob")
        # bob goes silent (crashed) — last_seen older than the 15-min window.
        set_seen(conn, "bob", last_seconds_ago=chat.ACTIVE_WINDOW_SECONDS + 120)
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        c.check("crashed teammate falls out of the active set",
                all(a["handle"] != "bob" for a in chat.active_agents(conn)))
        c.check("a silent teammate cannot wedge the barrier forever",
                chat.team_done(conn) is True)
        conn.close()
    scrub()


def test_size_precedence_and_ceiling(c):
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.set_meta(conn, "team_size", "3")
        c.check("expected size reads from meta", chat.expected_team_size(conn) == 3)
        os.environ["GROUPCHAT_TEAM_SIZE"] = "5"
        c.check("env GROUPCHAT_TEAM_SIZE overrides meta",
                chat.expected_team_size(conn) == 5)
        conn.close()
    scrub()
    c.check("default max-park ceiling is 2h", chat.max_park_seconds() == 7200)
    os.environ["GROUPCHAT_MAX_PARK"] = "0"
    c.check("GROUPCHAT_MAX_PARK=0 releases immediately", chat.max_park_seconds() == 0)
    os.environ["GROUPCHAT_MAX_PARK"] = "60"
    c.check("GROUPCHAT_MAX_PARK honoured", chat.max_park_seconds() == 60)
    scrub()


def test_barrier_functions_do_not_raise(c):
    """Regression for the duplicate-``_env_int`` bug (commit 47aef9d): a second
    two-arg ``_env_int`` shadowed the single-arg one, so every barrier call —
    ``expected_team_size`` / ``max_park_seconds`` / ``team_done`` — raised
    TypeError, which stop.py swallowed (fail-open), silently killing the barrier.
    These must simply NOT raise."""
    scrub()
    with tmp_root() as root:
        conn = room(root)
        for name, fn in (
            ("expected_team_size does not raise", lambda: chat.expected_team_size(conn)),
            ("max_park_seconds does not raise", lambda: chat.max_park_seconds()),
            ("team_done does not raise", lambda: chat.team_done(conn)),
        ):
            try:
                fn()
                c.check(name, True)
            except Exception as e:
                c.check(name, False, f"RAISED {type(e).__name__}: {e}")
        conn.close()


def test_done_and_expect_cli(c):
    # done CLI reflects the barrier state (black box, with size set via env).
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=2)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        d1 = cli(["done", "--from", "alice"], env)
        c.check("first done -> waiting for teammates",
                "Waiting for teammates" in d1.stdout, d1.stdout)
        d2 = cli(["done", "--from", "bob"], env)
        c.check("last done -> team all done",
                "Team is all done" in d2.stdout, d2.stdout)
    # expect CLI sets and reports the team size.
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        e1 = cli(["expect", "3"], env)
        c.check("expect N sets the size", "set to 3" in e1.stdout, e1.stdout)
        e2 = cli(["expect"], env)
        c.check("expect (no arg) reports the size",
                "expected team size: 3" in e2.stdout, e2.stdout)
        e3 = cli(["expect"], env_for(root, GROUPCHAT_TEAM_SIZE=9))
        c.check("env overrides reported size",
                "expected team size: 9" in e3.stdout, e3.stdout)


def main():
    c = Checker("team barrier (startup guard / done / age-out / ceiling)")
    # Each test is wrapped: a crash inside chat (e.g. the duplicate-_env_int bug)
    # becomes a clean FAIL for that case instead of aborting the whole suite, so
    # the harness keeps reporting and flips green the moment the bug is fixed.
    tests = [
        ("barrier_functions_do_not_raise", test_barrier_functions_do_not_raise),
        ("empty_and_guard", test_empty_and_guard),
        ("grace_satisfied_by_age", test_grace_satisfied_by_age),
        ("all_active_done_by_size", test_all_active_done_by_size),
        ("silent_teammate_ages_out", test_silent_teammate_ages_out),
        ("size_precedence_and_ceiling", test_size_precedence_and_ceiling),
        ("done_and_expect_cli", test_done_and_expect_cli),
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
