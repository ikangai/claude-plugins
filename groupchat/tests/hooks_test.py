#!/usr/bin/env python3
"""Hook integration tests — the seamless-integration layer (the project's one
hard boundary: it must drop into Claude Code / Codex / any host that speaks the
same hook I/O contract). Each hook is driven exactly as the host does: JSON on
stdin, JSON (or nothing) on stdout, and — sacred invariant — **always exit 0**.

A non-zero exit from ``user_prompt_submit`` would block the user's prompt; from
``stop`` it would wedge the session. So the fail-open tests come first.

Some stop-hook barrier cases are RED until bug #21 (duplicate ``_env_int`` →
``team_done`` raises → stop fails open) is fixed; they are labelled and flip
green automatically once the fix lands. Run:

    python3 tests/hooks_test.py
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, HOOKS, cli, env_for, hook, parse_hook_json, tmp_root  # noqa: E402


# --------------------------------------------------------------------------- #
# Fail-open — the non-negotiable invariant for all three hooks.
# --------------------------------------------------------------------------- #
def test_fail_open(c):
    bad_inputs = {
        "empty stdin": "",
        "malformed JSON": "{not json",
        "missing session_id": json.dumps({"hook_event_name": "X"}),
        "valid but unknown session": json.dumps({"session_id": "ghost"}),
    }
    for hookname in ("session_start.py", "user_prompt_submit.py", "stop.py"):
        with tmp_root() as root:
            env = env_for(root)
            cli(["init"], env)
            for label, payload in bad_inputs.items():
                r = hook(hookname, env, payload, timeout=20)
                c.check(f"{hookname} exits 0 on {label}", r.returncode == 0,
                        f"rc={r.returncode} stderr={r.stderr[:200]}")
                # A hook must never emit a stray decision:block on junk input.
                if hookname != "stop.py":
                    c.check(f"{hookname} emits no decision on {label}",
                            '"decision"' not in r.stdout, r.stdout[:200])


# --------------------------------------------------------------------------- #
# SessionStart — register + briefing.
# --------------------------------------------------------------------------- #
def test_session_start(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "other", "--from", "ada"], env)
        r = hook("session_start.py", env,
                 {"session_id": "s1", "cwd": root, "hook_event_name": "SessionStart"})
        c.check("session_start exits 0", r.returncode == 0, r.stderr)
        obj = parse_hook_json(r.stdout)
        c.check("emits hookSpecificOutput", bool(obj and obj.get("hookSpecificOutput")),
                r.stdout[:200])
        ctx = (obj or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
        c.check("briefing names this agent's handle", "you are **" in ctx, ctx[:200])
        c.check("briefing lists the active teammate", "ada" in ctx, ctx[:200])
        c.check("briefing tells the agent how to post", "send --from" in ctx, ctx[:200])
        # The new agent must now be registered & visible.
        w = cli(["who", "--all"], env)
        c.check("session_start registered the agent",
                "s1" not in w.stdout and any(  # handle, not raw session id
                    line for line in w.stdout.splitlines()), w.stdout)


def test_session_start_vote_hint(c):
    """When a constitution exists, the briefing must spell out the registered-
    session voting one-liner — otherwise an agent that only knows its handle
    can't use the parliament (the DX gap behind #19)."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["constitution", "init"], env)  # creates CONSTITUTION.md at repo root
        r = hook("session_start.py", env,
                 {"session_id": "s1", "cwd": root, "hook_event_name": "SessionStart"})
        ctx = (parse_hook_json(r.stdout) or {}).get(
            "hookSpecificOutput", {}).get("additionalContext", "")
        c.check("briefing surfaces the constitution", "CONSTITUTION.md" in ctx, ctx[-300:])
        # Host-neutral: the briefing embeds THIS agent's real session id, not a
        # Claude-only env var, so the vote line works verbatim on any host.
        c.check("briefing embeds a ready-to-run vote command with the real session id",
                'vote --session "s1"' in ctx, ctx[-300:])
        c.check("briefing does not hard-code a Claude-only env var for voting",
                "CLAUDE_CODE_SESSION_ID" not in ctx, ctx[-300:])


def test_session_start_host_neutral(c):
    """The briefing is read by Codex/opencode agents too — it must not claim the
    fleet is Claude-only (the goal's hard boundary: seamless across CLIs)."""
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        r = hook("session_start.py", env,
                 {"session_id": "s1", "cwd": root, "hook_event_name": "SessionStart"})
        ctx = (parse_hook_json(r.stdout) or {}).get(
            "hookSpecificOutput", {}).get("additionalContext", "")
        c.check("briefing wording is host-neutral (not 'Claude Code instances')",
                "Claude Code instances" not in ctx, ctx[:200])
        c.check("briefing names multiple hosts",
                "Codex" in ctx or "opencode" in ctx, ctx[:200])
        # Hub-and-spoke hierarchy must be discoverable from the briefing.
        c.check("briefing explains @human funnels to the lead",
                "@human" in ctx and "lead" in ctx.lower(), ctx[:400])
        c.check("briefing shows the lead command",
                "lead --claim" in ctx or '" lead' in ctx, ctx[:400])


