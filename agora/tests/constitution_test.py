#!/usr/bin/env python3
"""Phase 1 tests for the constitution / governance layer.

Dependency-free, matching the repo's "no framework" convention: drives
``chat.py`` (and the SessionStart hook) via subprocess in an isolated
``GROUPCHAT_DIR`` so the live room is never touched. Run:

    python3 tests/constitution_test.py     # exit 0 = all pass

The constitution file resolves at ``repo_root()`` == ``dirname(store_dir())``;
with ``GROUPCHAT_DIR=<root>/.groupchat`` that is ``<root>``, mirroring production
where the room is ``<repo>/.groupchat`` and the law is ``<repo>/CONSTITUTION.md``.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHAT = os.path.join(ROOT, ".groupchat", "chat.py")
HOOK = os.path.join(ROOT, ".groupchat", "hooks", "session_start.py")

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


def run(args, env, stdin=None):
    return subprocess.run([sys.executable, CHAT, *args],
                          capture_output=True, text=True, env=env, input=stdin)


def run_hook(env, payload):
    return subprocess.run([sys.executable, HOOK],
                          capture_output=True, text=True, env=env, input=payload)


def test_init(root):
    env = env_for(root)
    const = os.path.join(root, "CONSTITUTION.md")
    r = run(["constitution", "init"], env)
    check("init exits 0", r.returncode == 0, r.stderr)
    check("init creates CONSTITUTION.md at repo root", os.path.isfile(const),
          f"expected {const}")
    text = open(const).read() if os.path.isfile(const) else ""
    check("file has CORE zone markers",
          "CONSTITUTION:CORE:BEGIN" in text and "CONSTITUTION:CORE:END" in text)
    check("file has ARTICLES zone markers",
          "CONSTITUTION:ARTICLES:BEGIN" in text and "CONSTITUTION:ARTICLES:END" in text)
    check("core entrenches human authority (C1)", "C1" in text and "human" in text.lower())
    check("seeds at least one Article with a provenance comment",
          "R1" in text and "id=R1" in text)
    # refuses to overwrite
    r2 = run(["constitution", "init"], env)
    check("second init refuses (nonzero exit)", r2.returncode != 0)
    check("second init leaves file unchanged", open(const).read() == text)


def test_show(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    r = run(["constitution", "show"], env)
    check("show exits 0", r.returncode == 0, r.stderr)
    check("show lists a Core item", "C1" in r.stdout)
    check("show lists an Article id", "R1" in r.stdout)
    # bare `constitution` defaults to show
    rb = run(["constitution"], env)
    check("bare `constitution` defaults to show", rb.returncode == 0 and "R1" in rb.stdout)


def test_check_valid(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    r = run(["constitution", "check"], env)
    check("check passes (exit 0) on a freshly-init'd file", r.returncode == 0, r.stderr)


def test_check_corrupt(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    const = os.path.join(root, "CONSTITUTION.md")
    text = open(const).read()
    open(const, "w").write(text.replace("<!-- CONSTITUTION:ARTICLES:END -->", ""))
    r = run(["constitution", "check"], env)
    check("check fails (nonzero) when a zone marker is missing", r.returncode != 0)


def test_check_duplicate_id(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    const = os.path.join(root, "CONSTITUTION.md")
    text = open(const).read()
    # Duplicate R1 by appending a second R1 Article inside the Articles zone.
    dup = ("### R1 — duplicate\nbogus\n<!-- meta: id=R1 added=2026-01-01 by=x "
           "ratified=2026-01-01 amended= source= -->\n"
           "<!-- CONSTITUTION:ARTICLES:END -->")
    open(const, "w").write(text.replace("<!-- CONSTITUTION:ARTICLES:END -->", dup))
    r = run(["constitution", "check"], env)
    check("check fails (nonzero) on a reused rule id", r.returncode != 0)


def test_check_missing_file(root):
    env = env_for(root)  # no init -> no file
    r = run(["constitution", "check"], env)
    check("check on a repo with no constitution is non-fatal-ish (reports clearly)",
          "no constitution" in (r.stdout + r.stderr).lower()
          or "not found" in (r.stdout + r.stderr).lower())


def test_repo_root_anchor(root):
    """repo_root() must equal dirname(store_dir()) so the law sits beside .groupchat."""
    env = env_for(root)
    run(["constitution", "init"], env)
    # CONSTITUTION.md must be a sibling of the .groupchat room dir, not inside it.
    inside = os.path.join(root, ".groupchat", "CONSTITUTION.md")
    sibling = os.path.join(root, "CONSTITUTION.md")
    check("law sits beside .groupchat (repo_root anchor), not inside it",
          os.path.isfile(sibling) and not os.path.isfile(inside))


def test_sessionstart_pointer(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    payload = '{"session_id":"const-s1","cwd":"/x","hook_event_name":"SessionStart"}'
    r = run_hook(env, payload)
    check("SessionStart hook exits 0 with a constitution present", r.returncode == 0)
    check("SessionStart briefing points at the constitution",
          "constitution" in r.stdout.lower())


def test_sessionstart_failopen_no_constitution(root):
    env = env_for(root)  # no init
    payload = '{"session_id":"const-s2","cwd":"/x","hook_event_name":"SessionStart"}'
    r = run_hook(env, payload)
    check("SessionStart hook fails open (exit 0) when no constitution exists",
          r.returncode == 0)


def test_sessionstart_failopen_corrupt(root):
    env = env_for(root)
    run(["constitution", "init"], env)
    const = os.path.join(root, "CONSTITUTION.md")
    open(const, "w").write("totally broken, no markers at all")
    payload = '{"session_id":"const-s3","cwd":"/x","hook_event_name":"SessionStart"}'
    r = run_hook(env, payload)
    check("SessionStart hook fails open (exit 0) on a corrupt constitution",
          r.returncode == 0)


def main():
    tests = [
        test_init, test_show, test_check_valid, test_check_corrupt,
        test_check_duplicate_id, test_check_missing_file, test_repo_root_anchor,
        test_sessionstart_pointer, test_sessionstart_failopen_no_constitution,
        test_sessionstart_failopen_corrupt,
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
