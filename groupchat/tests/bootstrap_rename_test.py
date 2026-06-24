#!/usr/bin/env python3
"""Bootstrap + rename tests — the team-spawn and runtime-rename features.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/bootstrap_rename_test.py     # exit 0 = all pass

Two features under test:
  * `rename` — change a live session's handle in place, mirroring register()'s
    identity rules (sanitize / reserved-reject / active-collision-reject /
    inactive-reclaim), with leadership and the read cursor following the rename.
  * `bootstrap` — pick free team-member handles and (here, via --dry-run/print, so
    nothing actually launches) emit the right `AGORA_HANDLE=… claude` commands.
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
              r.stdout.count("AGORA_HANDLE=") == 3, r.stdout)

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


def test_bootstrap_declares_team_size():
    print("bootstrap declares team size (gate + env override):")
    import argparse
    import contextlib
    import io
    sys.path.insert(0, os.path.join(ROOT, ".groupchat"))
    import chat  # noqa: E402

    def _ns(**kw):
        base = dict(spec=None, method="terminal", cwd="/x", prompt=None,
                    dry_run=False, worktree=False, force=False)
        base.update(kw)
        return argparse.Namespace(**base)

    # Stub the launcher so nothing real spawns but ok>0 (as a real terminal would),
    # and the join-poll so it returns instantly instead of waiting ~5s for the
    # never-launched phantom agents to register.
    orig, orig_poll = chat.spawn_agents, chat.poll_joined
    chat.spawn_agents = lambda names, cwd, **kw: [
        {"name": n, "command": "c", "ok": True, "error": None} for n in names]
    chat.poll_joined = lambda conn, names, **kw: {n: True for n in names}
    try:
        def _seed(root):
            os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
            os.environ.pop("GROUPCHAT_TEAM_SIZE", None)
            conn = chat.connect()
            chat.register(conn, "sb", handle="boss")
            conn.close()

        def _bootstrap(**ns):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                chat.cmd_bootstrap(_ns(**ns))
            return buf.getvalue()

        with tempfile.TemporaryDirectory() as root:
            _seed(root)
            _bootstrap(spec=["2"], method="terminal")
            conn = chat.connect(); size = chat.expected_team_size(conn); conn.close()
            check("a real spawn declares size = active + spawned", size == 3, str(size))

        with tempfile.TemporaryDirectory() as root:
            _seed(root)
            _bootstrap(spec=["2"], method="print")
            conn = chat.connect(); size = chat.expected_team_size(conn); conn.close()
            check("--method print declares no team size (preview only)",
                  size is None, str(size))

        with tempfile.TemporaryDirectory() as root:
            _seed(root)
            _bootstrap(spec=["2"], dry_run=True)
            conn = chat.connect(); size = chat.expected_team_size(conn); conn.close()
            check("--dry-run declares no team size", size is None, str(size))

        with tempfile.TemporaryDirectory() as root:
            _seed(root)
            os.environ["GROUPCHAT_TEAM_SIZE"] = "9"
            out = _bootstrap(spec=["2"], method="terminal")
            check("warns when $GROUPCHAT_TEAM_SIZE overrides the declared size",
                  "overrides" in out and "9" in out, out)
    finally:
        chat.spawn_agents = orig
        chat.poll_joined = orig_poll
        os.environ.pop("GROUPCHAT_TEAM_SIZE", None)
        os.environ.pop("GROUPCHAT_DIR", None)


def test_poll_joined():
    print("bootstrap join poll:")
    sys.path.insert(0, os.path.join(ROOT, ".groupchat"))
    import chat  # noqa: E402
    with tempfile.TemporaryDirectory() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            chat.register(conn, "s_here", handle="here")
            joined = chat.poll_joined(conn, ["here", "missing"], timeout=0.5, tick=0.1)
            check("an already-registered teammate reports joined",
                  joined.get("here") is True, str(joined))
            check("a teammate that never registers reports not-yet",
                  joined.get("missing") is False, str(joined))
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_bootstrap_worktree_dry_run():
    print("bootstrap --worktree (dry-run — nothing launches):")
    with tempfile.TemporaryDirectory() as root:
        env = env_for(root)
        run(["init"], env)
        r = run(["bootstrap", "2", "--worktree", "--dry-run",
                 "--cwd", "/repo/proj"], env)
        check("emits a `git worktree add` per agent",
              r.stdout.count("worktree add") == 2, r.stdout)
        check("creates a groupchat/<name> branch per agent",
              "groupchat/ada" in r.stdout and "groupchat/turing" in r.stdout, r.stdout)
        check("each agent launches cd'd into its own worktree dir",
              "proj-worktrees" in r.stdout, r.stdout)
        check("still emits a GROUPCHAT_HANDLE launch per agent",
              r.stdout.count("AGORA_HANDLE=") == 2, r.stdout)


def test_worktree_creation_isolates_files():
    print("bootstrap --worktree (real git isolation):")
    import subprocess as sp
    sys.path.insert(0, os.path.join(ROOT, ".groupchat"))
    import chat  # noqa: E402
    with tempfile.TemporaryDirectory() as root:
        repo = os.path.join(root, "proj")
        os.makedirs(repo)
        sp.run(["git", "init", "-q", repo], check=True)
        sp.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True)
        sp.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
        with open(os.path.join(repo, "f.txt"), "w") as fh:
            fh.write("main\n")
        sp.run(["git", "-C", repo, "add", "-A"], check=True)
        sp.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)

        wt = chat._worktree_path(repo, "ada")
        err = chat._create_worktree(repo, wt, "ada")
        check("worktree created without error", err is None, str(err))
        check("worktree dir exists", os.path.isdir(wt))

        with open(os.path.join(wt, "f.txt"), "w") as fh:
            fh.write("changed-by-ada\n")
        with open(os.path.join(repo, "f.txt")) as fh:
            main_contents = fh.read()
        check("the main working tree is unaffected by a worktree edit",
              main_contents == "main\n", main_contents)

        br = sp.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True).stdout.strip()
        check("the worktree is on its own groupchat/<name> branch",
              br == "groupchat/ada", br)


def test_worktree_no_stale_branch_reuse():
    print("bootstrap --worktree (no stale-branch reuse):")
    import subprocess as sp
    sys.path.insert(0, os.path.join(ROOT, ".groupchat"))
    import chat  # noqa: E402
    with tempfile.TemporaryDirectory() as root:
        repo = os.path.join(root, "proj")
        os.makedirs(repo)
        sp.run(["git", "init", "-q", repo], check=True)
        sp.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True)
        sp.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
        with open(os.path.join(repo, "f.txt"), "w") as fh:
            fh.write("c1\n")
        sp.run(["git", "-C", repo, "add", "-A"], check=True)
        sp.run(["git", "-C", repo, "commit", "-qm", "c1"], check=True)

        wt1 = chat._worktree_path(repo, "ada")
        check("first worktree created", chat._create_worktree(repo, wt1, "ada") is None)
        # The worktree departs (dir removed) but branch groupchat/ada lingers at c1.
        sp.run(["git", "-C", repo, "worktree", "remove", wt1, "--force"], check=True)
        with open(os.path.join(repo, "f.txt"), "w") as fh:
            fh.write("c2\n")  # main advances; the lingering branch is now a stale base
        sp.run(["git", "-C", repo, "commit", "-aqm", "c2"], check=True)

        wt2 = os.path.join(root, "proj-worktrees-2", "ada")
        err = chat._create_worktree(repo, wt2, "ada")
        check("re-bootstrap does NOT silently reuse a stale branch (reports instead)",
              err is not None, str(err))


def test_worktree_failure_skips_agent():
    print("bootstrap --worktree (failure reported, agent skipped):")
    sys.path.insert(0, os.path.join(ROOT, ".groupchat"))
    import chat  # noqa: E402
    with tempfile.TemporaryDirectory() as root:
        nongit = os.path.join(root, "plain")
        os.makedirs(nongit)
        res = chat.spawn_agents(["ada"], cwd=nongit, method="terminal", worktree=True)
        check("a failed worktree skips the agent (never silent shared-cwd fallback)",
              bool(res) and res[0]["ok"] is False, str(res))
        check("...and reports the git error", bool(res[0]["error"]), str(res))
        check("...and leaves no empty worktree dir behind",
              not os.path.isdir(os.path.join(root, "plain-worktrees")),
              os.listdir(root))


def main():
    test_rename()
    test_active_collision_and_reclaim()
    test_lead_and_cursor_follow()
    test_bootstrap_dry_run()
    test_bootstrap_cap()
    test_bootstrap_declares_team_size()
    test_poll_joined()
    test_bootstrap_worktree_dry_run()
    test_worktree_creation_isolates_files()
    test_worktree_no_stale_branch_reuse()
    test_worktree_failure_skips_agent()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("all bootstrap + rename tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