# --------------------------------------------------------------------------- #
# UserPromptSubmit — surface new messages, advance cursor, stay silent / capped.
# --------------------------------------------------------------------------- #
def test_user_prompt_submit(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["send", "--from", "bob", "hi alice, starting on auth.py"], env)

        r = hook("user_prompt_submit.py", env,
                 {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "go"})
        obj = parse_hook_json(r.stdout)
        ctx = (obj or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
        c.check("injects the new message", "starting on auth.py" in ctx, ctx[:200])
        c.check("labels it as new group-chat", "New group-chat" in ctx, ctx[:200])

        # Cursor advanced -> a second submit with nothing new is silent.
        r2 = hook("user_prompt_submit.py", env,
                  {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "again"})
        c.check("silent when nothing new", r2.stdout.strip() == "", r2.stdout[:200])


def test_user_prompt_submit_mention(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["send", "--from", "bob", "@alice can you review?"], env)
        r = hook("user_prompt_submit.py", env,
                 {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "go"})
        ctx = (parse_hook_json(r.stdout) or {}).get(
            "hookSpecificOutput", {}).get("additionalContext", "")
        c.check("mention adds a 'you were mentioned' nudge",
                "mentioned" in ctx.lower(), ctx[:200])


def test_user_prompt_submit_cap(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        for i in range(45):  # > CAP (40)
            cli(["send", "--from", "bob", f"msg-{i}"], env)
        r = hook("user_prompt_submit.py", env,
                 {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "go"})
        ctx = (parse_hook_json(r.stdout) or {}).get(
            "hookSpecificOutput", {}).get("additionalContext", "")
        c.check("large backlog is capped with an 'omitted' note",
                "omitted" in ctx, ctx[:200])
        c.check("newest message is kept", "msg-44" in ctx, ctx[-200:])
        c.check("oldest over-cap message is dropped", "msg-0\n" not in ctx and
                "msg-0]" not in ctx, "msg-0 should be omitted")


# --------------------------------------------------------------------------- #
# Stop — mention block (works today) + barrier/park (RED until #21 fixed).
# --------------------------------------------------------------------------- #
def test_stop_blocks_on_mention(c):
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=2)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["send", "--from", "bob", "@alice one question before you go"], env)
        r = hook("stop.py", env,
                 {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False})
        c.check("stop exits 0 even when blocking", r.returncode == 0, r.stderr)
        obj = parse_hook_json(r.stdout)
        c.check("unanswered mention -> decision:block",
                bool(obj and obj.get("decision") == "block"), r.stdout[:200])
        c.check("block reason carries the message",
                "one question" in (obj or {}).get("reason", ""), r.stdout[:200])
        # The agent owes a reply -> it's no longer 'done'.
        w = cli(["who", "--all"], env)
        c.check("mentioned agent is marked active, not done",
                "alice — active" in w.stdout or "alice  " in w.stdout, w.stdout)


def test_stop_parks_when_team_not_done(c):
    """RED until #21: with a teammate still working, a finished agent must PARK
    (re-park block), not be allowed to stop. The bug makes team_done() raise ->
    stop fails open -> empty output (no block)."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=2,
                      GROUPCHAT_PARK_WINDOW=0, GROUPCHAT_POLL_TICK=0.1,
                      GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)  # bob never done
        r = hook("stop.py", env,
                 {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
                 timeout=20)
        obj = parse_hook_json(r.stdout)
        c.check("[needs #21] finished agent parks (re-park block) when team unfinished",
                bool(obj and obj.get("decision") == "block"
                     and "barrier" in obj.get("reason", "")),
                f"got {r.stdout[:200]!r}")


def test_stop_allows_when_team_done(c):
    """A lone agent whose team is fully done is allowed to stop (no block)."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        r = hook("stop.py", env,
                 {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
                 timeout=20)
        c.check("solo finished team -> stop allowed (no block)",
                '"decision"' not in r.stdout, r.stdout[:200])
        c.check("stop exits 0", r.returncode == 0, r.stderr)


def test_stop_wakes_on_mention_during_park(c):
    """RED until #21: a parked agent must wake when a teammate @mentions it.
    Starts a parked stop hook, injects a mention mid-park, expects a block."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=2,
                      GROUPCHAT_PARK_WINDOW=8, GROUPCHAT_POLL_TICK=0.3,
                      GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)  # keeps team unfinished
        proc = subprocess.Popen(
            [sys.executable, os.path.join(HOOKS, "stop.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env)
        proc.stdin.write(json.dumps(
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False}))
        proc.stdin.close()
        time.sleep(1.5)  # let it enter the park loop
        cli(["send", "--from", "bob", "@alice actually, wait — one more thing"], env)
        try:
            out, _ = proc.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        obj = parse_hook_json(out)
        c.check("[needs #21] parked agent wakes & blocks on a fresh mention",
                bool(obj and obj.get("decision") == "block"
                     and "one more thing" in obj.get("reason", "")),
                f"got {out[:200]!r}")


def main():
    c = Checker("hooks (fail-open / session_start / prompt-submit / stop)")
    tests = [
        test_fail_open, test_session_start, test_session_start_vote_hint,
        test_session_start_host_neutral, test_user_prompt_submit,
        test_user_prompt_submit_mention, test_user_prompt_submit_cap,
        test_stop_blocks_on_mention, test_stop_parks_when_team_not_done,
        test_stop_allows_when_team_done, test_stop_wakes_on_mention_during_park,
    ]
    for fn in tests:
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
