#!/usr/bin/env python3
"""Regression tests for the post-review fixes to the constitution layer.

Covers: --change injection (heading/marker), in-body marker robustness, repeal
tombstones + no-id-reuse, motion @mentions not stored, ratify read-only/--confirm
split, ratify 'add' id-taken guard, env-var tolerance, narrowed cite guard, and
parse_rules R0/leading-zero/-squared. Run: python3 tests/constitution_fixes_test.py
"""
import os
import sqlite3
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


def check(n, c, d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + ("" if c else f"  -- {d}"))
    if not c:
        _failures.append(n)


def env_for(root):
    e = dict(os.environ)
    e["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    e.pop("CLAUDE_PROJECT_DIR", None)
    return e


def run(a, e):
    return subprocess.run([sys.executable, CHAT, *a], capture_output=True, text=True, env=e)


def db(root):
    return sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))


def first_mid(root):
    return db(root).execute("SELECT MIN(id) FROM motions").fetchone()[0]


def test_change_injection_rejected(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    r = run(["motion", "--from", "ada", "--rule", "R2",
             "--change", "ok line\n### R9 — sneaky\nbad", "--because", "x"], e)
    check("motion --change containing a '### ' heading is rejected", r.returncode != 0,
          r.stdout + r.stderr)
    r2 = run(["motion", "--from", "ada", "--rule", "R2",
              "--change", "has <!-- CONSTITUTION:ARTICLES:END --> in it", "--because", "x"], e)
    check("motion --change containing a zone marker is rejected", r2.returncode != 0,
          r2.stdout + r2.stderr)


def test_inbody_marker_robust(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    const = os.path.join(root, "CONSTITUTION.md")
    t = open(const).read().replace(
        "Post \"starting on <path>\" before editing, so two agents don't collide.",
        "Post before editing. (The file ends with a <!-- CONSTITUTION:ARTICLES:END --> line.)")
    open(const, "w").write(t)
    r = run(["constitution", "check"], e)
    check("an in-body marker with surrounding text doesn't corrupt parse (R2 still seen)",
          r.returncode == 0 and "2 article" in r.stdout, r.stdout + r.stderr)


def test_no_id_reuse_from_committed_tombstone(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    const = os.path.join(root, "CONSTITUTION.md")
    t = open(const).read().replace(
        "<!-- meta: id=R2 added=2026-06-07 by=human ratified=2026-06-07 amended= source= -->",
        "<!-- meta: id=R2 added=2026-06-07 by=human ratified=2026-06-07 amended= "
        "source= repealed=2026-06-07 -->")
    open(const, "w").write(t)
    dbp = os.path.join(root, ".groupchat", "chat.db")        # wipe runtime high-water mark
    if os.path.exists(dbp):
        os.remove(dbp)
    run(["motion", "--from", "ada", "--rule", "new", "--change", "Be excellent", "--because", "x"], e)
    nid = db(root).execute("SELECT new_id FROM motions").fetchone()[0]
    check("a fresh db still won't reuse a committed-tombstone id (R2)",
          nid not in (None, "R1", "R2"), str(nid))


def test_repeal_produces_tombstone(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    run(["motion", "--from", "ada", "--repeal", "R2", "--because", "dead letter #5"], e)
    r = run(["ratify", f"M{first_mid(root)}"], e)
    check("repeal diff tombstones the rule (keeps R2 + a repealed= mark), not a blind delete",
          "repealed=" in r.stdout and "R2" in r.stdout, r.stdout[-600:])


def test_motion_mention_not_stored(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "ping @turing", "--because", "x"], e)
    row = db(root).execute("SELECT mentions FROM messages WHERE kind='motion'").fetchone()
    check("a motion body's @handle is NOT stored as a mention (won't block Stop)",
          bool(row) and (row[0] in ("[]", None)), str(row))


def test_ratify_readonly_then_confirm(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "NEWTEXT_V9", "--because", "x"], e)
    mid = first_mid(root)
    r1 = run(["ratify", f"M{mid}"], e)
    st = db(root).execute("SELECT status FROM motions WHERE id=?", (mid,)).fetchone()[0]
    check("default ratify is read-only — status stays open", st == "open", st)
    check("default ratify does NOT announce on the bus",
          "ratified" not in run(["log", "--limit", "20"], e).stdout.lower())
    r2 = run(["ratify", f"M{mid}"], e)
    check("default ratify is repeatable (re-shows the diff)",
          r2.returncode == 0 and "NEWTEXT_V9" in r2.stdout)
    run(["ratify", "--confirm", f"M{mid}"], e)
    st2 = db(root).execute("SELECT status FROM motions WHERE id=?", (mid,)).fetchone()[0]
    check("ratify --confirm marks the motion ratified", st2 == "ratified", st2)
    check("ratify --confirm announces on the bus",
          "ratified" in run(["log", "--limit", "20"], e).stdout.lower())


def test_ratify_add_id_taken_refused(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    run(["motion", "--from", "ada", "--rule", "new", "--change", "Rule three", "--because", "x"], e)
    mid = first_mid(root)
    const = os.path.join(root, "CONSTITUTION.md")
    nid = db(root).execute("SELECT new_id FROM motions WHERE id=?", (mid,)).fetchone()[0]
    t = open(const).read().replace(
        "<!-- CONSTITUTION:ARTICLES:END -->",
        f"### {nid} — preexisting\nx\n<!-- meta: id={nid} added=2026-06-07 by=human "
        "ratified=2026-06-07 amended= source= -->\n<!-- CONSTITUTION:ARTICLES:END -->")
    open(const, "w").write(t)
    r = run(["ratify", "--confirm", f"M{mid}"], e)
    check("ratify of an 'add' whose id is now taken is refused (TOCTOU)",
          r.returncode != 0, r.stdout + r.stderr)


def test_env_tolerant(root):
    e = env_for(root)
    e["GROUPCHAT_REVIEW_LOW"] = "abc"
    e["GROUPCHAT_AMEND_QUORUM"] = "lots"
    e["GROUPCHAT_AMEND_SUPERMAJORITY"] = "nope"
    run(["constitution", "init"], e)
    check("review tolerates a non-numeric GROUPCHAT_REVIEW_LOW (no crash)",
          run(["review"], e).returncode == 0)
    run(["motion", "--from", "ada", "--rule", "R2", "--change", "x", "--because", "y"], e)
    check("amendments tolerates non-numeric AMEND_QUORUM/SUPERMAJORITY (no crash)",
          run(["amendments"], e).returncode == 0)


def test_cite_guard_narrowed(root):
    e = env_for(root)
    run(["constitution", "init"], e)
    run(["send", "--from", "ada", "update the CONSTITUTION: it is stale, per R2"], e)
    n = db(root).execute("SELECT COUNT(*) FROM rule_cites WHERE rule_id='R2'").fetchone()[0]
    check("a chat msg with 'CONSTITUTION:' prose still harvests its R2 cite (guard narrowed)",
          n == 1, f"got {n}")


def test_amendment_roundtrips_clean(root):
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    os.environ.pop("CLAUDE_PROJECT_DIR", None)
    import chat
    today = "2026-06-07"
    text = chat._starter_constitution(today)
    m = {"op": "repeal", "target": "R1", "id": 7, "change": None,
         "new_id": None, "proposer": "ada"}
    new = chat._apply_amendment(text, m, today)
    parsed = chat.parse_constitution(new)
    check("amended file still parses ok", parsed["ok"], str(parsed["errors"]))
    r1 = next((a for a in parsed["articles"] if a["id"] == "R1"), None)
    check("tombstone provenance is clean (empty values don't swallow the next key)",
          bool(r1) and r1["prov"].get("source") == "M7"
          and r1["prov"].get("amended") in ("", None)
          and r1["prov"].get("repealed") == today, r1["prov"] if r1 else None)
    live = [a["id"] for a in parsed["live"]]
    check("repealed R1 is not live; R2 still live", "R1" not in live and "R2" in live, live)


def test_parse_rules_fixes():
    import chat
    check("parse_rules rejects R0", chat.parse_rules("see R0") == [])
    check("parse_rules rejects leading-zero R007", chat.parse_rules("R007 here") == [])
    check("parse_rules rejects 'R2-squared'", chat.parse_rules("the R2-squared term") == [])
    check("parse_rules rejects 'R2 squared'", chat.parse_rules("R2 squared") == [])
    check("parse_rules still harvests a plain R2", chat.parse_rules("per R2 ok") == ["R2"])


def main():
    print("\n# test_parse_rules_fixes")
    test_parse_rules_fixes()
    tests = [
        test_change_injection_rejected, test_inbody_marker_robust,
        test_no_id_reuse_from_committed_tombstone, test_repeal_produces_tombstone,
        test_motion_mention_not_stored, test_ratify_readonly_then_confirm,
        test_ratify_add_id_taken_refused, test_env_tolerant, test_cite_guard_narrowed,
        test_amendment_roundtrips_clean,
    ]
    for t in tests:
        print(f"\n# {t.__name__}")
        with tempfile.TemporaryDirectory() as root:
            try:
                t(root)
            except Exception as ex:
                check(t.__name__ + " (no exception)", False, repr(ex))
    print(f"\n{'='*50}")
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
