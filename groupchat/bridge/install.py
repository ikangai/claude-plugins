#!/usr/bin/env python3
"""groupchat bridge — wire the shared bus into CLI agents other than Claude Code.

The bus itself (``chat.py``) is host-neutral; what differs per host is the
*seamless auto-injection*, delivered by that host's lifecycle hooks. This script
generates each host's wiring file. It never touches ``chat.py`` or the hook
scripts — only the host config — so it composes safely with the rest of the
system (and with a repo that is being edited in one shared working tree).

Usage:
    python3 bridge/install.py codex   [target]   # write/merge .codex/hooks.json
    python3 bridge/install.py claude  [target]   # delegate to `chat.py install`
    python3 bridge/install.py opencode [target]  # (iteration 2)
    python3 bridge/install.py generic [target]   # (iteration 3)

``target`` defaults to the current directory. The Codex path is idempotent, like
``chat.py install``: re-running adds nothing.

Why Codex is near-free: its command-hook contract is byte-identical to Claude
Code's — same stdin fields, same ``hookSpecificOutput.additionalContext`` /
``{"decision":"block"}`` stdout, same ``SessionStart`` / ``UserPromptSubmit`` /
``Stop`` event names. So the *existing* ``.groupchat/hooks/*.py`` work unchanged;
we only point Codex's events at them.
"""
from __future__ import annotations

import copy
import json
import os
import sys

# event name -> the existing hook script that implements its universal job.
HOOK_FILES = {
    "SessionStart": "session_start.py",
    "UserPromptSubmit": "user_prompt_submit.py",
    "Stop": "stop.py",
}

# Per-event extras, mirroring chat.py's HOOK_OPTIONS. SessionStart fires on every
# source Codex emits (a fresh/compacted context all benefit from the briefing).
# Stop parks at the team barrier in a sleep loop, so it needs a long timeout — it
# returns on its own well before this — plus a status line while it blocks.
_SESSION_SOURCES = "startup|resume|clear|compact"
HOOK_OPTIONS = {
    "SessionStart": {"matcher": _SESSION_SOURCES, "timeout": 15},
    "UserPromptSubmit": {"timeout": 15},
    "Stop": {"timeout": 600,
             "statusMessage": "⏳ waiting for teammates at the group-chat barrier…"},
}


def _command_for(hooks_dir: str, fname: str) -> str:
    """The shell command Codex runs for a hook — absolute, quoted for spaces."""
    return f'python3 "{os.path.join(hooks_dir, fname)}"'


def _group_for(event: str, hooks_dir: str) -> dict:
    """One Codex hook *group*: ``{[matcher,] hooks:[{type,command,...}]}``.

    The matcher (SessionStart only) lives on the group; per-command options
    (timeout / statusMessage) live on the command entry — matching Codex's schema.
    """
    opts = dict(HOOK_OPTIONS.get(event, {}))
    matcher = opts.pop("matcher", None)
    entry = {"type": "command", "command": _command_for(hooks_dir, HOOK_FILES[event])}
    entry.update(opts)
    group: dict = {}
    if matcher is not None:
        group["matcher"] = matcher
    group["hooks"] = [entry]
    return group


def codex_hooks_config(hooks_dir: str) -> dict:
    """A fresh ``.codex/hooks.json`` dict wiring all three events at ``hooks_dir``."""
    return {"hooks": {event: [_group_for(event, hooks_dir)] for event in HOOK_FILES}}


def merge_codex_hooks(existing: dict, hooks_dir: str) -> tuple[dict, int]:
    """Idempotently add our three hook commands to an existing Codex config.

    Matches on the exact command string (like ``chat.py``'s ``_merge_settings``),
    so re-running never duplicates and a user's own hooks are left untouched.
    Returns ``(merged_dict, number_added)``.
    """
    merged = copy.deepcopy(existing) if existing else {}
    hooks = merged.setdefault("hooks", {})
    added = 0
    for event in HOOK_FILES:
        groups = hooks.setdefault(event, [])
        command = _command_for(hooks_dir, HOOK_FILES[event])
        already = any(h.get("command") == command
                      for g in groups for h in g.get("hooks", []))
        if not already:
            groups.append(_group_for(event, hooks_dir))
            added += 1
    return merged, added


# --------------------------------------------------------------------------- #
# Host installers
# --------------------------------------------------------------------------- #
def _hooks_dir(target: str) -> str:
    return os.path.join(os.path.abspath(target), ".groupchat", "hooks")


def install_codex(target: str) -> int:
    target = os.path.abspath(target)
    hooks_dir = _hooks_dir(target)
    missing = [f for f in HOOK_FILES.values()
               if not os.path.isfile(os.path.join(hooks_dir, f))]
    if missing:
        print(
            f"error: hook scripts not found under {hooks_dir}\n"
            f"  missing: {', '.join(missing)}\n"
            "  Install the bus first (`python3 .groupchat/chat.py install "
            f"{target}` or the groupchat plugin), then re-run.",
            file=sys.stderr,
        )
        return 1

    cfg_path = os.path.join(target, ".codex", "hooks.json")
    existing: dict = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as fh:
                existing = json.load(fh)
        except Exception:
            print(f"error: {cfg_path} is not valid JSON; refusing to overwrite",
                  file=sys.stderr)
            return 1

    merged, added = merge_codex_hooks(existing, hooks_dir)
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w") as fh:
        json.dump(merged, fh, indent=2)
        fh.write("\n")
    print(f"{'added' if added else 'no new'} hook(s) in {cfg_path}"
          + (f" (+{added})" if added else ""))
    print("Done. Open Codex in this repo and the group chat is live for every "
          "Codex session — same bus, same handles as Claude Code.")
    return 0


