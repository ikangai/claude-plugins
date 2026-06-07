#!/usr/bin/env python3
"""Cross-CLI integration tests — the Codex adapter (iteration 1).

Two things are under test:

  1. The **installer** ``bridge/install.py`` that generates a host's wiring file.
     For Codex that is ``.codex/hooks.json`` pointing the three lifecycle events
     at the *existing* ``.groupchat/hooks/*.py`` scripts. (NEW code → TDD.)

  2. The **contract claim** the whole adapter rests on: Codex's command-hook I/O
     is byte-identical to Claude Code's, so the existing hooks already accept a
     Codex-shaped payload (extra ``model`` / ``permission_mode`` / ``turn_id`` /
     ``last_assistant_message`` fields and all) and emit valid output. (Verifies
     EXISTING behaviour → these pass from the start; they guard the dependency.)

Dependency-free, isolated via ``GROUPCHAT_DIR``, run:

    python3 tests/cross_cli_test.py     # exit 0 = all pass
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _util import Checker, env_for, cli, hook, db, tmp_root, ROOT, HOOKS, parse_hook_json

# The installer is NEW code living outside .groupchat (it only writes host wiring,
# never touches chat.py/hooks). Guard the import so a missing module reports a
# clean FAIL during the RED phase instead of erroring the whole run.
sys.path.insert(0, os.path.join(ROOT, "bridge"))
try:
    import install as bridge  # bridge/install.py
    HAVE = True
except Exception as e:  # noqa: BLE001
    bridge = None
    HAVE = False
    _IMPORT_ERR = repr(e)

CODEX_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop"}
HOOK_FILES = {
    "SessionStart": "session_start.py",
    "UserPromptSubmit": "user_prompt_submit.py",
    "Stop": "stop.py",
}


# --------------------------------------------------------------------------- #
# 1. Installer — pure config generation (unit)
# --------------------------------------------------------------------------- #
def test_config_shape(c):
    if not c.check("bridge/install.py importable", HAVE,
                   _IMPORT_ERR if not HAVE else ""):
        return
    hooks_dir = "/tmp/example/.groupchat/hooks"
    cfg = bridge.codex_hooks_config(hooks_dir)
    c.check("config has a 'hooks' object", isinstance(cfg.get("hooks"), dict))
    c.check("wires exactly the three lifecycle events",
            set(cfg.get("hooks", {})) == CODEX_EVENTS,
            f"got {set(cfg.get('hooks', {}))}")

    # Each event points its command at the matching hook file, absolute path.
    for event, fname in HOOK_FILES.items():
        groups = cfg["hooks"].get(event, [])
        cmds = [h.get("command", "")
                for g in groups for h in g.get("hooks", [])]
        c.check(f"{event} wired to {fname}",
                any(fname in cmd and os.path.join(hooks_dir, fname) in cmd
                    for cmd in cmds),
                f"commands={cmds}")
        c.check(f"{event} command type is 'command'",
                all(h.get("type") == "command"
                    for g in groups for h in g.get("hooks", [])))

    # Stop parks at the barrier (long sleep) → needs a long timeout, like Claude's.
    stop_entries = [h for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
    c.check("Stop hook gets a long timeout (>=600) for barrier parking",
            any(int(h.get("timeout", 0)) >= 600 for h in stop_entries),
            f"timeouts={[h.get('timeout') for h in stop_entries]}")

    # SessionStart matches the fresh-context sources Codex emits.
    ss_groups = cfg["hooks"]["SessionStart"]
    c.check("SessionStart carries a source matcher",
            any("matcher" in g for g in ss_groups),
            f"groups={ss_groups}")


def test_merge_idempotent(c):
    if not HAVE:
        return
    hooks_dir = "/tmp/example/.groupchat/hooks"
    merged, added = bridge.merge_codex_hooks({}, hooks_dir)
    c.check("first merge adds three events", added == 3, f"added={added}")
    merged2, added2 = bridge.merge_codex_hooks(merged, hooks_dir)
    c.check("second merge is a no-op (idempotent)", added2 == 0, f"added={added2}")
    # No duplicate commands after a double merge.
    for event in CODEX_EVENTS:
        cmds = [h["command"] for g in merged2["hooks"][event] for h in g["hooks"]]
        c.check(f"{event} has no duplicate command", len(cmds) == len(set(cmds)),
                f"cmds={cmds}")


def test_merge_preserves_existing(c):
    if not HAVE:
        return
    existing = {
        "model": "gpt-5-codex",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo mine"}]}],
        },
    }
    merged, added = bridge.merge_codex_hooks(existing, "/x/.groupchat/hooks")
    c.check("user's unrelated top-level keys preserved",
            merged.get("model") == "gpt-5-codex")
    c.check("user's own PreToolUse hook preserved",
            any(h.get("command") == "echo mine"
                for g in merged["hooks"].get("PreToolUse", [])
                for h in g.get("hooks", [])))
    c.check("our three events were added", added == 3)


# --------------------------------------------------------------------------- #
# 2. Installer — CLI writes a real .codex/hooks.json (integration)
# --------------------------------------------------------------------------- #
def _seed_target(root):
    """A target repo with placeholder hook files (installer only needs them to
    EXIST to wire them — it never executes them)."""
    hd = os.path.join(root, ".groupchat", "hooks")
    os.makedirs(hd, exist_ok=True)
    for fname in HOOK_FILES.values():
        with open(os.path.join(hd, fname), "w") as fh:
            fh.write("# placeholder\n")
    return hd


def _run_installer(host, target):
    import subprocess
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "bridge", "install.py"), host, target],
        capture_output=True, text=True, timeout=30)


def test_cli_writes_codex_config(c):
    if not HAVE:
        return
    with tmp_root() as root:
        hd = _seed_target(root)
        r = _run_installer("codex", root)
        c.check("installer exits 0", r.returncode == 0, r.stderr)
        cfg_path = os.path.join(root, ".codex", "hooks.json")
        c.check("writes .codex/hooks.json", os.path.isfile(cfg_path), cfg_path)
        if not os.path.isfile(cfg_path):
            return
        cfg = json.load(open(cfg_path))
        c.check("config valid JSON with three events",
                set(cfg.get("hooks", {})) == CODEX_EVENTS)
        # Commands point at THIS target's hooks (absolute).
        all_cmds = [h["command"] for ev in CODEX_EVENTS
                    for g in cfg["hooks"][ev] for h in g["hooks"]]
        c.check("commands reference the target's absolute hook paths",
                all(hd in cmd for cmd in all_cmds), f"cmds={all_cmds}")

        # Idempotent: a second run reports no new hooks and doesn't duplicate.
        r2 = _run_installer("codex", root)
        c.check("second install exits 0", r2.returncode == 0, r2.stderr)
        cfg2 = json.load(open(cfg_path))
        cmds2 = [h["command"] for ev in CODEX_EVENTS
                 for g in cfg2["hooks"][ev] for h in g["hooks"]]
        c.check("re-install does not duplicate commands",
                len(cmds2) == len(set(cmds2)) == 3, f"cmds={cmds2}")


def test_cli_refuses_without_hooks(c):
    """Don't wire hooks that don't exist — a config pointing at missing scripts is
    worse than no config. Must fail loudly and write nothing."""
    if not HAVE:
        return
    with tmp_root() as root:
        # no .groupchat/hooks seeded
        r = _run_installer("codex", root)
        c.check("installer fails when hooks are absent", r.returncode != 0)
        c.check("no .codex/hooks.json written on failure",
                not os.path.isfile(os.path.join(root, ".codex", "hooks.json")))


# --------------------------------------------------------------------------- #
# 3. Contract claim — the EXISTING hooks accept Codex-shaped payloads
# --------------------------------------------------------------------------- #
def _codex_payload(event, session_id, cwd, **extra):
    """A Codex command-hook stdin payload: the Claude fields PLUS the extras Codex
    adds (model, permission_mode, turn_id, last_assistant_message)."""
    base = {
        "session_id": session_id,
        "cwd": cwd,
        "hook_event_name": event,
        "transcript_path": None,
        "model": "gpt-5-codex",
        "permission_mode": "default",
    }
    base.update(extra)
    return json.dumps(base)


def _handle_of(root, session_id):
    conn = db(root)
    row = conn.execute("SELECT handle FROM agents WHERE session_id=?",
                       (session_id,)).fetchone()
    conn.close()
    return row["handle"] if row else None


def test_codex_session_start(c):
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_MAX_PARK=0)
        cli(["init"], env)
        payload = _codex_payload("SessionStart", "codex-A", root, source="startup")
        r = hook("session_start.py", env, payload)
        c.check("session_start exits 0 on a Codex payload", r.returncode == 0, r.stderr)
        obj = parse_hook_json(r.stdout)
        c.check("emits hookSpecificOutput.additionalContext",
                bool(obj and obj.get("hookSpecificOutput", {}).get("additionalContext")),
                r.stdout[:200])
        handle = _handle_of(root, "codex-A")
        c.check("registers the Codex agent", handle is not None)
        if obj and handle:
            ctx = obj["hookSpecificOutput"]["additionalContext"]
            c.check("briefing names this agent's handle", handle in ctx)


def test_codex_user_prompt_inject(c):
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_MAX_PARK=0)
        cli(["init"], env)
        # Register A (caught up), then a teammate posts after → A has unread.
        hook("session_start.py", env,
             _codex_payload("SessionStart", "codex-A", root, source="startup"))
        a = _handle_of(root, "codex-A")
        cli(["send", "--from", "mentor", f"@{a} please rebase onto main"], env)
        r = hook("user_prompt_submit.py", env,
                 _codex_payload("UserPromptSubmit", "codex-A", root,
                                turn_id="t1", prompt="continue"))
        c.check("user_prompt_submit exits 0 on a Codex payload",
                r.returncode == 0, r.stderr)
        obj = parse_hook_json(r.stdout)
        ctx = (obj or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
        c.check("injects the teammate's new message", "rebase onto main" in ctx,
                ctx[:200])


def test_codex_stop_blocks_on_mention(c):
    with tmp_root() as root:
        # MAX_PARK=0 + small window: even if it reached the barrier it wouldn't hang.
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_MAX_PARK=0,
                      GROUPCHAT_PARK_WINDOW=1, GROUPCHAT_POLL_TICK="0.1")
        cli(["init"], env)
        hook("session_start.py", env,
             _codex_payload("SessionStart", "codex-A", root, source="startup"))
        a = _handle_of(root, "codex-A")
        cli(["send", "--from", "mentor", f"@{a} can you take the parser?"], env)
        r = hook("stop.py", env,
                 _codex_payload("Stop", "codex-A", root,
                                turn_id="t1", stop_hook_active=False,
                                last_assistant_message="ok"))
        c.check("stop exits 0 on a Codex payload", r.returncode == 0, r.stderr)
        obj = parse_hook_json(r.stdout)
        c.check("blocks the stop with decision=block",
                bool(obj) and obj.get("decision") == "block", r.stdout[:200])
        c.check("hands the mention back in the reason",
                bool(obj) and "take the parser" in (obj.get("reason") or ""),
                (obj or {}).get("reason", "")[:200])


# --------------------------------------------------------------------------- #
# 4. Generic adapter — the universal floor (AGENTS.md instruction block)
# --------------------------------------------------------------------------- #
GC_BEGIN = "<!-- GROUPCHAT:BEGIN"
GC_END = "<!-- GROUPCHAT:END"


def test_generic_appends_snippet(c):
    if not HAVE:
        return
    with tmp_root() as root:
        r = _run_installer("generic", root)
        c.check("generic install exits 0", r.returncode == 0, r.stderr)
        agents_md = os.path.join(root, "AGENTS.md")
        c.check("writes AGENTS.md", os.path.isfile(agents_md), agents_md)
        if not os.path.isfile(agents_md):
            return
        body = open(agents_md).read()
        c.check("snippet is marker-delimited",
                GC_BEGIN in body and GC_END in body)
        for needle in ("chat.py", "register", "read", "send"):
            c.check(f"snippet documents `{needle}`", needle in body)


def test_generic_idempotent(c):
    if not HAVE:
        return
    with tmp_root() as root:
        _run_installer("generic", root)
        _run_installer("generic", root)
        body = open(os.path.join(root, "AGENTS.md")).read()
        c.check("re-install does not duplicate the snippet",
                body.count(GC_BEGIN) == 1, f"count={body.count(GC_BEGIN)}")


def test_generic_preserves_existing(c):
    if not HAVE:
        return
    with tmp_root() as root:
        agents_md = os.path.join(root, "AGENTS.md")
        with open(agents_md, "w") as fh:
            fh.write("# My Project\n\nExisting agent instructions here.\n")
        _run_installer("generic", root)
        body = open(agents_md).read()
        c.check("pre-existing AGENTS.md content preserved",
                "# My Project" in body and "Existing agent instructions" in body)
        c.check("snippet appended below existing content",
                body.index("# My Project") < body.index(GC_BEGIN))


def test_generic_floor_documents_escalation(c):
    """Non-Claude hosts learn coordination ONLY from the AGENTS.md floor (they don't
    get the injected briefing). So the floor must teach the @human→lead escalation
    path, or a Codex/opencode/aider agent can't discover how to reach the operator."""
    if not HAVE:
        return
    with tmp_root() as root:
        _run_installer("generic", root)
        body = open(os.path.join(root, "AGENTS.md")).read()
        c.check("floor documents the @human escalation token", "@human" in body)
        c.check("floor explains @human funnels to the lead", "lead" in body.lower())


