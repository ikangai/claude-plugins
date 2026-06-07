#!/usr/bin/env python3
"""Install/onboarding tests — `chat.py install <repo>` is the user's first
contact with the system, and it idempotently merges their `.claude/settings.json`
(a destructive-looking operation), yet nothing tested it. Covered: a fresh
install lays down code + wires the three hooks; re-install is idempotent; an
existing settings file is merged (user's own hooks preserved); a corrupt
settings file is refused, not clobbered; the copied chat.py actually runs.

    python3 tests/install_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, env_for, tmp_root  # noqa: E402


def _settings(target):
    p = os.path.join(target, ".claude", "settings.json")
    return json.load(open(p)) if os.path.isfile(p) else None


def _hook_commands(settings, event):
    return [h.get("command", "")
            for g in (settings or {}).get("hooks", {}).get(event, [])
            for h in g.get("hooks", [])]


def test_fresh_install(c):
    with tmp_root() as root:
        target = os.path.join(root, "repo")
        os.makedirs(target)
        r = cli(["install", target], env_for(root))
        c.check("install exits 0", r.returncode == 0, r.stderr)
        c.check("copies chat.py",
                os.path.isfile(os.path.join(target, ".groupchat", "chat.py")))
        for h in ("session_start.py", "user_prompt_submit.py", "stop.py"):
            c.check(f"copies hook {h}",
                    os.path.isfile(os.path.join(target, ".groupchat", "hooks", h)))
        s = _settings(target)
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            cmds = _hook_commands(s, event)
            c.check(f"wires {event} hook",
                    any(".groupchat/hooks" in cmd for cmd in cmds), cmds)


def test_install_is_idempotent(c):
    with tmp_root() as root:
        target = os.path.join(root, "repo")
        os.makedirs(target)
        cli(["install", target], env_for(root))
        before = _settings(target)
        r = cli(["install", target], env_for(root))
        c.check("re-install exits 0", r.returncode == 0, r.stderr)
        c.check("re-install reports no new hooks", "no new" in r.stdout, r.stdout)
        c.check("re-install does not duplicate hook entries",
                _settings(target) == before)


def test_install_merges_existing_settings(c):
    with tmp_root() as root:
        target = os.path.join(root, "repo")
        os.makedirs(os.path.join(target, ".claude"))
        # The user already has an unrelated hook.
        with open(os.path.join(target, ".claude", "settings.json"), "w") as fh:
            json.dump({"hooks": {"PreToolUse": [
                {"hooks": [{"type": "command", "command": "echo hi"}]}]},
                "model": "opus"}, fh)
        r = cli(["install", target], env_for(root))
        c.check("merge install exits 0", r.returncode == 0, r.stderr)
        s = _settings(target)
        c.check("preserves the user's unrelated hook",
                "echo hi" in _hook_commands(s, "PreToolUse"), s)
        c.check("preserves unrelated settings keys", s.get("model") == "opus", s)
        c.check("adds our Stop hook alongside",
                any(".groupchat/hooks" in cmd for cmd in _hook_commands(s, "Stop")))


def test_install_refuses_corrupt_settings(c):
    with tmp_root() as root:
        target = os.path.join(root, "repo")
        os.makedirs(os.path.join(target, ".claude"))
        bad = os.path.join(target, ".claude", "settings.json")
        with open(bad, "w") as fh:
            fh.write("{ this is not valid json ")
        r = cli(["install", target], env_for(root))
        c.check("refuses on corrupt settings (non-zero exit)",
                r.returncode != 0, f"rc={r.returncode}")
        c.check("does NOT overwrite the corrupt file",
                open(bad).read() == "{ this is not valid json ",
                "the file was modified")


def test_installed_chat_runs(c):
    with tmp_root() as root:
        target = os.path.join(root, "repo")
        os.makedirs(target)
        cli(["install", target], env_for(root))
        installed = os.path.join(target, ".groupchat", "chat.py")
        # Run the COPIED chat.py against its own isolated room.
        import subprocess
        env = dict(os.environ)
        env["GROUPCHAT_DIR"] = os.path.join(root, "room")
        env.pop("CLAUDE_PROJECT_DIR", None)
        r = subprocess.run([sys.executable, installed, "init"],
                           capture_output=True, text=True, env=env, timeout=20)
        c.check("the copied chat.py runs (init exits 0)", r.returncode == 0, r.stderr)


def main():
    c = Checker("install / onboarding")
    for fn in (test_fresh_install, test_install_is_idempotent,
               test_install_merges_existing_settings,
               test_install_refuses_corrupt_settings, test_installed_chat_runs):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
