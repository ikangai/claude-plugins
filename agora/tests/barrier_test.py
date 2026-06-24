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
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, cli, env_for, hook, tmp_root  # noqa: E402

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402

_TUNABLES = ("GROUPCHAT_TEAM_SIZE", "GROUPCHAT_MAX_PARK", "GROUPCHAT_SOLO_GRACE",
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
        # The survivor's cohort has aged past the startup grace so the size time
        # fallback can release it once it's alone (the guard counts ACTIVE agents).
        set_seen(conn, "alice", first_seconds_ago=chat.STARTUP_GRACE_SECONDS + 30)
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        # While bob is still in the active set and not done, the team is NOT done —
        # so it's bob LEAVING the active set (not the cohort age) that flips the result.
        c.check("not done while a silent teammate is still active and unfinished",
                chat.team_done(conn) is False)
        # bob goes silent (crashed) — last_seen older than the 15-min active window.
        set_seen(conn, "bob", last_seconds_ago=chat.ACTIVE_WINDOW_SECONDS + 120)
        c.check("crashed teammate falls out of the active set",
                all(a["handle"] != "bob" for a in chat.active_agents(conn)))
        c.check("a silent teammate cannot wedge the barrier forever",
                chat.team_done(conn) is True)
        conn.close()
    scrub()


def test_solo_does_not_wait(c):
    """A lone agent with no declared team size must NOT sit through the full 90s
    startup grace — only a brief solo settle window — else 'working solo' means
    waiting for nobody. (Goal: when solo, don't wait for others.)"""
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="solo")
        # Aged past the solo settle grace but well under the full 90s grace.
        set_seen(conn, "solo", first_seconds_ago=30)
        chat.set_status(conn, "s1", chat.DONE_STATUS)  # refreshes last_seen -> active
        c.check("solo agent past the settle grace satisfies the guard",
                chat.startup_guard_satisfied(conn) is True)
        c.check("...so a solo barrier completes without the full 90s wait",
                chat.team_done(conn) is True)
        conn.close()


def test_solo_settles_briefly(c):
    """Within the brief settle window a lone agent still waits, so a co-launched
    teammate that is a moment behind registering is not dropped."""
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="solo")
        set_seen(conn, "solo", first_seconds_ago=1)  # brand-new cohort
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        c.check("a brand-new solo agent still settles briefly",
                chat.startup_guard_satisfied(conn) is False)
        conn.close()


def test_declared_size_unmet_releases_after_grace(c):
    """A declared size that never fully assembles must release at the startup grace,
    NOT hang the whole team until the 2h ceiling (the D2 deadlock)."""
    scrub()
    os.environ["GROUPCHAT_TEAM_SIZE"] = "3"
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="alice")
        chat.register(conn, "s2", handle="bob")  # only 2 of the 3 ever show up
        set_seen(conn, "alice", first_seconds_ago=chat.STARTUP_GRACE_SECONDS + 30)
        set_seen(conn, "bob", first_seconds_ago=chat.STARTUP_GRACE_SECONDS + 30)
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        chat.set_status(conn, "s2", chat.DONE_STATUS)
        c.check("unmet declared size satisfies the guard after the grace",
                chat.startup_guard_satisfied(conn) is True)
        c.check("...so an unmet team releases at ~90s, not the 2h ceiling",
                chat.team_done(conn) is True)
        conn.close()
    scrub()


def test_declared_size_waits_within_grace(c):
    """Within the grace, a declared-but-unmet size still blocks so a slow latecomer
    has time to register."""
    scrub()
    os.environ["GROUPCHAT_TEAM_SIZE"] = "3"
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="alice")
        chat.register(conn, "s2", handle="bob")  # fresh cohort, 2 of 3
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        chat.set_status(conn, "s2", chat.DONE_STATUS)
        c.check("fresh unmet size blocks the barrier (wait for the latecomer)",
                chat.startup_guard_satisfied(conn) is False)
        conn.close()
    scrub()


def test_size_guard_counts_active_not_ghosts(c):
    """The declared-size guard must count ACTIVE agents, not all-time rows — a stale
    ghost row from a prior run must never trivially satisfy it (premature exit)."""
    scrub()
    os.environ["GROUPCHAT_TEAM_SIZE"] = "2"
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="alice")
        chat.register(conn, "s2", handle="ghost")
        # ghost is a dead row from a prior run; alice is the only live agent, fresh.
        set_seen(conn, "ghost", last_seconds_ago=chat.ACTIVE_WINDOW_SECONDS + 120)
        chat.set_status(conn, "s1", chat.DONE_STATUS)
        c.check("only one agent is actually active",
                len(chat.active_agents(conn)) == 1)
        c.check("a ghost row does NOT satisfy the size guard",
                chat.startup_guard_satisfied(conn) is False)
        c.check("...so the barrier does not prematurely complete",
                chat.team_done(conn) is False)
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