def test_generic_flow_roundtrips(c):
    """The manual flow the snippet documents must actually work: a generic agent
    registers by a chosen session id, a teammate @mentions it, and `read` surfaces
    the message and advances the cursor (so it isn't shown twice)."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "gen-1", "--cwd", root], env)
        # Assert on the authoritative db row, not register's stdout (robust under
        # the multi-agent load that can momentarily drop a subprocess's captured
        # stdout even on a clean rc=0).
        h = _handle_of(root, "gen-1")
        c.check("generic agent registers and gets a handle", bool(h), repr(h))
        if not h:
            return
        cli(["send", "--from", "mate", f"@{h} ping from a teammate"], env)
        first = cli(["read", "--session", "gen-1"], env)
        c.check("read surfaces the teammate message",
                "ping from a teammate" in first.stdout, first.stdout[:200])
        second = cli(["read", "--session", "gen-1"], env)
        c.check("cursor advanced — message not shown twice",
                "ping from a teammate" not in second.stdout, second.stdout[:120])


# --------------------------------------------------------------------------- #
# 5. opencode adapter — experimental plugin + the AGENTS.md floor
# --------------------------------------------------------------------------- #
import shutil

PLUGIN_SRC = os.path.join(ROOT, "bridge", "opencode", "groupchat.js")


def _seed_bus(root):
    """A target with a (placeholder) chat.py so install_* don't warn about a
    missing bus."""
    gc = os.path.join(root, ".groupchat")
    os.makedirs(gc, exist_ok=True)
    with open(os.path.join(gc, "chat.py"), "w") as fh:
        fh.write("# placeholder\n")


def test_opencode_installs_plugin_and_floor(c):
    if not HAVE:
        return
    with tmp_root() as root:
        _seed_bus(root)
        r = _run_installer("opencode", root)
        c.check("opencode install exits 0", r.returncode == 0, r.stderr)
        plugin = os.path.join(root, ".opencode", "plugins", "groupchat.js")
        c.check("copies the plugin to .opencode/plugins/groupchat.js",
                os.path.isfile(plugin), plugin)
        agents_md = os.path.join(root, "AGENTS.md")
        c.check("also installs the AGENTS.md floor", os.path.isfile(agents_md)
                and GC_BEGIN in open(agents_md).read())


def test_opencode_plugin_uses_documented_api(c):
    c.check("plugin source exists in bridge/opencode/", os.path.isfile(PLUGIN_SRC),
            PLUGIN_SRC)
    if not os.path.isfile(PLUGIN_SRC):
        return
    src = open(PLUGIN_SRC).read()
    # Only documented opencode primitives + a fail-open posture.
    for needle in ("shell.env", "event", "GROUPCHAT_SESSION", "register",
                   "chat.py", "catch"):
        c.check(f"plugin uses `{needle}`", needle in src)
    c.check("plugin reads the ctx props opencode actually provides",
            "worktree" in src and "directory" in src)
    c.check("plugin never advances the cursor (peek-only surface)",
            "--peek" in src)


def test_opencode_plugin_syntax_valid(c):
    """ESM syntax check via node (skipped if node is absent)."""
    node = shutil.which("node")
    if not node or not os.path.isfile(PLUGIN_SRC):
        c.check("node present for syntax check (skipped if absent)", True)
        return
    import subprocess
    with tmp_root() as root:
        mjs = os.path.join(root, "p.mjs")  # .mjs forces ESM parsing of `export`
        shutil.copy(PLUGIN_SRC, mjs)
        r = subprocess.run([node, "--check", mjs], capture_output=True, text=True)
        c.check("plugin is valid ESM (node --check)", r.returncode == 0,
                r.stderr[:300])


def test_opencode_inbox_peek_surfaces_without_advancing(c):
    """The plugin surfaces @mentions with `inbox --peek` — high-signal and it must
    NOT advance the cursor (the agent's own read owns the single cursor)."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "oc-1", "--cwd", root], env)
        h = _handle_of(root, "oc-1")
        if not c.check("opencode session registers", bool(h), repr(h)):
            return
        cli(["send", "--from", "mate", f"@{h} review my PR?"], env)
        peek1 = cli(["inbox", "--session", "oc-1", "--peek"], env)
        c.check("inbox --peek surfaces the mention", "review my PR?" in peek1.stdout)
        peek2 = cli(["inbox", "--session", "oc-1", "--peek"], env)
        c.check("peek did not advance the cursor (still visible)",
                "review my PR?" in peek2.stdout)


def test_codex_session_start_source_variants(c):
    """Codex emits SessionStart with source in {startup,resume,clear,compact}; the
    briefing must register + brief for each (the wiring matcher covers all four)."""
    for source in ("resume", "compact"):
        with tmp_root() as root:
            env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_MAX_PARK=0)
            cli(["init"], env)
            r = hook("session_start.py", env,
                     _codex_payload("SessionStart", "codex-A", root, source=source))
            obj = parse_hook_json(r.stdout)
            c.check(f"source={source}: registers + briefs",
                    r.returncode == 0 and _handle_of(root, "codex-A") is not None
                    and bool((obj or {}).get("hookSpecificOutput", {}).get("additionalContext")),
                    r.stdout[:160])