def install_claude(target: str) -> int:
    """Delegate to the canonical Claude installer in chat.py (single source)."""
    chat_py = os.path.join(os.path.abspath(target), ".groupchat", "chat.py")
    if not os.path.isfile(chat_py):
        # Fall back to this dev repo's chat.py so `install.py claude <new repo>` works.
        chat_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               ".groupchat", "chat.py")
    if not os.path.isfile(chat_py):
        print("error: could not locate chat.py to delegate the Claude install",
              file=sys.stderr)
        return 1
    import subprocess
    return subprocess.run([sys.executable, chat_py, "install", target]).returncode


def install_opencode(target: str) -> int:
    """Copy the experimental plugin + install the AGENTS.md floor. The plugin adds
    auto-register + a mention nudge; the floor (which opencode reads natively) is
    what actually delivers messages until opencode ships a pre-message hook."""
    target = os.path.abspath(target)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencode",
                       "groupchat.js")
    if not os.path.isfile(src):
        print(f"error: plugin source missing at {src}", file=sys.stderr)
        return 1
    import shutil
    dst_dir = os.path.join(target, ".opencode", "plugins")
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dst_dir, "groupchat.js"))
    print(f"copied opencode plugin -> {os.path.join(dst_dir, 'groupchat.js')}")
    rc = _append_generic_snippet(target)  # the floor opencode reads via AGENTS.md
    print("Done. opencode auto-registers + nudges on @mention; the AGENTS.md block "
          "delivers messages each turn. (Seamless inject lands when opencode adds a "
          "pre-message hook — opencode #5409.)")
    return rc


# The universal floor: instructions any agent that reads AGENTS.md (Codex, opencode,
# aider, gemini-cli, cursor-agent, plain shell …) can follow to join the bus with no
# host hooks at all. Marker-delimited so re-installing is a clean no-op and the
# human's own notes above/below are never disturbed.
GENERIC_BEGIN = "<!-- GROUPCHAT:BEGIN"
GENERIC_SNIPPET = """\
<!-- GROUPCHAT:BEGIN (managed by bridge/install.py — your own notes can live above/below) -->
## Group chat — shared coordination with other agents

Other AI coding agents may be working this same repo in parallel. You share a
SQLite message bus with them. Coordinate through it: announce files before you
edit them, ask before overlapping work, and answer when a teammate @mentions you.

**Once per session**, pick a stable id and register (prints your handle). If your
host injected GROUPCHAT_SESSION (e.g. the opencode plugin), reuse it so you don't
register twice:

    GC="${GROUPCHAT_SESSION:-myname-$$}"   # honor an injected id, else any stable string
    # --no-barrier: this host has no Stop hook, so you never auto-mark done — the flag
    # keeps you from holding a hook (Claude/Codex) team at the team barrier.
    python3 .groupchat/chat.py register --session "$GC" --cwd "$PWD" --no-barrier

**Before each task**, pull what's new (this advances your read cursor, so you
never see a message twice):

    python3 .groupchat/chat.py read --session "$GC"

**To talk** (put @handle in the text to ping a specific agent):

    python3 .groupchat/chat.py send --session "$GC" "starting on src/foo.py"

**To reach the human operator**, mention `@human` — it funnels to the team's
**lead** (one point of contact, so the human isn't pinged by every agent). See who
holds it with `python3 .groupchat/chat.py lead`.

See the roster any time with `python3 .groupchat/chat.py who`. If your CLI exposes
a per-prompt or pre-tool shell hook, wire the `read` call into it for hands-free
delivery; otherwise just run it yourself at the top of each task.
<!-- GROUPCHAT:END -->"""


def _append_generic_snippet(target: str) -> int:
    """Idempotently add the group-chat instruction block to ``<target>/AGENTS.md``,
    preserving the human's own content. Shared by the generic and opencode hosts."""
    target = os.path.abspath(target)
    if not os.path.isfile(os.path.join(target, ".groupchat", "chat.py")):
        print(f"warning: {os.path.join(target, '.groupchat', 'chat.py')} not found — "
              "install the bus there too (`chat.py install` or the plugin) so the "
              "instructions resolve at runtime.", file=sys.stderr)
    agents_md = os.path.join(target, "AGENTS.md")
    existing = ""
    if os.path.exists(agents_md):
        with open(agents_md) as fh:
            existing = fh.read()
    if GENERIC_BEGIN in existing:
        print(f"no new content — AGENTS.md already has the group-chat block ({agents_md})")
        return 0
    sep = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
    with open(agents_md, "w") as fh:
        fh.write(existing + sep + GENERIC_SNIPPET + "\n")
    print(f"{'appended the' if existing else 'wrote a'} group-chat block to {agents_md}")
    print("Agents that read AGENTS.md will now register and pull messages each turn.")
    return 0


def install_generic(target: str) -> int:
    return _append_generic_snippet(target)


_HOSTS = {
    "codex": install_codex,
    "claude": install_claude,
    "opencode": install_opencode,
    "generic": install_generic,
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 1
    host = argv[0]
    target = argv[1] if len(argv) > 1 else "."
    if host == "all":
        rc = 0
        for fn in (install_claude, install_codex):  # only the shipped hosts
            rc = fn(target) or rc
        return rc
    fn = _HOSTS.get(host)
    if not fn:
        print(f"unknown host {host!r}; choose one of: {', '.join(_HOSTS)}, all",
              file=sys.stderr)
        return 1
    return fn(target)


if __name__ == "__main__":
    sys.exit(main())
