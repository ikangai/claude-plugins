#!/usr/bin/env python3
"""Regression tests for the two cmd_lead findings the integration-seam audit
surfaced (chat #50/#70) and tesla fixed (chat.py designate branch now refuses a
non-active designee, citing "audit #70"). These pin the fix so it can't regress:
designating an inactive/nonexistent handle is refused (it would otherwise
broadcast "route your @human to @h" while @human actually funnels to the floor —
a silent-question-loss hub-and-spoke failure), while designating an ACTIVE agent
still works (guards against over-refusal).

    python3 tests/leadership_audit_findings_test.py
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, db, env_for, tmp_root  # noqa: E402


def _two_agents(env):
    cli(["register", "--session", "s1", "--from", "ada"], env)
    cli(["register", "--session", "s2", "--from", "turing"], env)


def test_designate_nonexistent_is_refused(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        _two_agents(env)
        r = cli(["lead", "bob", "--from", "ada"], env)
        out = (r.stdout + r.stderr).lower()
        c.check("designating a nonexistent handle is refused (non-zero)",
                r.returncode != 0, f"rc={r.returncode}")
        c.check("refusal explains the lead must be active",
                "active" in out, out[:160])
        # No misadvertising broadcast was posted, and @human still funnels to floor.
        broadcast = cli(["read", "--from", "turing"], env).stdout
        c.check("no false routing broadcast was posted",
                "route to @bob" not in broadcast, broadcast[:160])
        note = cli(["send", "--from", "turing", "@human ping"], env).stdout
        m = re.search(r"redirected to @([a-z0-9_-]+)", note)
        c.check("@human still funnels to the floor lead (ada)",
                m and m.group(1) == "ada", note[:160])


def test_designate_inactive_registered_is_refused(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        _two_agents(env)
        cli(["register", "--session", "s3", "--from", "hopper"], env)
        old = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        conn = db(root)
        conn.execute("UPDATE agents SET last_seen=? WHERE handle='hopper'", (old,))
        conn.commit(); conn.close()
        r = cli(["lead", "hopper", "--from", "ada"], env)
        out = (r.stdout + r.stderr).lower()
        c.check("designating an inactive registered handle is refused",
                r.returncode != 0 and "active" in out, f"rc={r.returncode} {out[:160]}")


def test_designate_active_handle_still_works(c):
    """The fix must not over-refuse: handing off to an ACTIVE agent succeeds and
    broadcasts a routing message that matches where @human actually goes."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        _two_agents(env)
        r = cli(["lead", "turing", "--from", "ada"], env)
        c.check("designating an active agent succeeds", r.returncode == 0, r.stderr)
        # Now ada (a worker) addressing @human funnels to the designated lead turing.
        note = cli(["send", "--from", "ada", "@human ping"], env).stdout
        m = re.search(r"redirected to @([a-z0-9_-]+)", note)
        c.check("a worker's @human now routes to the designated active lead (turing)",
                m and m.group(1) == "turing", note[:160])


def main():
    c = Checker("leadership audit findings (cmd_lead designate — fix confirmed)")
    for fn in (test_designate_nonexistent_is_refused,
               test_designate_inactive_registered_is_refused,
               test_designate_active_handle_still_works):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
