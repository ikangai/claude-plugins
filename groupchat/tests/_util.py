#!/usr/bin/env python3
"""Shared, dependency-free helpers for the groupchat test suite.

The product's *core transport* (identity, messaging, the read cursor, the team
barrier, and the three hooks) had no automated tests — only the constitution
layer did. These helpers let the core tests follow the same conventions the
constitution tests already use:

  * isolate every run via ``GROUPCHAT_DIR`` so the live room is never touched;
  * drive ``chat.py`` and the hooks via subprocess, exactly as Claude Code does
    (JSON on stdin for hooks);
  * no third-party deps — stdlib only, matching the repo's "no framework" rule.

Each test module builds a ``Checker``, runs its cases, and exits non-zero if any
fail. ``run_all.py`` aggregates them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GROUPCHAT = os.path.join(ROOT, ".groupchat")
CHAT = os.path.join(GROUPCHAT, "chat.py")
HOOKS = os.path.join(GROUPCHAT, "hooks")

# Park/barrier tunables that a live `/goal` session may export into our env.
# Tests must control these explicitly, so we scrub them from the inherited env
# and let each call opt back in. (Without this, a parent session's
# GROUPCHAT_TEAM_SIZE would silently skew every barrier assertion.)
_SCRUB = (
    "GROUPCHAT_TEAM_SIZE", "GROUPCHAT_MAX_PARK", "GROUPCHAT_SOLO_GRACE",
    "GROUPCHAT_PARK_WINDOW", "GROUPCHAT_POLL_TICK", "GROUPCHAT_AMEND_SUPERMAJORITY",
    "GROUPCHAT_AMEND_QUORUM", "GROUPCHAT_REVIEW_LOW",
    "GROUPCHAT_HANDLE", "GROUPCHAT_SPAWN_DEPTH", "GROUPCHAT_SPAWNED_BY",
    "GROUPCHAT_MAX_SPAWN_DEPTH", "GROUPCHAT_MAX_FLEET", "GROUPCHAT_LEAD",
)


def env_for(root: str, **extra) -> dict:
    """A clean environment pointing the bus at an isolated dir under ``root``."""
    env = dict(os.environ)
    env["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    env.pop("CLAUDE_PROJECT_DIR", None)
    for k in _SCRUB:
        env.pop(k, None)
    for k, v in extra.items():
        env[k] = str(v)
    return env


def cli(args, env, stdin: str | None = None, timeout: int = 30):
    """Run ``chat.py <args>`` and capture the result."""
    return subprocess.run(
        [sys.executable, CHAT, *args],
        capture_output=True, text=True, env=env, input=stdin, timeout=timeout,
    )


def hook(name: str, env, payload, timeout: int = 30):
    """Run a hook script (``session_start.py`` / ``user_prompt_submit.py`` /
    ``stop.py``) with a JSON payload on stdin, as Claude Code does."""
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    return subprocess.run(
        [sys.executable, os.path.join(HOOKS, name)],
        capture_output=True, text=True, env=env, input=payload, timeout=timeout,
    )


def db_path(root: str) -> str:
    return os.path.join(root, ".groupchat", "chat.db")


def db(root: str) -> sqlite3.Connection:
    """Open the isolated db directly — for arranging state (e.g. ageing an agent
    out of the active window) that the CLI deliberately doesn't expose."""
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    return conn


def init_room(root: str) -> dict:
    """Create the db in an isolated room and return its env."""
    env = env_for(root)
    cli(["init"], env)
    return env


def parse_hook_json(stdout: str):
    """Hooks print a single JSON object (or nothing). Return it, or None."""
    out = stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except Exception:
        # Some hooks may print multiple lines; try the last JSON-looking one.
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    continue
        return None


class Checker:
    """Minimal assert-and-tally helper (no pytest — stdlib only)."""

    def __init__(self, title: str):
        self.title = title
        self.failures: list[str] = []
        self.passes = 0
        print(f"\n=== {title} ===")

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        ok = bool(cond)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  -- {detail}"))
        if ok:
            self.passes += 1
        else:
            self.failures.append(name)
        return ok

    def done(self) -> int:
        n = len(self.failures)
        if n:
            print(f"--- {self.title}: {n} FAILED ({', '.join(self.failures)}); "
                  f"{self.passes} passed")
        else:
            print(f"--- {self.title}: all {self.passes} passed")
        return 1 if n else 0


def tmp_root():
    """A fresh temp directory context manager."""
    return tempfile.TemporaryDirectory(prefix="gc_test_")
