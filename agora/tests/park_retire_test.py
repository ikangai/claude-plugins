#!/usr/bin/env python3
"""Park retirement (push-wake P3).

Pre-push-wake, a finished spawned worker took a BLOCKING park (a sleep-poll in
the Stop hook) because an idle session had no way to be reached for a late
@mention. Now that a worker binds a native inbox socket, it can RETIRE instead:
return, go idle, and wake on a teammate's @mention via the push nudge — no frozen
session, instant wake, no re-park turns, no 2h drop.

Two agents still take the blocking park, because the loop still earns its keep:
  * an agent awaiting the operator (the loop enforces the 2h escalation ceiling —
    an idle agent can't self-enforce it);
  * a non-socketed worker (bridge host / feature off) — blocking is its only way
    to stay reachable.

Also checks the P4 spawn hardening: a bootstrapped worker launches with
`--settings crossSessionInbound:accept` so a push is delivered, not held.

Drives the real stop.py. Run:  python3 tests/park_retire_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (Checker, GROUPCHAT, cli, env_for, hook,  # noqa: E402
                   parse_hook_json, tmp_root)

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402

SOCK = "/tmp/cc-socks/p3-test.sock"          # any path — retirement checks env presence
# A parking test waits out one park window for the re-park block, which the hook
# emits at the window deadline regardless of how many poll ticks fit — so a short
# window keeps every assertion and just stops idling a full second per parking test.
_FAST = dict(GROUPCHAT_PARK_WINDOW=0.3, GROUPCHAT_POLL_TICK=0.1)


def room(root):
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    return chat.connect()


def _stop(env, sid, timeout=20):
    return hook("stop.py", env,
                {"session_id": sid, "hook_event_name": "Stop",
                 "stop_hook_active": False}, timeout=timeout)


def _parked(result) -> bool:
    j = parse_hook_json(result.stdout)
    return bool(j and j.get("decision") == "block")


def _status(root, sid):
    conn = room(root)
    try:
        r = conn.execute("SELECT status FROM agents WHERE session_id=?", (sid,)).fetchone()
        return r["status"] if r else None
    finally:
        conn.close()


def test_socketed_spawned_worker_retires(c):
    """A spawned worker WITH an inbox socket + team not done + not awaiting →
    retires: allows the stop (goes idle), does not freeze in the park loop."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator",
                    CLAUDE_CODE_MESSAGING_SOCKET=SOCK))     # spawned + socketed
        cli(["register", "--session", "s2", "--from", "bob"], env)  # team not-done
        t0 = time.monotonic()
        r = _stop(env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=SOCK, **_FAST), "s1")
        dt = time.monotonic() - t0
        c.check("socketed spawned worker retires (no park block)",
                not _parked(r), f"stdout={r.stdout!r}")
        c.check("...and returns promptly (didn't sit in the park loop)",
                dt < 3.0, f"took {dt:.1f}s")
        c.check("...still marked done, so it gates the barrier as before",
                _status(root, "s1") == chat.DONE_STATUS)


def test_non_socketed_spawned_worker_still_parks(c):
    """No inbox socket (bridge host / feature off) → blocking park is its only way
    to stay reachable, so it must still park."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)   # env_for scrubs CLAUDE_CODE_MESSAGING_SOCKET
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator"))
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = _stop(env, "s1")
        c.check("non-socketed spawned worker still parks", _parked(r), r.stdout)


def test_push_disabled_spawned_worker_still_parks(c):
    """AGORA_PUSH=0 turns the native channel off, so even a socketed worker can't
    rely on a nudge → it must park."""
    with tmp_root() as root:
        env = env_for(root, AGORA_PUSH=0, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator",
                    CLAUDE_CODE_MESSAGING_SOCKET=SOCK))
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = _stop(env_for(root, AGORA_PUSH=0, CLAUDE_CODE_MESSAGING_SOCKET=SOCK,
                          **_FAST), "s1")
        c.check("push-off socketed worker still parks", _parked(r), r.stdout)


def test_awaiting_operator_socketed_worker_still_parks(c):
    """An agent owing the operator an @human answer keeps the BLOCKING park even
    when socketed — the loop is what enforces the 2h ceiling that stops a
    never-answered escalation from pinning the team forever."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator",
                    CLAUDE_CODE_MESSAGING_SOCKET=SOCK))     # spawned + socketed lead
        conn = room(root)
        chat.send(conn, "alice", "@human need a call on the schema", session_id="s1")
        conn.close()
        c.check("(precondition) escalation open for s1",
                len(chat.session_open_escalations(room(root), "s1")) == 1)
        r = _stop(env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=SOCK, **_FAST), "s1")
        c.check("awaiting + socketed still parks (keeps the 2h ceiling)",
                _parked(r), r.stdout)


def test_agora_park_forces_block_park_on_socketed(c):
    """AGORA_PARK=1 restores the old blocking park even for a socketed worker."""
    with tmp_root() as root:
        env = env_for(root, AGORA_PARK=1, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, GROUPCHAT_SPAWNED_BY="orchestrator",
                    CLAUDE_CODE_MESSAGING_SOCKET=SOCK))
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = _stop(env_for(root, AGORA_PARK=1, CLAUDE_CODE_MESSAGING_SOCKET=SOCK,
                          **_FAST), "s1")
        c.check("AGORA_PARK=1 forces the blocking park even when socketed",
                _parked(r), r.stdout)


def test_attended_socketed_still_returns(c):
    """An attended (human-launched) socketed session returns as always — the
    retirement path doesn't change attended behavior."""
    with tmp_root() as root:
        env = env_for(root, **_FAST)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"],
            env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=SOCK))   # attended (no spawned_by)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = _stop(env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=SOCK, **_FAST), "s1")
        c.check("attended socketed session returns (unchanged)",
                not _parked(r), r.stdout)


def test_spawn_command_hardens_inbound(c):
    """Bootstrap launches a worker with crossSessionInbound:accept (push delivered,
    not held) — gated on push being enabled."""
    on = chat._spawn_command("ada", "/tmp/repo", None)
    c.check("spawn adds --settings crossSessionInbound:accept when push is on",
            '--settings' in on and 'crossSessionInbound' in on and 'accept' in on, on)
    old = os.environ.pop("AGORA_PUSH", None)
    os.environ["AGORA_PUSH"] = "0"
    try:
        off = chat._spawn_command("ada", "/tmp/repo", None)
    finally:
        os.environ.pop("AGORA_PUSH", None)
        if old is not None:
            os.environ["AGORA_PUSH"] = old
    c.check("AGORA_PUSH=0 omits the inbound-accept setting",
            'crossSessionInbound' not in off, off)


def main():
    c = Checker("park retirement (push-wake P3) + inbound hardening (P4)")
    for t in (test_socketed_spawned_worker_retires,
              test_non_socketed_spawned_worker_still_parks,
              test_push_disabled_spawned_worker_still_parks,
              test_awaiting_operator_socketed_worker_still_parks,
              test_agora_park_forces_block_park_on_socketed,
              test_attended_socketed_still_returns,
              test_spawn_command_hardens_inbound):
        try:
            t(c)
        except Exception as e:
            c.check(f"{t.__name__} raised", False, repr(e))
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
