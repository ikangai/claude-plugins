#!/usr/bin/env python3
"""Core transport tests: identity, messaging, the read cursor, and inbox.

This is the heart of the product — the single monotonic ``last_read_id`` cursor
delivery model (CLAUDE.md: "surface, then advance past everything surfaced") and
the handle pool. None of it had a regression test before. Run:

    python3 tests/transport_test.py     # exit 0 = all pass
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, env_for, init_room, tmp_root  # noqa: E402


def test_init_and_identity(c):
    with tmp_root() as root:
        env = env_for(root)
        r = cli(["init"], env)
        c.check("init exits 0", r.returncode == 0, r.stderr)
        c.check("init creates chat.db",
                os.path.isfile(os.path.join(root, ".groupchat", "chat.db")))

        # Automatic handle assignment follows the pool order (ada, turing, ...).
        h1 = cli(["register", "--session", "s1"], env).stdout.strip()
        h2 = cli(["register", "--session", "s2"], env).stdout.strip()
        c.check("first agent gets pool[0]=ada", h1 == "ada", h1)
        c.check("second agent gets pool[1]=turing", h2 == "turing", h2)

        # Registration is idempotent: same session keeps its handle.
        h1b = cli(["register", "--session", "s1"], env).stdout.strip()
        c.check("re-register keeps the same handle", h1b == "ada", h1b)

        # whoami resolves by session and by handle.
        w = cli(["whoami", "--session", "s1"], env).stdout.strip()
        c.check("whoami by session", w == "ada", w)
        wun = cli(["whoami", "--session", "nope"], env).stdout.strip()
        c.check("whoami unknown session -> (unregistered)", wun == "(unregistered)", wun)


def test_preferred_handle_and_collision(c):
    with tmp_root() as root:
        env = init_room(root)
        a = cli(["register", "--session", "s1", "--from", "Custom_Name"], env).stdout.strip()
        c.check("preferred handle is sanitized/lowercased",
                a == "custom_name", a)
        # A collision on a preferred name appends -2.
        b = cli(["register", "--session", "s2", "--from", "custom_name"], env).stdout.strip()
        c.check("preferred collision -> name-2", b == "custom_name-2", b)


def test_send_and_mentions(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = cli(["send", "--from", "alice", "hello @bob how goes"], env)
        c.check("send reports an id", r.stdout.strip().startswith("sent #"), r.stdout)
        c.check("send exits 0", r.returncode == 0, r.stderr)

        # bob (registered before the send) sees it as unread; alice does not see her own.
        rb = cli(["read", "--from", "bob"], env)
        c.check("recipient sees the message", "hello @bob" in rb.stdout, rb.stdout)
        c.check("message renders the mention arrow", "@bob" in rb.stdout, rb.stdout)
        ra = cli(["read", "--from", "alice"], env)
        c.check("sender does NOT see her own message",
                "no new messages" in ra.stdout, ra.stdout)

        # empty send is rejected (non-zero, nothing posted).
        re = cli(["send", "--from", "alice", "   "], env)
        c.check("empty send is rejected", re.returncode != 0, re.stdout)


def test_cursor_surface_then_advance(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["send", "--from", "alice", "m1"], env)
        cli(["send", "--from", "alice", "m2"], env)

        # First read surfaces both, then advances the cursor past them.
        r1 = cli(["read", "--from", "bob"], env)
        c.check("first read surfaces both messages",
                "m1" in r1.stdout and "m2" in r1.stdout, r1.stdout)
        r2 = cli(["read", "--from", "bob"], env)
        c.check("second read shows nothing (cursor advanced)",
                "no new messages" in r2.stdout, r2.stdout)

        # New message after the cursor is surfaced once.
        cli(["send", "--from", "alice", "m3"], env)
        r3 = cli(["read", "--from", "bob"], env)
        c.check("only the new message is surfaced",
                "m3" in r3.stdout and "m1" not in r3.stdout, r3.stdout)


def test_peek_does_not_advance(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["send", "--from", "alice", "peekme"], env)
        p1 = cli(["read", "--from", "bob", "--peek"], env)
        c.check("peek surfaces the message", "peekme" in p1.stdout, p1.stdout)
        p2 = cli(["read", "--from", "bob", "--peek"], env)
        c.check("peek again still shows it (cursor not advanced)",
                "peekme" in p2.stdout, p2.stdout)
        # A real read now advances.
        cli(["read", "--from", "bob"], env)
        p3 = cli(["read", "--from", "bob"], env)
        c.check("after a real read the cursor has advanced",
                "no new messages" in p3.stdout, p3.stdout)


def test_new_agent_starts_caught_up(c):
    """A newly-registered agent must NOT see all prior history as unread — its
    cursor starts at the current max id. This is the invariant that keeps a late
    joiner from being flooded."""
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        for i in range(5):
            cli(["send", "--from", "alice", f"history-{i}"], env)
        # carol joins only now.
        cli(["register", "--session", "s9", "--from", "carol"], env)
        rc = cli(["read", "--from", "carol"], env)
        c.check("late joiner sees no backlog as unread",
                "no new messages" in rc.stdout, rc.stdout)
        # but does see a message sent AFTER joining.
        cli(["send", "--from", "alice", "after-join"], env)
        rc2 = cli(["read", "--from", "carol"], env)
        c.check("late joiner sees messages sent after joining",
                "after-join" in rc2.stdout, rc2.stdout)


def test_inbox_only_mentions(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["register", "--session", "s3", "--from", "carol"], env)
        cli(["send", "--from", "alice", "broadcast no mention"], env)
        cli(["send", "--from", "alice", "ping @bob please"], env)
        # bob's inbox shows only the mention, not the broadcast.
        ib = cli(["inbox", "--from", "bob"], env)
        c.check("inbox shows the mention", "ping @bob" in ib.stdout, ib.stdout)
        c.check("inbox excludes the broadcast",
                "broadcast" not in ib.stdout, ib.stdout)
        # carol, unmentioned, has no inbox items.
        ic = cli(["inbox", "--from", "carol"], env)
        c.check("unmentioned agent has empty inbox",
                "no unread mentions" in ic.stdout, ic.stdout)


def test_read_requires_identity(c):
    with tmp_root() as root:
        env = init_room(root)
        r = cli(["read", "--from", "ghost"], env)
        c.check("read by an unregistered handle errors cleanly",
                r.returncode != 0 and "no agent identity" in (r.stdout + r.stderr),
                r.stdout + r.stderr)


def main():
    c = Checker("transport (identity / messaging / cursor / inbox)")
    test_init_and_identity(c)
    test_preferred_handle_and_collision(c)
    test_send_and_mentions(c)
    test_cursor_surface_then_advance(c)
    test_peek_does_not_advance(c)
    test_new_agent_starts_caught_up(c)
    test_inbox_only_mentions(c)
    test_read_requires_identity(c)
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