def test_stale_team_size_cleared_when_old(c):
    """A leftover size from a long-departed team (stamped > one active-window ago) is
    cleared when a fresh solo agent arrives, so a quick solo session in a REUSED room
    isn't routed into the 90s wait."""
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.set_meta(conn, "team_size", "3")
        chat.set_meta(conn, "team_size_at",
                      _old(chat.ACTIVE_WINDOW_SECONDS + 60))  # declared long ago
        chat.register(conn, "s_new", handle="solo")  # a fresh solo agent arrives
        c.check("a stale (old) declared size is cleared for a fresh solo agent",
                chat.expected_team_size(conn) is None)
        set_seen(conn, "solo", first_seconds_ago=30)
        chat.set_status(conn, "s_new", chat.DONE_STATUS)
        c.check("the fresh solo agent then settles at the solo grace, not 90s",
                chat.startup_guard_satisfied(conn) is True)
        conn.close()


def test_fresh_declared_size_survives_first_agent(c):
    """REGRESSION: a freshly-declared size (set_team_size stamps it 'now') MUST survive
    the first agent registering alone — erasing it would silently destroy the
    coordination signal and re-open the ragged-startup race the guard exists to close."""
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.set_team_size(conn, 3)  # fresh declaration (stamped now)
        chat.register(conn, "s1", handle="first")  # first teammate registers alone
        c.check("a freshly-declared size survives the first lone registration",
                chat.expected_team_size(conn) == 3)
        conn.close()


def test_team_size_survives_a_real_cohort(c):
    """The clear must NOT fire when joining a room that already has an active agent —
    a declared size for a still-assembling team has to survive."""
    scrub()
    with tmp_root() as root:
        conn = room(root)
        chat.register(conn, "s1", handle="boss")  # an agent is already here
        chat.set_meta(conn, "team_size", "3")      # and a size is declared
        chat.register(conn, "s2", handle="worker")  # a teammate joins a non-empty room
        c.check("a declared size survives a teammate joining",
                chat.expected_team_size(conn) == 3)
        conn.close()


def test_expect_survives_first_agent(c):
    """REGRESSION (CLI): `expect N` then the first agent registering alone must not
    wipe N — the documented declare-then-launch path must survive."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["expect", "3"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        out = cli(["expect"], env).stdout
        c.check("expect N survives the first agent registering alone",
                "expected team size: 3" in out, out)


def test_park_sleep_clamped_to_deadline(c):
    """A large POLL_TICK must not overshoot the park window — an overshoot would blow
    past the 600s Stop-hook timeout, the host kills the hook with no block emitted,
    and the agent is released early (a later @mention dropped)."""
    scrub()
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_PARK_WINDOW=1, GROUPCHAT_POLL_TICK=5,
                      GROUPCHAT_SOLO_GRACE=600)  # force a park, tiny window, huge tick
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        t0 = time.monotonic()
        hook("stop.py", env,
             {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
             timeout=20)
        dt = time.monotonic() - t0
        c.check("park honours the window, not the longer poll tick",
                dt < 3.0, f"took {dt:.1f}s (expected ~1s; unclamped would be ~5s)")


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
        ("solo_does_not_wait", test_solo_does_not_wait),
        ("solo_settles_briefly", test_solo_settles_briefly),
        ("declared_size_unmet_releases_after_grace",
         test_declared_size_unmet_releases_after_grace),
        ("declared_size_waits_within_grace", test_declared_size_waits_within_grace),
        ("size_guard_counts_active_not_ghosts", test_size_guard_counts_active_not_ghosts),
        ("stale_team_size_cleared_when_old", test_stale_team_size_cleared_when_old),
        ("fresh_declared_size_survives_first_agent",
         test_fresh_declared_size_survives_first_agent),
        ("team_size_survives_a_real_cohort", test_team_size_survives_a_real_cohort),
        ("expect_survives_first_agent", test_expect_survives_first_agent),
        ("size_precedence_and_ceiling", test_size_precedence_and_ceiling),
        ("park_sleep_clamped_to_deadline", test_park_sleep_clamped_to_deadline),
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
