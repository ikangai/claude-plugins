#!/usr/bin/env python3
"""groupchat doctor — a one-command health & staleness check for the bus.

Motivation (see .dev-diary/2026-06-06-groupchat-install-drift.md): installs
drift and *nothing in the system announces it*; and a subtle code regression
(e.g. a duplicate top-level ``def`` that shadows an earlier one) can silently
disable a whole feature because the hooks fail open. ``doctor`` turns "diff files
and grep for keywords" into one command, and adds runtime smoke checks that
would have caught the dead-barrier bug (duplicate ``_env_int``) immediately.

Run:

    python3 .groupchat/doctor.py          # full report; exit 0 = healthy
    python3 .groupchat/doctor.py -q       # only warnings/failures + summary

What it checks (▲ = fails the run; others are warnings/info):
  ▲ chat.py imports cleanly
  ▲ no shadowed top-level defs in chat.py / doctor.py (the bug-class catcher)
  ▲ barrier functions don't raise (team_done / expected_team_size / max_park)
  ▲ every hook script compiles
  ▲ every hook fails open (exit 0) on empty stdin
    schema health for the live room (expected tables & columns)
    hook wiring in .claude/settings.json
    room snapshot (active agents, message count, schema version)

Dependency-free; safe to run anytime (room checks are read-only; smoke checks
use a throwaway temp room, never the live one).
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT_PY = os.path.join(HERE, "chat.py")
DOCTOR_PY = os.path.abspath(__file__)
HOOKS_DIR = os.path.join(HERE, "hooks")


# --------------------------------------------------------------------------- #
# Tiny reporter
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str):
        if not self.quiet:
            print(f"  \033[32m✓\033[0m {msg}")

    def info(self, msg: str):
        if not self.quiet:
            print(f"    {msg}")

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(f"  \033[33m!\033[0m {msg}")

    def fail(self, msg: str):
        self.failures.append(msg)
        print(f"  \033[31m✗\033[0m {msg}")

    def section(self, title: str):
        if not self.quiet:
            print(f"\n\033[1m{title}\033[0m")

    def summary(self) -> int:
        print("\n" + "=" * 60)
        if not self.failures and not self.warnings:
            print("\033[32mgroupchat doctor: healthy — all checks passed.\033[0m")
        else:
            print(f"groupchat doctor: {len(self.failures)} failure(s), "
                  f"{len(self.warnings)} warning(s).")
            for f in self.failures:
                print(f"  ✗ {f}")
            for w in self.warnings:
                print(f"  ! {w}")
        return 1 if self.failures else 0


# --------------------------------------------------------------------------- #
# Static checks
# --------------------------------------------------------------------------- #
def _shadowed_defs(path: str) -> list[str]:
    """Top-level function/class names defined more than once — a second def
    silently shadows the first (the root cause of the dead-barrier bug)."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    except SyntaxError as e:
        return [f"__syntax__: {e}"]
    seen: dict[str, int] = {}
    for node in tree.body:  # module level only
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name] = seen.get(node.name, 0) + 1
    return sorted(n for n, count in seen.items() if count > 1)


