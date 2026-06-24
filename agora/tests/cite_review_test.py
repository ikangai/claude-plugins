#!/usr/bin/env python3
"""Phase 2 tests: rule-citation harvesting + the repeal-first review.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/cite_review_test.py     # exit 0 = all pass
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GC = os.path.join(ROOT, ".groupchat")
CHAT = os.path.join(GC, "chat.py")
if GC not in sys.path:
    sys.path.insert(0, GC)

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


def test_parse_rules():
    import chat
    cases = {
        "per R2 I'll converge": ["R2"],
        "R2D2 is a droid": [],
        "curve R2=0.99 fit": [],          # R-squared chatter
        "the value r2 lower": [],          # case-sensitive: lowercase r is not a cite
        "see [[R2]] and R10!": ["R10", "R2"],
        "path/to/R2/x": [],                # path boundary (lookbehind)
        "R1 and R1 again": ["R1"],         # de-duped within a body
    }
    for body, exp in cases.items():
        got = chat.parse_rules(body)
        check(f"parse_rules({body!r}) == {exp}", got == exp, f"got {got}")


def test_cite_harvest_gating(root):
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    os.environ.pop("CLAUDE_PROJECT_DIR", None)
    import chat
    conn = chat.connect()
    chat.send(conn, "ada", "per R2 I'll converge", kind="chat")          # counts
    chat.send(conn, "ada", "motion to repeal R2 — per R2", kind="motion")  # excluded (kind)
    chat.send(conn, "ada", "quoting <!-- CONSTITUTION:ARTICLES:BEGIN --> with R2", kind="chat")  # excluded (quote)
    n = conn.execute("SELECT COUNT(*) FROM rule_cites WHERE rule_id='R2'").fetchone()[0]
    check("only the plain chat cite is harvested (kind + quote gating)", n == 1, f"got {n}")


def test_review_repeal_and_unknown(root):
    env = env_for(root)
    run(["constitution", "init"], env)              # R1, R2 are live
    for who in ("ada", "turing", "hopper"):
        run(["send", "--from", who, "per R2 I'll converge"], env)
    run(["send", "--from", "ada", "per R99 ghost rule"], env)  # R99 not a live article
    r = run(["review"], env)
    check("review exits 0", r.returncode == 0, r.stderr)
    lines = r.stdout.splitlines()
    check("R1 (never cited) is a repeal candidate with 0 cites",
          any("R1" in l and "0 cites" in l for l in lines), r.stdout)
    check("R2 (3 distinct agents) is kept with 3 cites",
          any("R2" in l and "3 cites" in l for l in lines), r.stdout)
    check("R99 reported as an unknown/repealed cite id",
          any("R99" in l for l in lines), r.stdout)


def test_review_distinct_senders(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    for _ in range(4):
        run(["send", "--from", "ada", "per R2"], env)  # same agent 4x
    r = run(["review"], env)
    check("4 cites from one agent count as 1 distinct sender",
          any("R2" in l and "1 cites" in l for l in r.stdout.splitlines()), r.stdout)


def test_review_no_constitution(root):
    env = env_for(root)
    r = run(["review"], env)
    check("review on a repo with no constitution is non-fatal (exit 0)",
          r.returncode == 0)


def main():
    print("\n# test_parse_rules")
    test_parse_rules()
    for t in (test_cite_harvest_gating, test_review_repeal_and_unknown,
              test_review_distinct_senders, test_review_no_constitution):
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
