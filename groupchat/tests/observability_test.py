#!/usr/bin/env python3
"""Phase-4 collision-safety & observability.

The roster showed liveness + tokens but never WHAT each instance is doing, nothing
warned when two agents shared a working tree, there was no structured file-claim, and
the only health signal was the binary 15-min ageout. Phase 4 adds:

  * **focus** — a per-agent "what I'm on right now" field (NOT the barrier status
    column), shown in `who` and the briefing;
  * **shared-cwd warning** — `who`/briefing flag when active agents share a working
    tree (the high-collision config), so they coordinate or use a worktree;
  * **claims ledger** — `claim <glob>` / `unclaim` / `claims`: a structured "I'm
    editing these files" surfaced to teammates, with a `path_claimed_by` lookup;
  * **amber dot** — `who` shows ◐ for an agent that's active but has gone quiet
    (no chat for a while) — a soft stuck/heads-down signal between ● and ○.

All dormant-until-used. Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/observability_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, env_for, hook, init_room, parse_hook_json, tmp_root,
)


def _briefing(env, sid, cwd):
    out = hook("session_start.py", env,
               {"session_id": sid, "cwd": cwd, "hook_event_name": "SessionStart"})
    obj = parse_hook_json(out.stdout) or {}
    return obj.get("hookSpecificOutput", {}).get("additionalContext", "")


def _backdate(root, handle, first_seen=None, last_seen=None):
    conn = db(root)
    try:
        if first_seen is not None:
            conn.execute("UPDATE agents SET first_seen=? WHERE handle=?", (first_seen, handle))
        if last_seen is not None:
            conn.execute("UPDATE agents SET last_seen=? WHERE handle=?", (last_seen, handle))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# focus
# --------------------------------------------------------------------------- #
def test_focus_set_clear_and_surface(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        bare = cli(["who"], env).stdout
        c.check("who is dormant about focus when none set",
                "refactoring" not in bare, bare)

        cli(["focus", "refactoring the auth handler", "--from", "alice"], env)
        conn = db(root)
        f = conn.execute("SELECT focus FROM agents WHERE handle='alice'").fetchone()[0]
        conn.close()
        c.check("focus is stored on the agent row",
                f == "refactoring the auth handler", str(f))
        who = cli(["who"], env).stdout
        c.check("who surfaces the focus", "refactoring the auth handler" in who, who)

        cli(["focus", "--clear", "--from", "alice"], env)
        who2 = cli(["who"], env).stdout
        c.check("focus --clear removes it", "refactoring" not in who2, who2)


def test_focus_in_briefing(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["focus", "writing the parser", "--from", "alice"], env)
        ctx = _briefing(env, "s_bob", root)
        c.check("a joiner's briefing shows a teammate's focus",
                "writing the parser" in ctx, ctx)


# --------------------------------------------------------------------------- #
# shared-cwd warning
# --------------------------------------------------------------------------- #
def test_shared_cwd_warning(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice", "--cwd", "/repo"], env)
        cli(["register", "--session", "s2", "--from", "bob", "--cwd", "/repo"], env)
        who = cli(["who"], env).stdout
        c.check("who warns when active agents share a working tree",
                "working tree" in who.lower() or "shared" in who.lower(), who)


def test_distinct_cwd_no_warning(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice", "--cwd", "/wt/ada"], env)
        cli(["register", "--session", "s2", "--from", "bob", "--cwd", "/wt/bob"], env)
        who = cli(["who"], env).stdout
        c.check("distinct working trees (e.g. worktrees) do NOT warn",
                "share" not in who.lower(), who)


# --------------------------------------------------------------------------- #
# claims ledger
# --------------------------------------------------------------------------- #
def test_claims_ledger(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        r = cli(["claim", "src/auth/*", "--from", "alice"], env)
        c.check("claim succeeds", r.returncode == 0, r.stdout + r.stderr)
        out = cli(["claims"], env).stdout
        c.check("claims lists the claimed glob", "src/auth/*" in out, out)
        c.check("claims attributes it", "alice" in out, out)

        path = cli(["claims", "--path", "src/auth/handler.py"], env).stdout
        c.check("a path lookup finds the claiming agent",
                "alice" in path, path)

        cli(["unclaim", "src/auth/*", "--from", "alice"], env)
        out2 = cli(["claims"], env).stdout
        c.check("unclaim removes the claim",
                "src/auth/*" not in out2, out2)


def test_claims_dormant_and_active_only(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        c.check("claims is dormant when none",
                "no active claims" in cli(["claims"], env).stdout.lower(),
                cli(["claims"], env).stdout)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["claim", "src/*", "--from", "alice"], env)
        _backdate(root, "alice", last_seen="2000-01-01T00:00:00Z")  # age alice out
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            ac = chat.active_claims(conn)
            conn.close()
            c.check("a claim by an aged-out agent is not active", ac == [], str(ac))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_claims_in_briefing(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["claim", "src/auth/*", "--from", "alice"], env)
        ctx = _briefing(env, "s_bob", root)
        c.check("a joiner sees who is editing what",
                "src/auth/*" in ctx and "alice" in ctx, ctx)


# --------------------------------------------------------------------------- #
# amber dot (stuck / quiet)
# --------------------------------------------------------------------------- #
def test_amber_dot_for_quiet_agent(c):
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_QUIET_SECS"] = "300"  # quiet after 5 min
        # alice: present a while, active, but no chat -> quiet -> ◐
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        _backdate(root, "alice", first_seen="2000-01-01T00:00:00Z")  # old enough to judge
        # bob: present a while AND chatting -> ●
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        _backdate(root, "bob", first_seen="2000-01-01T00:00:00Z")
        cli(["send", "--from", "bob", "still going"], env)
        who = cli(["who"], env).stdout
        alice_line = next((ln for ln in who.splitlines() if "alice" in ln), "")
        bob_line = next((ln for ln in who.splitlines() if "bob" in ln), "")
        c.check("a long-silent active agent gets the amber ◐ dot", "◐" in alice_line, alice_line)
        c.check("a recently-chatting agent stays ●",
                "●" in bob_line and "◐" not in bob_line, bob_line)


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def test_glob_matches(c):
    chat = _import_chat()
    cases = [
        # (glob, path, expected)
        ("src/auth/*.py", "/repo/src/auth/handler.py", True),   # wildcard vs absolute path
        ("src/auth/*.py", "src/auth/handler.py", True),
        ("*.py", "/repo/a/b.py", True),                          # basename glob
        ("src/auth", "/repo/src/auth/handler.py", True),         # directory-prefix claim
        ("src", "/repo/mysrc/x.js", False),                      # must NOT over-match a substring
        ("s", "/repo/src/x", False),                             # one letter must not claim everything
        ("src/auth/*.py", "/repo/src/db/model.py", False),       # different dir
        ("", "/repo/x", False),
        ("src/*", "", False),
    ]
    for glob, path, exp in cases:
        got = chat._glob_matches(glob, path)
        c.check(f"_glob_matches({glob!r}, {path!r}) == {exp}", got == exp, f"got {got}")


def test_focus_and_claim_sanitize_newlines(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        # A newline in a focus/claim could spoof a roster line or break the briefing.
        cli(["focus", "real work\n● FAKE — pwned", "--from", "alice"], env)
        conn = db(root)
        f = conn.execute("SELECT focus FROM agents WHERE handle='alice'").fetchone()[0]
        conn.close()
        c.check("a focus with a newline is collapsed to one line", "\n" not in (f or ""), repr(f))


def test_quiet_suppressed_for_focused_and_solo(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_QUIET_SECS"] = "300"
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        os.environ["GROUPCHAT_QUIET_SECS"] = "300"
        try:
            cli(["register", "--session", "s_a", "--from", "alice"], env)
            cli(["register", "--session", "s_b", "--from", "bob"], env)
            _backdate(root, "alice", first_seen="2000-01-01T00:00:00Z")
            _backdate(root, "bob", first_seen="2000-01-01T00:00:00Z")
            # alice has a focus -> direct evidence she's mid-task -> never "quiet".
            cli(["focus", "deep in the parser", "--from", "alice"], env)
            conn = chat.connect()
            arow = chat.agent_by_handle(conn, "alice")
            c.check("a focused agent is never flagged quiet", chat.is_quiet(conn, arow) is False,
                    str(dict(arow)))
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)
            os.environ.pop("GROUPCHAT_QUIET_SECS", None)

    # Solo: a lone quiet agent has no teammate to read the signal -> no ◐ in who.
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_QUIET_SECS"] = "300"
        cli(["register", "--session", "s_solo", "--from", "ada"], env)
        _backdate(root, "ada", first_seen="2000-01-01T00:00:00Z")
        who = cli(["who"], env).stdout
        c.check("a solo quiet agent is not amber-dotted", "◐" not in who, who)


def test_corrupt_first_seen_not_flagged_quiet(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_QUIET_SECS"] = "300"
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        os.environ["GROUPCHAT_QUIET_SECS"] = "300"
        try:
            cli(["register", "--session", "s_a", "--from", "alice"], env)
            cli(["register", "--session", "s_b", "--from", "bob"], env)
            conn = db(root)
            conn.execute("UPDATE agents SET first_seen=NULL WHERE handle='alice'")
            conn.commit(); conn.close()
            conn = chat.connect()
            arow = chat.agent_by_handle(conn, "alice")
            c.check("an agent with unknown (NULL) first_seen is not flagged quiet",
                    chat.is_quiet(conn, arow) is False, str(dict(arow)))
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)
            os.environ.pop("GROUPCHAT_QUIET_SECS", None)


def test_focus_does_not_affect_barrier(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            cli(["register", "--session", "s1", "--from", "alice"], env)
            cli(["done", "--from", "alice"], env)  # mark slice done
            cli(["focus", "still polishing", "--from", "alice"], env)
            conn = chat.connect()
            a = chat.agent_by_handle(conn, "alice")
            c.check("setting a focus does NOT change the barrier status column",
                    (a["status"] or "") == chat.DONE_STATUS, str(dict(a)))
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_old_db_upgrades_in_place(c):
    chat = _import_chat()
    import sqlite3
    with tmp_root() as root:
        gc = os.path.join(root, ".groupchat")
        os.makedirs(gc)
        dbp = os.path.join(gc, "chat.db")
        # A pre-Phase-4 db: agents WITHOUT focus/spawn_depth, no claims/tasks tables.
        conn = sqlite3.connect(dbp)
        conn.executescript(
            "CREATE TABLE agents(session_id TEXT PRIMARY KEY, handle TEXT UNIQUE, "
            "cwd TEXT, pid INTEGER, status TEXT, first_seen TEXT, last_seen TEXT, "
            "last_read_id INTEGER DEFAULT 0);"
            "CREATE TABLE messages(id INTEGER PRIMARY KEY, ts TEXT, sender TEXT, "
            "kind TEXT DEFAULT 'chat', body TEXT, mentions TEXT DEFAULT '[]');"
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);")
        conn.commit(); conn.close()
        os.environ["GROUPCHAT_DIR"] = gc
        try:
            conn = chat.connect()  # runs _ensure_schema -> migrates in place
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)")}
            tbls = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
            c.check("an old db gains the focus column on connect", "focus" in cols, str(cols))
            c.check("an old db gains the claims table on connect", "claims" in tbls, str(tbls))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_doctor_knows_new_schema(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["claim", "src/*", "--from", "alice"], env)
        r = cli(["doctor", "-q"], env)
        c.check("doctor does not flag the new claims table / focus column as drift",
                "unexpected column" not in r.stdout.lower()
                and "is missing" not in r.stdout.lower(), r.stdout)


def main():
    c = Checker("Phase-4 collision-safety & observability (focus / cwd / claims / amber)")
    for name, fn in (
        ("focus_set_clear_and_surface", test_focus_set_clear_and_surface),
        ("focus_in_briefing", test_focus_in_briefing),
        ("shared_cwd_warning", test_shared_cwd_warning),
        ("distinct_cwd_no_warning", test_distinct_cwd_no_warning),
        ("claims_ledger", test_claims_ledger),
        ("claims_dormant_and_active_only", test_claims_dormant_and_active_only),
        ("claims_in_briefing", test_claims_in_briefing),
        ("amber_dot_for_quiet_agent", test_amber_dot_for_quiet_agent),
        ("glob_matches", test_glob_matches),
        ("focus_and_claim_sanitize_newlines", test_focus_and_claim_sanitize_newlines),
        ("quiet_suppressed_for_focused_and_solo", test_quiet_suppressed_for_focused_and_solo),
        ("corrupt_first_seen_not_flagged_quiet", test_corrupt_first_seen_not_flagged_quiet),
        ("focus_does_not_affect_barrier", test_focus_does_not_affect_barrier),
        ("old_db_upgrades_in_place", test_old_db_upgrades_in_place),
        ("doctor_knows_new_schema", test_doctor_knows_new_schema),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