def check_imports_clean(rep: Report):
    rep.section("Code integrity")
    spec = importlib.util.spec_from_file_location("_gc_chat_doctor", CHAT_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        rep.ok("chat.py imports cleanly")
        return mod
    except Exception as e:
        rep.fail(f"chat.py fails to import: {type(e).__name__}: {e}")
        return None


def check_no_shadowed_defs(rep: Report):
    for label, path in (("chat.py", CHAT_PY), ("doctor.py", DOCTOR_PY)):
        dups = _shadowed_defs(path)
        if dups and dups[0].startswith("__syntax__"):
            rep.fail(f"{label} has a syntax error: {dups[0]}")
        elif dups:
            rep.fail(f"{label} has shadowed top-level def(s): {', '.join(dups)} "
                     "— a later definition silently overrides the earlier one "
                     "(this is the bug-class that killed the team barrier)")
        else:
            rep.ok(f"{label}: no shadowed top-level definitions")


def check_barrier_smoke(rep: Report, chat):
    """The runtime symptom of the duplicate-_env_int bug: these raise. Exercise
    them against a throwaway room so a regression can never hide behind the
    hooks' fail-open wrapper again."""
    rep.section("Barrier smoke (isolated room)")
    if chat is None:
        rep.warn("skipped — chat.py did not import")
        return
    saved = {k: os.environ.get(k) for k in ("GROUPCHAT_DIR", "AGORA_DIR")}
    with tempfile.TemporaryDirectory(prefix="gc_doctor_") as tmp:
        # Set BOTH spellings — AGORA_DIR wins in store_dir, so leaving an ambient one
        # would point this probe at the operator's real room instead of the throwaway.
        room = os.path.join(tmp, ".agora")
        os.environ["GROUPCHAT_DIR"] = os.environ["AGORA_DIR"] = room
        try:
            conn = chat.connect()
            for name, fn in (
                ("expected_team_size", lambda: chat.expected_team_size(conn)),
                ("max_park_seconds", lambda: chat.max_park_seconds()),
                ("team_done", lambda: chat.team_done(conn)),
                ("startup_guard_satisfied", lambda: chat.startup_guard_satisfied(conn)),
            ):
                try:
                    fn()
                    rep.ok(f"{name}() does not raise")
                except Exception as e:
                    rep.fail(f"{name}() raises {type(e).__name__}: {e} "
                             "— the team barrier is dead (fails open in stop.py)")
            conn.close()
        finally:
            for k, v in saved.items():
                (os.environ.__setitem__(k, v) if v is not None
                 else os.environ.pop(k, None))


# --------------------------------------------------------------------------- #
# Hook checks
# --------------------------------------------------------------------------- #
HOOK_FILES = ("session_start.py", "user_prompt_submit.py", "stop.py")


def check_hooks_compile(rep: Report):
    rep.section("Hooks")
    for h in HOOK_FILES:
        p = os.path.join(HOOKS_DIR, h)
        if not os.path.isfile(p):
            rep.fail(f"missing hook script: {h}")
            continue
        try:
            compile(open(p, encoding="utf-8").read(), p, "exec")
            rep.ok(f"{h} compiles")
        except SyntaxError as e:
            rep.fail(f"{h} has a syntax error: {e}")


def check_hooks_fail_open(rep: Report):
    """Every hook MUST exit 0 on empty stdin — a non-zero exit from
    UserPromptSubmit would block the user's prompt; from Stop would wedge it."""
    saved = os.environ.get("GROUPCHAT_DIR")
    with tempfile.TemporaryDirectory(prefix="gc_doctor_") as tmp:
        env = dict(os.environ)
        # Both spellings — AGORA_DIR wins in store_dir, so a copied-in ambient one
        # would shadow GROUPCHAT_DIR and aim the probe at the real room.
        env["GROUPCHAT_DIR"] = env["AGORA_DIR"] = os.path.join(tmp, ".agora")
        env.pop("CLAUDE_PROJECT_DIR", None)
        for h in HOOK_FILES:
            p = os.path.join(HOOKS_DIR, h)
            if not os.path.isfile(p):
                continue
            try:
                r = subprocess.run([sys.executable, p], input="",
                                   capture_output=True, text=True, env=env, timeout=20)
                if r.returncode == 0:
                    rep.ok(f"{h} fails open on empty stdin (exit 0)")
                else:
                    rep.fail(f"{h} exited {r.returncode} on empty stdin "
                             "(must fail open!)")
            except Exception as e:
                rep.fail(f"{h} crashed on empty stdin: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# Room / wiring (live, read-only)
# --------------------------------------------------------------------------- #
EXPECTED = {
    "messages": {"id", "ts", "sender", "session_id", "kind", "body", "mentions"},
    "agents": {"session_id", "handle", "cwd", "pid", "status", "first_seen",
               "last_seen", "last_read_id", "in_tokens", "out_tokens",
               "cache_read_tokens", "cache_create_tokens", "spawn_depth", "spawned_by",
               "focus", "parks", "squad", "model"},
    "meta": {"key", "value"},
    "rule_cites": {"id", "ts", "rule_id", "sender", "message_id"},
    "motions": {"id", "ts", "proposer", "target", "op", "change", "because",
                "base_text", "new_id", "title", "status", "session_id"},
    "votes": {"id", "ts", "motion_id", "voter_session", "voter_handle", "vote"},
    "tasks": {"id", "ts", "title", "owner", "status", "paths", "creator"},
    "claims": {"id", "ts", "session_id", "handle", "glob"},
    "dismissed": {"session_id", "ts"},
}


def check_room(rep: Report, chat):
    rep.section("Live room")
    if chat is None:
        return
    dbp = chat.db_path()
    if not os.path.isfile(dbp):
        rep.warn(f"no room database yet at {dbp} "
                 "(it bootstraps on first connect — fine for a fresh checkout)")
        return
    import sqlite3
    try:
        conn = sqlite3.connect(dbp)
        conn.row_factory = sqlite3.Row
        have_tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as e:
        # A corrupt / non-SQLite file is exactly the unhealthy state doctor exists
        # to report — never let it crash the checker.
        rep.fail(f"room db at {dbp} is unreadable / not a SQLite database: {e}")
        return
    for table, cols in EXPECTED.items():
        if table not in have_tables:
            rep.fail(f"table '{table}' is missing from the room db")
            continue
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        missing = cols - present
        if missing:
            rep.fail(f"table '{table}' missing column(s): {', '.join(sorted(missing))}")
        else:
            rep.ok(f"table '{table}' has all expected columns")
        # The drift tell from the diary: columns the current code wouldn't create.
        extra = present - cols
        if extra:
            rep.warn(f"table '{table}' has unexpected column(s): {', '.join(sorted(extra))} "
                     "(schema ahead of code? possible install drift)")
    # Snapshot.
    try:
        nmsg = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        nact = len(chat.active_agents(conn))
        ver = chat.get_meta(conn, "schema_version", "?")
        rep.info(f"room: {nmsg} messages, {nact} active agent(s), schema_version={ver}")
    except Exception as e:
        rep.warn(f"could not read room snapshot: {e}")
    conn.close()


def check_wiring(rep: Report, chat):
    rep.section("Hook wiring (.claude/settings.json)")
    if chat is None:
        return
    root = chat.repo_root()
    settings = os.path.join(root, ".claude", "settings.json")
    if not os.path.isfile(settings):
        rep.warn(f"no {settings} — run `chat.py install {root}` to wire the hooks "
                 "(or this repo may be plugin-wired elsewhere)")
        return
    import json
    try:
        data = json.load(open(settings))
    except Exception as e:
        rep.fail(f"{settings} is not valid JSON: {e}")
        return
    if not isinstance(data, dict):
        rep.fail(f"{settings} is valid JSON but not an object (got "
                 f"{type(data).__name__}) — Claude Code expects a settings object")
        return
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    for event in ("SessionStart", "UserPromptSubmit", "Stop"):
        groups = hooks.get(event) if isinstance(hooks.get(event), list) else []
        cmds = [h.get("command", "") for g in groups
                for h in (g.get("hooks", []) if isinstance(g, dict) else [])]
        if any("groupchat" in c or ".groupchat" in c for c in cmds):
            rep.ok(f"{event} hook is wired")
        else:
            rep.warn(f"{event} hook not found in settings.json")


def check_cross_cli_wiring(rep: Report, chat):
    """Cross-CLI hosts (Codex / opencode / generic) wire the SAME hook scripts via
    their own config files. A config that points at a hook path which no longer
    exists is the heterogeneous-fleet version of the install-drift failure mode —
    so validate the referenced paths actually resolve. Absence of a config is
    fine (Claude-Code-only repo); a *broken* one is a failure."""
    rep.section("Cross-CLI wiring (Codex / opencode / generic)")
    if chat is None:
        return
    import json
    import re
    root = chat.repo_root()
    found_any = False

    codex = os.path.join(root, ".codex", "hooks.json")
    if os.path.isfile(codex):
        found_any = True
        try:
            data = json.load(open(codex))
        except Exception as e:
            rep.fail(f".codex/hooks.json is not valid JSON: {e}")
            data = None
        if data is not None and not isinstance(data, dict):
            rep.fail(".codex/hooks.json is valid JSON but not an object (got "
                     f"{type(data).__name__})")
            data = None
        if data:
            hk = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
            cmds = [h.get("command", "")
                    for groups in hk.values() if isinstance(groups, list)
                    for g in groups if isinstance(g, dict)
                    for h in (g.get("hooks", []) if isinstance(g.get("hooks"), list) else [])
                    if isinstance(h, dict)]
            paths = [m.group(1) for c in cmds
                     for m in [re.search(r'"([^"]+)"', c)] if m]
            missing = [p for p in paths if not os.path.isfile(p)]
            if not paths:
                rep.warn(".codex/hooks.json has no recognizable hook commands")
            elif missing:
                rep.fail(".codex/hooks.json points at missing script(s): "
                         + ", ".join(missing) + " — cross-CLI install drift "
                         "(re-run `python3 bridge/install.py codex`)")
            else:
                rep.ok(f".codex/hooks.json wires {len(paths)} hook(s), all present")

    oc = os.path.join(root, ".opencode", "plugins", "groupchat.js")
    if os.path.isfile(oc):
        found_any = True
        rep.ok(".opencode/plugins/groupchat.js present")

    agents_md = os.path.join(root, "AGENTS.md")
    if os.path.isfile(agents_md):
        try:
            txt = open(agents_md, encoding="utf-8").read()
        except Exception:
            txt = ""
        if "chat.py" in txt and "group" in txt.lower():
            found_any = True
            rep.ok("AGENTS.md carries the agora floor block")

    if not found_any:
        rep.info("no cross-CLI host configs found (Claude Code only — fine)")


def main(argv):
    quiet = "-q" in argv
    print("\033[1mgroupchat doctor\033[0m  —  " + HERE)
    rep = Report(quiet=quiet)
    chat = check_imports_clean(rep)
    # Defense in depth: a health checker must NEVER crash — an unexpected error in
    # one check becomes a reported failure, and the run still reaches summary().
    for fn in (check_no_shadowed_defs, check_barrier_smoke, check_hooks_compile,
               check_hooks_fail_open, check_room, check_wiring,
               check_cross_cli_wiring):
        try:
            # check_no_shadowed_defs/check_hooks_* don't take chat; pass only what's needed.
            if fn in (check_no_shadowed_defs, check_hooks_compile, check_hooks_fail_open):
                fn(rep)
            else:
                fn(rep, chat)
        except Exception as e:
            rep.fail(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
    return rep.summary()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
