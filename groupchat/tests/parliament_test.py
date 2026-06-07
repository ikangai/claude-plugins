#!/usr/bin/env python3
"""Phase 3 tests: the ADVISORY parliament (motion / vote / amendments / ratify).

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/parliament_test.py     # exit 0 = all pass

Key invariants under test: votes require a registered --session (bare --from is
unauthenticated and rejected); the tally is advisory (no green "passes"); ratify
is diff-only (never writes the file) and is TOCTOU-guarded.
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


def env_for(root):
    env = dict(os.environ)
    env["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env


def run(args, env):
    return subprocess.run([sys.executable, CHAT, *args],
                          capture_output=True, text=True, env=env)


def db(root):
    return sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))


def first_motion_id(root):
    return db(root).execute("SELECT MIN(id) FROM motions").fetchone()[0]


def test_motion_basic(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    r = run(["motion", "--from", "ada", "--rule", "R2",
             "--change", "Converge harder", "--because", "see #1,#2"], env)
    check("motion exits 0", r.returncode == 0, r.stderr)
    check("motion prints an M<id>", "M" in r.stdout, r.stdout)
    row = db(root).execute(
        "SELECT op,target,status,base_text,because FROM motions").fetchone()
    check("stored as open amend on R2 with base_text + evidence",
          bool(row) and row[0] == "amend" and row[1] == "R2" and row[2] == "open"
          and row[3] and row[4], str(row))


def test_motion_core_rejected(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    r = run(["motion", "--from", "ada", "--rule", "C1",
             "--change", "x", "--because", "y"], env)
    check("motion targeting entrenched Core C1 is rejected", r.returncode != 0,
          r.stdout + r.stderr)


def test_motion_requires_evidence(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    r = run(["motion", "--from", "ada", "--rule", "R2", "--change", "x"], env)
    check("motion without --because is rejected", r.returncode != 0)


def test_motion_supersede(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "v1", "--because", "a"], env)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "v2", "--because", "b"], env)
    rows = db(root).execute(
        "SELECT status FROM motions WHERE target='R2' ORDER BY id").fetchall()
    check("a newer motion supersedes the older open one on the same rule",
          len(rows) == 2 and rows[0][0] == "superseded" and rows[1][0] == "open", str(rows))


def test_motion_add_allocates_new_id(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    r = run(["motion", "--from", "ada", "--rule", "new",
             "--change", "Be kind in reviews", "--because", "x"], env)
    check("add motion exits 0", r.returncode == 0, r.stderr)
    row = db(root).execute("SELECT op,new_id FROM motions").fetchone()
    check("add allocates a fresh, non-reused R-id",
          bool(row) and row[0] == "add" and row[1] not in (None, "R1", "R2"), str(row))


def test_vote_requires_registered_session(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "x", "--because", "y"], env)
    mid = first_motion_id(root)
    r = run(["vote", "--from", "turing", f"M{mid}", "yea"], env)  # bare handle, no session
    check("vote via bare --from (unregistered) is rejected", r.returncode != 0,
          r.stdout + r.stderr)


def test_vote_recorded_last_wins(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "x", "--because", "y"], env)
    mid = first_motion_id(root)
    run(["register", "--session", "vs1"], env)
    run(["vote", "--session", "vs1", f"M{mid}", "yea"], env)
    r = run(["vote", "--session", "vs1", f"M{mid}", "nay"], env)  # changed vote
    check("second vote recorded (exit 0)", r.returncode == 0, r.stderr)
    a = run(["amendments", "--all"], env)
    check("last vote per session wins (yea 0 / nay 1, one voter)",
          "yea 0" in a.stdout and "nay 1" in a.stdout, a.stdout)


def test_amendments_advisory(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "x", "--because", "y"], env)
    r = run(["amendments"], env)
    check("amendments exits 0", r.returncode == 0, r.stderr)
    check("framing is advisory, not a green gate",
          "advisory" in r.stdout.lower() and "passed" not in r.stdout.lower(), r.stdout)


def test_ratify_dossier_diff_no_write(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    const = os.path.join(root, "CONSTITUTION.md")
    before = open(const).read()
    run(["motion", "--from", "ada", "--rule", "R2",
         "--change", "CONVERGE_NOW_V2", "--because", "#1"], env)
    mid = first_motion_id(root)
    r = run(["ratify", f"M{mid}"], env)
    check("ratify exits 0", r.returncode == 0, r.stderr)
    check("dossier surfaces evidence + advisory votes (no green 'passes')",
          "evidence" in r.stdout.lower() and "advisory" in r.stdout.lower(),
          r.stdout[:400])
    check("ratify prints a diff containing the proposed change",
          "CONVERGE_NOW_V2" in r.stdout and ("@@" in r.stdout or "+++" in r.stdout),
          r.stdout[-400:])
    check("ratify is diff-only — the file is NOT modified", open(const).read() == before)
    run(["ratify", "--confirm", f"M{mid}"], env)   # the human confirms after committing
    log = run(["log", "--limit", "10"], env)
    check("ratify --confirm announces the change on the bus (rides the cursor)",
          "ratif" in log.stdout.lower() or "constitution" in log.stdout.lower(), log.stdout)


def test_ratify_toctou(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "v2", "--because", "#1"], env)
    mid = first_motion_id(root)
    const = os.path.join(root, "CONSTITUTION.md")
    text = open(const).read().replace("Converge, don't fork", "Converge, do not fork")
    open(const, "w").write(text)  # the target Article changed since the motion opened
    r = run(["ratify", f"M{mid}"], env)
    check("ratify refuses on a base-text mismatch (TOCTOU guard)",
          r.returncode != 0, r.stdout + r.stderr)


def main():
    tests = [
        test_motion_basic, test_motion_core_rejected, test_motion_requires_evidence,
        test_motion_supersede, test_motion_add_allocates_new_id,
        test_vote_requires_registered_session, test_vote_recorded_last_wins,
        test_amendments_advisory, test_ratify_dossier_diff_no_write, test_ratify_toctou,
    ]
    for t in tests:
        print(f"\n# {t.__name__}")
        with tempfile.TemporaryDirectory() as root:
            try:
                t(root)
            except Exception as e:
                check(t.__name__ + " (no exception)", False, repr(e))
    print(f"\n{'='*50}")
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