def test_codex_silent_when_nothing_new(c):
    """No new messages → the pre-turn hook must inject NOTHING (no per-turn noise)."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_MAX_PARK=0)
        cli(["init"], env)
        hook("session_start.py", env,
             _codex_payload("SessionStart", "codex-A", root, source="startup"))
        # No teammate posted since registration → caught up.
        r = hook("user_prompt_submit.py", env,
                 _codex_payload("UserPromptSubmit", "codex-A", root,
                                turn_id="t1", prompt="continue"))
        c.check("silent (no stdout) when nothing is new",
                r.returncode == 0 and r.stdout.strip() == "", repr(r.stdout[:120]))


def test_codex_stop_done_path(c):
    """Empty inbox + the whole (size-1) team done → Stop is allowed and the agent is
    marked done (the barrier-release path, driven by a Codex payload)."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_MAX_PARK=0,
                      GROUPCHAT_PARK_WINDOW=1, GROUPCHAT_POLL_TICK="0.1")
        cli(["init"], env)
        hook("session_start.py", env,
             _codex_payload("SessionStart", "codex-A", root, source="startup"))
        r = hook("stop.py", env,
                 _codex_payload("Stop", "codex-A", root, turn_id="t1",
                                stop_hook_active=False, last_assistant_message="done"),
                 timeout=20)
        c.check("stop allowed (no block) on an empty inbox", r.returncode == 0
                and parse_hook_json(r.stdout) is None, r.stdout[:160])
        conn = db(root)
        st = conn.execute("SELECT status FROM agents WHERE session_id='codex-A'").fetchone()
        conn.close()
        c.check("agent marked done", st and st["status"] == "done",
                st["status"] if st else "no row")


def main():
    c = Checker("cross-CLI / Codex + generic + opencode adapters")
    for t in (test_config_shape, test_merge_idempotent, test_merge_preserves_existing,
              test_cli_writes_codex_config, test_cli_refuses_without_hooks,
              test_codex_session_start, test_codex_user_prompt_inject,
              test_codex_stop_blocks_on_mention,
              test_codex_session_start_source_variants,
              test_codex_silent_when_nothing_new, test_codex_stop_done_path,
              test_generic_appends_snippet, test_generic_idempotent,
              test_generic_preserves_existing, test_generic_floor_documents_escalation,
              test_generic_flow_roundtrips,
              test_opencode_installs_plugin_and_floor,
              test_opencode_plugin_uses_documented_api,
              test_opencode_plugin_syntax_valid,
              test_opencode_inbox_peek_surfaces_without_advancing):
        t(c)
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
