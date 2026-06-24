#!/usr/bin/env python3
"""Rename to Agora — env + store-dir dual-read backward-compat.

The plugin is renamed groupchat -> agora. The runtime honors the new `AGORA_*` env and
`.agora` room dir, with the legacy `GROUPCHAT_*` / `.groupchat` still read so existing
rooms and launch scripts keep working (new spelling wins on a tie).

Dependency-free; isolated. Run:  python3 tests/rename_compat_test.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, CHAT, tmp_root  # noqa: E402


def _import_chat():
    sys.path.insert(0, os.path.dirname(CHAT))
    import chat  # noqa: E402
    return chat


def _run(args, env):
    return subprocess.run([sys.executable, CHAT, *args],
                          capture_output=True, text=True, env=env)


def _base_env(**extra):
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("GROUPCHAT_") or k.startswith("AGORA_"):
            env.pop(k, None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def test_env_precedence(c):
    chat = _import_chat()
    keep = {k: os.environ.get(k) for k in ("AGORA_TEAM_SIZE", "GROUPCHAT_TEAM_SIZE")}
    try:
        os.environ.pop("AGORA_TEAM_SIZE", None); os.environ["GROUPCHAT_TEAM_SIZE"] = "5"
        c.check("legacy GROUPCHAT_* is read when AGORA_* is unset",
                chat._env("TEAM_SIZE") == "5", chat._env("TEAM_SIZE"))
        os.environ["AGORA_TEAM_SIZE"] = "9"
        c.check("AGORA_* wins over GROUPCHAT_* on a tie",
                chat._env("TEAM_SIZE") == "9", chat._env("TEAM_SIZE"))
        c.check("_env accepts either spelling for the same suffix",
                chat._env("GROUPCHAT_TEAM_SIZE") == "9", chat._env("GROUPCHAT_TEAM_SIZE"))
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_env_float_honors_agora(c):
    # _env_float is a sibling of _env_int and must route through the same seam, so the
    # AGORA_* spelling of a float knob (AMEND_SUPERMAJORITY) wins like every other.
    chat = _import_chat()
    keep = {k: os.environ.get(k) for k in ("AGORA_AMEND_SUPERMAJORITY",
                                           "GROUPCHAT_AMEND_SUPERMAJORITY")}
    try:
        os.environ.pop("GROUPCHAT_AMEND_SUPERMAJORITY", None)
        os.environ["AGORA_AMEND_SUPERMAJORITY"] = "0.8"
        c.check("_env_float honors the AGORA_* spelling",
                chat._env_float("GROUPCHAT_AMEND_SUPERMAJORITY", 0.66) == 0.8,
                chat._env_float("GROUPCHAT_AMEND_SUPERMAJORITY", 0.66))
        os.environ.pop("AGORA_AMEND_SUPERMAJORITY", None)
        os.environ["GROUPCHAT_AMEND_SUPERMAJORITY"] = "0.7"
        c.check("_env_float still honors the legacy GROUPCHAT_* spelling",
                chat._env_float("GROUPCHAT_AMEND_SUPERMAJORITY", 0.66) == 0.7,
                chat._env_float("GROUPCHAT_AMEND_SUPERMAJORITY", 0.66))
    finally:
        for k, v in keep.items():
            (os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None))


def test_room_dirname_prefers_agora_then_legacy(c):
    chat = _import_chat()
    with tmp_root() as root:
        c.check("a fresh repo defaults to the new .agora room",
                chat._room_dirname(root) == ".agora", chat._room_dirname(root))
        os.makedirs(os.path.join(root, ".groupchat"))
        open(os.path.join(root, ".groupchat", "chat.db"), "w").close()
        c.check("an existing legacy .groupchat room keeps being used",
                chat._room_dirname(root) == ".groupchat", chat._room_dirname(root))
        os.makedirs(os.path.join(root, ".agora"))
        open(os.path.join(root, ".agora", "chat.db"), "w").close()
        c.check("an existing .agora room wins over a legacy one",
                chat._room_dirname(root) == ".agora", chat._room_dirname(root))


def test_store_dir_honors_both_env_spellings(c):
    chat = _import_chat()
    keep = {k: os.environ.get(k) for k in ("AGORA_DIR", "GROUPCHAT_DIR")}
    try:
        os.environ.pop("AGORA_DIR", None); os.environ["GROUPCHAT_DIR"] = "/tmp/legacy_room"
        c.check("legacy GROUPCHAT_DIR resolves the store dir",
                chat.store_dir() == "/tmp/legacy_room", chat.store_dir())
        os.environ["AGORA_DIR"] = "/tmp/agora_room"
        c.check("AGORA_DIR wins", chat.store_dir() == "/tmp/agora_room", chat.store_dir())
    finally:
        for k, v in keep.items():
            (os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None))


def test_cli_works_under_agora_dir(c):
    with tmp_root() as root:
        agora_env = _base_env(AGORA_DIR=os.path.join(root, ".agora"))
        c.check("init under AGORA_DIR exits 0", _run(["init"], agora_env).returncode == 0)
        _run(["register", "--session", "s1", "--from", "ada"], agora_env)
        who = _run(["who"], agora_env).stdout
        c.check("a room created under AGORA_DIR is usable (who shows the agent)",
                "ada" in who, who)


def test_legacy_groupchat_dir_still_works(c):
    with tmp_root() as root:
        legacy = _base_env(GROUPCHAT_DIR=os.path.join(root, ".groupchat"))
        _run(["init"], legacy)
        _run(["register", "--session", "s1", "--from", "ada"], legacy)
        c.check("a legacy GROUPCHAT_DIR room still works",
                "ada" in _run(["who"], legacy).stdout)


def test_agora_squad_env_recorded(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = _base_env(AGORA_DIR=os.path.join(root, ".agora"))
        _run(["init"], env)
        # register reads AGORA_SQUAD (the new spelling of GROUPCHAT_SQUAD)
        env2 = dict(env); env2["AGORA_SQUAD"] = "frontend"
        _run(["register", "--session", "s1", "--from", "ada"], env2)
        os.environ["AGORA_DIR"] = os.path.join(root, ".agora")
        try:
            conn = chat.connect()
            sq = conn.execute("SELECT squad FROM agents WHERE handle='ada'").fetchone()[0]
            conn.close()
            c.check("register honors AGORA_SQUAD", sq == "frontend", str(sq))
        finally:
            os.environ.pop("AGORA_DIR", None)


def test_spawn_command_uses_agora_env(c):
    chat = _import_chat()
    cmd = chat._spawn_command("bob", "/x", None, depth=1, spawned_by="ada", squad="qa")
    c.check("spawned children get AGORA_HANDLE", "AGORA_HANDLE=bob" in cmd, cmd)
    c.check("spawned children get AGORA_SQUAD", "AGORA_SQUAD=qa" in cmd, cmd)
    c.check("spawned children get AGORA_SPAWN_DEPTH", "AGORA_SPAWN_DEPTH=1" in cmd, cmd)


def main():
    c = Checker("rename to Agora — env + store-dir dual-read backward-compat")
    for name, fn in (
        ("env_precedence", test_env_precedence),
        ("env_float_honors_agora", test_env_float_honors_agora),
        ("room_dirname_prefers_agora_then_legacy", test_room_dirname_prefers_agora_then_legacy),
        ("store_dir_honors_both_env_spellings", test_store_dir_honors_both_env_spellings),
        ("cli_works_under_agora_dir", test_cli_works_under_agora_dir),
        ("legacy_groupchat_dir_still_works", test_legacy_groupchat_dir_still_works),
        ("agora_squad_env_recorded", test_agora_squad_env_recorded),
        ("spawn_command_uses_agora_env", test_spawn_command_uses_agora_env),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
