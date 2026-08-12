#!/usr/bin/env python3
"""Roster / identity bridge (push-wake P2).

The blindspot doc's P2 (docs/plans/2026-08-09-native-cross-session-messaging-
blindspot.md): an agora agent is ALSO a native Claude Code session, and the two
show disjoint rosters — agora `ada` vs native `agora-bb`. P2 closes the gap:

  * `bootstrap` names each spawned session with `-n <handle>` so `/list-agents`
    and `who` agree going forward;
  * `who` reads Claude Code's own session registry (best-effort, fail-open) and
    surfaces the native alias `⇄<name>` on a mismatch, plus a `⌁` push-reachable
    marker and a `push-reachable: K/N` tally.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/native_bridge_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, env_for, init_room, tmp_root,
)


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def _write_native(cfg_dir, pid, session_id, name, status="idle",
                  socket="/tmp/cc-socks/x.sock"):
    d = os.path.join(cfg_dir, "sessions")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{pid}.json"), "w") as fh:
        json.dump({"pid": pid, "sessionId": session_id, "name": name,
                   "status": status, "messagingSocketPath": socket}, fh)


def main() -> int:
    c = Checker("roster / identity bridge (P2)")
    chat = _import_chat()

    # ---- bootstrap names the native session (-n <handle>) -------------------
    cmd = chat._spawn_command("ada", "/tmp/repo", None)
    c.check("spawn command sets the native name to the handle (-n ada)",
            " -n ada" in cmd, cmd)
    cmd2 = chat._spawn_command("front-end", "/tmp/repo", "build the UI")
    c.check("native name is shell-quoted and precedes the prompt",
            " -n front-end " in cmd2 and cmd2.rstrip().endswith("'build the UI'"), cmd2)

    # ---- _native_sessions reads the registry (honoring CLAUDE_CONFIG_DIR) ----
    with tmp_root() as root:
        cfg = os.path.join(root, "cchome")
        _write_native(cfg, 4242, "sess-ada", "agora-bb", status="busy")
        _write_native(cfg, 4243, "sess-bob", "bob", status="idle")
        # malformed file must not blind the rest
        os.makedirs(os.path.join(cfg, "sessions"), exist_ok=True)
        with open(os.path.join(cfg, "sessions", "junk.json"), "w") as fh:
            fh.write("{not json")
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = cfg
        try:
            got = chat._native_sessions()
        finally:
            if old is None:
                del os.environ["CLAUDE_CONFIG_DIR"]
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        c.check("_native_sessions keys by sessionId, skips the malformed file",
                got.get("sess-ada", {}).get("name") == "agora-bb"
                and got.get("sess-bob", {}).get("status") == "idle"
                and len(got) == 2, str(got))

    # ---- who surfaces the alias on a mismatch, and the ⌁ reachable marker ----
    with tmp_root() as root:
        env = init_room(root)
        cfg = os.path.join(root, "cchome")
        # ada: agora handle differs from the native name -> alias shown.
        cli(["register", "--session", "sess-ada", "--from", "ada"],
            env_for(root, CLAUDE_CODE_MESSAGING_SOCKET="/tmp/cc-socks/ada.sock",
                    CLAUDE_CONFIG_DIR=cfg))
        _write_native(cfg, 5001, "sess-ada", "agora-bb")
        # bob: native name already agrees (a bootstrap -n child) -> no alias.
        cli(["register", "--session", "sess-bob", "--from", "bob"],
            env_for(root, CLAUDE_CONFIG_DIR=cfg))
        _write_native(cfg, 5002, "sess-bob", "bob")

        r = cli(["who"], env_for(root, CLAUDE_CONFIG_DIR=cfg))
        out = r.stdout
        c.check("who shows the native alias only on a mismatch (ada ⇄agora-bb)",
                "⇄agora-bb" in out, out)
        c.check("who does NOT alias a handle that already matches (bob)",
                "⇄bob" not in out, out)
        # ada has a captured inbox socket -> reachable marker on its row.
        ada_line = next((ln for ln in out.splitlines()
                         if ln.split(" ")[1:2] == ["ada"]), "")
        c.check("the push-reachable ⌁ marks ada's row (has an inbox socket)",
                "⌁" in ada_line, ada_line)
        c.check("push-reachable tally is shown for the team",
                "push-reachable: 1/2" in out, out)

    # ---- fail-open: no registry at all -> who still works, no annotations ----
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "turing"], env)
        # Point CLAUDE_CONFIG_DIR at an empty dir so no native data exists.
        r = cli(["who"], env_for(root, CLAUDE_CONFIG_DIR=os.path.join(root, "empty")))
        c.check("who works with no native registry (fail-open)",
                r.returncode == 0 and "ada" in r.stdout and "⇄" not in r.stdout,
                r.stdout)
        c.check("no push-reachable line when no agent has an inbox",
                "push-reachable" not in r.stdout, r.stdout)

    return c.done()


if __name__ == "__main__":
    sys.exit(main())
