#!/usr/bin/env python3
"""Tests for .groupchat/doctor.py — the health/staleness checker.

Validates the bug-class catcher (shadowed top-level defs) on crafted inputs, and
that a full run against the (now-fixed) code + a fresh room is clean. Run:

    python3 tests/doctor_test.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, cli, env_for, tmp_root  # noqa: E402

sys.path.insert(0, GROUPCHAT)
import doctor  # noqa: E402

DOCTOR = os.path.join(GROUPCHAT, "doctor.py")


def test_shadowed_defs_detection(c):
    with tmp_root() as root:
        dup = os.path.join(root, "dup.py")
        with open(dup, "w") as fh:
            fh.write("def foo():\n    return 1\n\n\ndef foo():\n    return 2\n")
        c.check("detects a duplicated top-level def",
                doctor._shadowed_defs(dup) == ["foo"], doctor._shadowed_defs(dup))

        clean = os.path.join(root, "clean.py")
        with open(clean, "w") as fh:
            fh.write("def a():\n    pass\n\n\ndef b():\n    pass\n")
        c.check("clean file has no shadowed defs",
                doctor._shadowed_defs(clean) == [], doctor._shadowed_defs(clean))

        broken = os.path.join(root, "broken.py")
        with open(broken, "w") as fh:
            fh.write("def x(:\n  pass\n")
        out = doctor._shadowed_defs(broken)
        c.check("flags a syntax error", out and out[0].startswith("__syntax__"), out)


def test_full_run_is_clean(c):
    """A full doctor run against the (fixed) code + a fresh inited room must have
    zero FAILURES (a missing settings.json is only a warning)."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        r = subprocess.run([sys.executable, DOCTOR, "-q"],
                           capture_output=True, text=True, env=env, timeout=40)
        c.check("doctor exits 0 (no failures) on healthy code+room",
                r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}")
        c.check("doctor reports zero failures",
                "0 failure(s)" in r.stdout or "healthy" in r.stdout, r.stdout[-300:])


def test_detects_a_planted_bug(c):
    """Point doctor's static scan at a chat.py copy with a re-introduced shadow:
    it must catch it (guards against the catcher silently regressing)."""
    with tmp_root() as root:
        # Re-create the #21 shape in a throwaway module and scan it directly.
        bad = os.path.join(root, "chatlike.py")
        with open(bad, "w") as fh:
            fh.write("import os\n"
                     "def _env_int(name):\n    return None\n\n\n"
                     "def _env_int(name, default):\n    return default\n")
        dups = doctor._shadowed_defs(bad)
        c.check("the catcher would have flagged the #21 bug",
                "_env_int" in dups, dups)


def test_cross_cli_drift_detection(c):
    """A .codex/hooks.json pointing at a missing script must FAIL doctor; one
    pointing at a real script must pass."""
    import json
    # Broken: command references a non-existent hook path.
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        codex_dir = os.path.join(root, ".codex")
        os.makedirs(codex_dir, exist_ok=True)
        with open(os.path.join(codex_dir, "hooks.json"), "w") as fh:
            json.dump({"hooks": {"Stop": [{"hooks": [
                {"type": "command",
                 "command": f'python3 "{root}/.groupchat/hooks/GONE.py"'}]}]}}, fh)
        r = subprocess.run([sys.executable, DOCTOR, "-q"],
                           capture_output=True, text=True, env=env, timeout=40)
        c.check("doctor FAILS on a codex hook path that doesn't exist",
                r.returncode != 0 and "missing script" in r.stdout, r.stdout[-300:])
    # Healthy: command references a real hook path.
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        codex_dir = os.path.join(root, ".codex")
        os.makedirs(codex_dir, exist_ok=True)
        real = os.path.join(GROUPCHAT, "hooks", "stop.py")
        with open(os.path.join(codex_dir, "hooks.json"), "w") as fh:
            json.dump({"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": f'python3 "{real}"'}]}]}}, fh)
        r = subprocess.run([sys.executable, DOCTOR, "-q"],
                           capture_output=True, text=True, env=env, timeout=40)
        c.check("doctor passes when codex hook paths resolve",
                r.returncode == 0, r.stdout[-300:])


def test_doctor_never_crashes_on_malformed_input(c):
    """A health checker must FAIL-CLEAN, never traceback. Feed it the three
    malformed inputs the integration audit caught: a corrupt room db, a
    settings.json that's valid JSON but not an object, and the same for
    .codex/hooks.json. doctor must report failures and exit non-zero — never
    crash with a traceback."""
    import json as _json

    # (a) corrupt / non-SQLite room db.
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        with open(os.path.join(root, ".groupchat", "chat.db"), "w") as fh:
            fh.write("this is not a sqlite database at all")
        r = subprocess.run([sys.executable, DOCTOR, "-q"],
                           capture_output=True, text=True, env=env, timeout=40)
        c.check("corrupt db -> reported, not a traceback",
                "Traceback" not in r.stderr and ("unreadable" in r.stdout
                or "not a SQLite" in r.stdout), r.stdout[-200:] + r.stderr[-200:])

    # (b) settings.json valid JSON but not an object (a list).
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
        with open(os.path.join(root, ".claude", "settings.json"), "w") as fh:
            _json.dump(["not", "an", "object"], fh)
        r = subprocess.run([sys.executable, DOCTOR, "-q"],
                           capture_output=True, text=True, env=env, timeout=40)
        c.check("non-object settings.json -> reported, not a traceback",
                "Traceback" not in r.stderr and "not an object" in r.stdout,
                r.stdout[-200:] + r.stderr[-200:])

    # (c) .codex/hooks.json valid JSON but not an object.
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        os.makedirs(os.path.join(root, ".codex"), exist_ok=True)
        with open(os.path.join(root, ".codex", "hooks.json"), "w") as fh:
            _json.dump("just a string", fh)
        r = subprocess.run([sys.executable, DOCTOR, "-q"],
                           capture_output=True, text=True, env=env, timeout=40)
        c.check("non-object .codex/hooks.json -> reported, not a traceback",
                "Traceback" not in r.stderr and "not an object" in r.stdout,
                r.stdout[-200:] + r.stderr[-200:])


def test_chat_py_doctor_subcommand(c):
    """doctor is wired as a first-class `chat.py doctor` subcommand, not just a
    hidden script."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        r = cli(["doctor", "-q"], env, timeout=40)
        c.check("`chat.py doctor` runs and exits 0 on a healthy room",
                r.returncode == 0, f"rc={r.returncode}\n{r.stdout[-200:]}\n{r.stderr[-200:]}")
        c.check("`chat.py doctor` produces the doctor report",
                "doctor" in r.stdout.lower(), r.stdout[-200:])


def main():
    c = Checker("doctor (health & staleness checker)")
    for fn in (test_shadowed_defs_detection, test_full_run_is_clean,
               test_detects_a_planted_bug, test_cross_cli_drift_detection,
               test_doctor_never_crashes_on_malformed_input,
               test_chat_py_doctor_subcommand):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
