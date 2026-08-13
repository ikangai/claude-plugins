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
    "GROUPCHAT_QUIET_SECS", "GROUPCHAT_SQUAD",
)


def env_for(root: str, **extra) -> dict:
    """A clean environment pointing the bus at an isolated dir under ``root``."""
    env = dict(os.environ)
    # Scrub BOTH spellings (groupchat→agora rename) so a parent session's AGORA_TEAM_SIZE
    # etc. can't skew a barrier assertion; the explicit _SCRUB list documents the knobs.
    for k in list(env):
        if k.startswith("GROUPCHAT_") or k.startswith("AGORA_"):
            env.pop(k, None)
    env["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    env.pop("CLAUDE_PROJECT_DIR", None)
    # A test run inside a live Claude session inherits that session's native inbox
    # socket; register() would capture it and send() would push-wake nudges INTO the
    # developer's own conversation. Scrub it — push tests opt back in via **extra.
    env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
    for k in _SCRUB:
        env.pop(k, None)
    for k, v in extra.items():
        env[k] = str(v)
    return env


def worker_env(root: str, **extra) -> dict:
    """env_for a *bootstrap-spawned* (unattended) agent — the config that PARKS at the
    team barrier. A plain ``env_for()`` models a human-launched, *attended* session,
    which never parks (its terminal must stay responsive); only spawned workers do.
    ``spawned_by`` is stamped once at register from this env, so pass this to the call
    that first registers the agent whose park behavior a test exercises."""
    return env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator", **extra)


class _Result:
    """A subprocess.CompletedProcess look-alike for the in-process `cli` path."""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


_chat_mod = None


def _chat():
    """Import chat.py once and reuse it. Safe to keep resident: chat has no mutable
    module-level state (fresh sqlite connection per call; store_dir() reads the env
    fresh; HANDLE_POOL is read-only), and main(argv)->int never sys.exits."""
    global _chat_mod
    if _chat_mod is None:
        sys.path.insert(0, GROUPCHAT)
        import chat as _c
        _chat_mod = _c
    return _chat_mod


def cli(args, env, stdin: str | None = None, timeout: int = 30):
    """Run a ``chat.py`` command IN-PROCESS (chat.main), not as a subprocess — the
    subprocess only ever re-imported the ~5k-line module (~90ms) to run a stateless
    command, and the suite makes hundreds of these. chat is dispatched under the
    call's ``env`` (os.environ swapped and restored) with stdout/stderr captured, so
    a caller sees the same returncode/stdout/stderr a subprocess produced. Isolation
    is preserved because chat keeps no cross-call state. (Hooks stay as subprocesses
    — they're separate scripts, and stop.py's park loop blocks.)"""
    import contextlib
    import io
    import traceback
    chat = _chat()
    out, err = io.StringIO(), io.StringIO()
    saved_env = os.environ.copy()
    saved_stdin = sys.stdin
    try:
        os.environ.clear()
        os.environ.update(env)
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = chat.main([str(a) for a in args])
            except SystemExit as e:          # argparse errors exit(2), etc.
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            except Exception:                # a crash reads like a non-zero subprocess
                err.write(traceback.format_exc())
                rc = 1
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.stdin = saved_stdin
    return _Result(0 if rc is None else rc, out.getvalue(), err.getvalue())


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
