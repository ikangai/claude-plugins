#!/usr/bin/env python3
"""Bootstrap + rename tests — the team-spawn and runtime-rename features.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/bootstrap_rename_test.py     # exit 0 = all pass

Two features under test:
  * `rename` — change a live session's handle in place, mirroring register()'s
    identity rules (sanitize / reserved-reject / active-collision-reject /
    inactive-reclaim), with leadership and the read cursor following the rename.
  * `bootstrap` — pick free team-member handles and (here, via --dry-run/print, so
    nothing actually launches) emit the right `GROUPCHAT_HANDLE=… claude` commands.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHAT = os.path.join(ROOT, ".groupchat", "chat.py")

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        _failures.append(name)


def env_for(root, **extra):
    env = dict(os.environ)
    env["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("GROUPCHAT_LEAD", None)
    env.pop("GROUPCHAT_HANDLE", None)
    env.update(extra)
    return env


def run(args, env):
    return subprocess.run([sys.executable, CHAT, *args],
                          capture_output=True, text=True, env=env)


def db(root):
    return sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))


def register(env, handle):
    run(["register", "--session", f"s_{handle}", "--from", handle], env)


def age_out(root, handle):
    """Make ``handle`` look inactive by backdating its last_seen far past the window."""
    conn = db(root)
    conn.execute("UPDATE agents SET last_seen = ? WHERE handle = ?",
                 ("2000-01-01T00:00:00Z", handle))
    conn.commit()
    conn.close()


def spawn_names(out):
    """Pull the spawned/would-spawn handles out of a bootstrap report."""
    names = []
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("✓ ") or s.startswith("✗ "):
            names.append(s[2:].strip())
    return names


def test_rename():
    print("rename:")
    with tempfile.TemporaryDirectory() as root:
        env = env_for(root)
        run(["init"], env)
        register(env, "ada")

        r = run(["rename", "--from", "ada", "frontend"], env)
        check("rename changes the handle", "ada → frontend" in r.stdout, r.stdout + r.stderr)
        who = run(["who"], env).stdout
        check("who shows the new handle, not the old",
              "frontend" in who and "ada" not in who, who)

        r = run(["rename", "--from", "frontend", "frontend"], env)
        check("renaming to your own name is a no-op", "no change" in r.stdout, r.stdout)

        r = run(["rename", "--from", "frontend", "human"], env)
        check("reserved handle 'human' is rejected", r.returncode != 0 and "reserved" in r.stderr,
              r.stdout + r.stderr)

        r = run(["rename", "--from", "frontend", "Back-End 2!"], env)
        who = run(["who"], env).stdout
        check("the new handle is sanitized to [a-z0-9_-]", "back-end2" in who, r.stdout + who)


def test_active_collision_and_reclaim():
    print("rename collisions:")
    with tempfile.TemporaryDirectory() as root:
        env = env_for(root)
        run(["init"], env)
        register(env, "ada")
        register(env, "bob")

        r = run(["rename", "--from", "bob", "ada"], env)
        check("cannot rename onto an ACTIVE teammate's handle",
              r.returncode != 0 and "active" in r.stderr, r.stdout + r.stderr)

        # An inactive holder's name is reclaimable.
        register(env, "ghost")
        age_out(root, "ghost")
        r = run(["rename", "--from", "bob", "ghost"], env)
        check("reclaims an INACTIVE holder's handle", "bob → ghost" in r.stdout, r.stdout + r.stderr)


def test_lead_and_cursor_follow():
    print("rename — lead + cursor follow:")
    with tempfile.TemporaryDirectory() as root:
        env = env_for(root)
        run(["init"], env)
        register(env, "ada")
        register(env, "bob")

        run(["lead", "ada"], env)
        run(["rename", "--from", "ada", "chief"], env)
        lead = run(["lead"], env).stdout
        check("the lead pointer follows the rename", "@chief" in lead, lead)

        # Cursor: ada reads to catch up, renames, cursor must be unchanged.
        run(["send", "--from", "bob", "hello team"], env)
        run(["read", "--from", "chief"], env)  # advance chief's cursor past #?
        conn = db(root)
        before = conn.execute("SELECT last_read_id FROM agents WHERE handle='chief'").fetchone()[0]
        conn.close()
        run(["rename", "--from", "chief", "boss"], env)
        conn = db(root)
        after = conn.execute("SELECT last_read_id FROM agents WHERE handle='boss'").fetchone()[0]
        same_session = conn.execute("SELECT session_id FROM agents WHERE handle='boss'").fetchone()[0]
        conn.close()
        check("the read cursor survives the rename", before == after, f"{before} != {after}")
        check("the session id is unchanged by the rename", same_session == "s_ada", same_session)


def test_bootstrap_dry_run():
    print("bootstrap (dry-run / print — nothing launches):")
    with tempfile.TemporaryDirectory() as root:
        env = env_for(root)
        run(["init"], env)

        r = run(["bootstrap", "3", "--dry-run"], env)
        names = spawn_names(r.stdout)
        check("count picks 3 distinct free handles",
              len(names) == 3 and len(set(names)) == 3, r.stdout)
        check("dry-run reports 'would spawn'", "would spawn 3/3" in r.stdout, r.stdout)
        check("dry-run emits a GROUPCHAT_HANDLE launch command per agent",
              r.stdout.count("GROUPCHAT_HANDLE=") == 3, r.stdout)

        # Explicit names: an active handle gets collision-suffixed.
        register(env, "ada")
        r = run(["bootstrap", "ada", "qa", "--method", "print"], env)
        names = spawn_names(r.stdout)
        check("explicit name colliding with an active agent is suffixed",
              "ada-2" in names and "qa" in names, r.stdout)

        r = run(["bootstrap"], env)
        check("no count and no names is an error", r.returncode != 0, r.stdout + r.stderr)


def test_bootstrap_cap():
    print("bootstrap cap:")
    with tempfile.TemporaryDirectory() as root:
        env = env_for(root)
        run(["init"], env)
        r = run(["bootstrap", "9", "--dry-run"], env)
        check("over the cap is refused without --force",
              r.returncode != 0 and "cap" in r.stderr, r.stdout + r.stderr)
        r = run(["bootstrap", "9", "--dry-run", "--force"], env)
        check("--force overrides the cap", "would spawn 9/9" in r.stdout, r.stdout + r.stderr)
        # 'team' is an alias for 'bootstrap'.
        r = run(["team", "2", "--dry-run"], env)
        check("`team` aliases `bootstrap`", "would spawn 2/2" in r.stdout, r.stdout + r.stderr)


def main():
    test_rename()
    test_active_collision_and_reclaim()
    test_lead_and_cursor_follow()
    test_bootstrap_dry_run()
    test_bootstrap_cap()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("all bootstrap + rename tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
