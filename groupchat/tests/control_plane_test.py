#!/usr/bin/env python3
"""Phase-3 control plane + safe autonomous spawn.

Steering a fleet was cooperative-only (you @mention and hope); a long-running
orchestrator wedged its own finished workers at the barrier; and an agent could spawn
agents with no recursion backstop. Phase 3 adds:

  * **standdown / disband** — a timestamped meta flag the Stop-hook park loop honors:
    every parked agent is released within one poll tick (auto-expires so it can't haunt
    a reused room).
  * **dismiss <handle>** — a lead/operator-gated release of ONE agent from the barrier
    (keyed by session id), so an orchestrator that stays active doesn't pin its
    finished workers to the 2h ceiling.
  * **direct <handle> "…"** — a guarded imperative redirect (an @mention that blocks
    the target's Stop) after an active-set check.
  * **@team / @all** — a broadcast token that expands to every active teammate, so a
    broadcast actually blocks everyone's Stop (a plain message doesn't).
  * **spawn-depth + lineage + fleet ceiling** — `bootstrap` refuses beyond a max spawn
    depth (the runaway-recursion backstop for autonomous spawning) or a live-fleet
    ceiling, threads `GROUPCHAT_SPAWN_DEPTH`/`GROUPCHAT_SPAWNED_BY` to each child, and
    records lineage on the agent row.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/control_plane_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, env_for, hook, init_room, parse_hook_json, tmp_root,
)


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def _stop(env, sid):
    return hook("stop.py", env,
                {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": False})


def _is_block(out):
    obj = parse_hook_json(out.stdout) or {}
    return obj.get("decision") == "block"


def _meta(root, key):
    conn = db(root)
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def _status(root, handle):
    conn = db(root)
    try:
        r = conn.execute("SELECT status FROM agents WHERE handle=?", (handle,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# standdown
# --------------------------------------------------------------------------- #
def test_standdown_releases_parked_agent(c):
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env)
        env["GROUPCHAT_TEAM_SIZE"] = "2"     # barrier needs 2 -> alice would park
        env["GROUPCHAT_PARK_WINDOW"] = "0"   # skip the sleep loop in the hook
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        # Control: with no standdown, alice (bob not done) parks -> block.
        c.check("without standdown a finished agent parks at the barrier",
                _is_block(_stop(env, "s_alice")), "expected a block")
        # Declare standdown -> alice is released (no block, allow stop).
        cli(["standdown"], env)
        c.check("standdown releases a parked agent (no block)",
                not _is_block(_stop(env, "s_alice")), "expected no block")


def test_standdown_set_and_clear(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        bare = cli(["who"], env).stdout
        c.check("who is dormant about standdown when none", "standdown" not in bare.lower(), bare)
        cli(["standdown"], env)
        c.check("standdown sets the meta flag", _meta(root, "standdown") is not None)
        c.check("who surfaces an active standdown",
                "standdown" in cli(["who"], env).stdout.lower(), cli(["who"], env).stdout)
        cli(["standdown", "--clear"], env)
        c.check("standdown --clear lifts it", _meta(root, "standdown") is None)


def test_standdown_auto_expires(c):
    # A stale standdown from a departed cohort must not release a fresh agent.
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["standdown"], env)
        conn = db(root)
        conn.execute("UPDATE meta SET value=? WHERE key='standdown'",
                     ("2000-01-01T00:00:00Z",))   # backdate far past the window
        conn.commit(); conn.close()
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            c2 = chat.connect()
            c.check("a long-stale standdown is inactive (auto-expired)",
                    chat.standdown_active(c2) is False)
            c2.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# dismiss
# --------------------------------------------------------------------------- #
def test_dismiss_releases_one_agent(c):
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env)
        env["GROUPCHAT_TEAM_SIZE"] = "2"
        env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_alice", "--from", "alice"], env)  # floor lead
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        c.check("bob parks while the team is unfinished",
                _is_block(_stop(env, "s_bob")), "expected a block")
        r = cli(["dismiss", "bob", "--from", "alice"], env)  # alice is the lead
        c.check("the lead can dismiss a worker", r.returncode == 0, r.stdout + r.stderr)
        c.check("a dismissed worker is marked done (so it doesn't wedge others)",
                _status(root, "bob") == "done", _status(root, "bob"))
        c.check("a dismissed worker is released from the barrier (no block)",
                not _is_block(_stop(env, "s_bob")), "expected no block")


def test_dismiss_is_lead_gated(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)  # floor lead
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        r = cli(["dismiss", "alice", "--from", "bob"], env)  # bob is not the lead
        c.check("a non-lead cannot dismiss", r.returncode != 0, r.stdout + r.stderr)


def test_dismiss_unknown_agent_errors(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        r = cli(["dismiss", "ghost", "--from", "alice"], env)
        c.check("dismissing a non-active agent errors", r.returncode != 0,
                r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# direct
# --------------------------------------------------------------------------- #
def test_direct_redirects_with_a_blocking_mention(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        r = cli(["direct", "bob", "switch to the API module now", "--from", "alice"], env)
        c.check("direct succeeds", r.returncode == 0, r.stdout + r.stderr)
        conn = db(root)
        m = conn.execute(
            "SELECT body, mentions FROM messages WHERE kind='chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        c.check("direct @mentions only the target",
                json.loads(m["mentions"] or "[]") == ["bob"], str(dict(m)))
        c.check("direct carries the instruction", "switch to the API module" in m["body"],
                m["body"])


def test_direct_rejects_inactive_target(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        r = cli(["direct", "ghost", "do x", "--from", "alice"], env)
        c.check("directing a non-active agent errors", r.returncode != 0,
                r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# @team / @all broadcast
# --------------------------------------------------------------------------- #
def test_team_broadcast_expands_to_all_active(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        cli(["register", "--session", "s_carol", "--from", "carol"], env)
        cli(["send", "--from", "alice", "@team standup in 5"], env)
        conn = db(root)
        m = conn.execute(
            "SELECT mentions FROM messages WHERE kind='chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        ms = set(json.loads(m["mentions"] or "[]"))
        c.check("@team expands to all other active agents", ms == {"bob", "carol"}, str(ms))
        c.check("@team does not mention the sender", "alice" not in ms, str(ms))
        c.check("the literal 'team' token is not left as a mention", "team" not in ms, str(ms))


def test_team_is_a_reserved_handle(c):
    with tmp_root() as root:
        env = init_room(root)
        r = cli(["register", "--session", "s1", "--from", "team"], env)
        # registration sanitizes/avoids reserved names -> the agent is NOT named 'team'.
        h = (r.stdout or "").strip()
        c.check("no agent can take the reserved handle 'team'", h != "team", h)


# --------------------------------------------------------------------------- #
# spawn-depth + lineage + fleet ceiling
# --------------------------------------------------------------------------- #
def test_spawn_depth_guard(c):
    with tmp_root() as root:
        env = init_room(root)
        # At/over the max depth, bootstrap refuses (runaway-recursion backstop).
        deep = dict(env); deep["GROUPCHAT_SPAWN_DEPTH"] = "2"
        r = cli(["bootstrap", "1", "--dry-run"], deep)
        c.check("bootstrap refuses beyond the max spawn depth",
                r.returncode != 0 and "depth" in (r.stdout + r.stderr).lower(),
                r.stdout + r.stderr)
        # At depth 0 (default), it proceeds.
        r2 = cli(["bootstrap", "1", "--dry-run"], env)
        c.check("bootstrap at depth 0 proceeds", r2.returncode == 0, r2.stdout + r2.stderr)


def test_fleet_ceiling(c):
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_MAX_FLEET"] = "2"
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        r = cli(["bootstrap", "1", "--dry-run"], env)  # 2 active + 1 > 2
        c.check("bootstrap refuses past the fleet ceiling",
                r.returncode != 0 and "fleet" in (r.stdout + r.stderr).lower(),
                r.stdout + r.stderr)
        r2 = cli(["bootstrap", "1", "--dry-run", "--force"], env)
        c.check("--force overrides the fleet ceiling", r2.returncode == 0,
                r2.stdout + r2.stderr)


def test_lineage_recorded_on_register(c):
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_SPAWN_DEPTH="1", GROUPCHAT_SPAWNED_BY="ada")
        cli(["init"], env)
        cli(["register", "--session", "s_child", "--from", "bob"], env)
        conn = db(root)
        r = conn.execute(
            "SELECT spawn_depth, spawned_by FROM agents WHERE handle='bob'").fetchone()
        conn.close()
        c.check("a spawned agent records its spawn depth", r["spawn_depth"] == 1,
                str(dict(r)))
        c.check("a spawned agent records who spawned it", r["spawned_by"] == "ada",
                str(dict(r)))


def test_spawn_command_threads_lineage(c):
    chat = _import_chat()
    cmd = chat._spawn_command("bob", "/x", None, depth=2, spawned_by="ada")
    c.check("the launch command threads the child spawn depth",
            "GROUPCHAT_SPAWN_DEPTH=2" in cmd, cmd)
    c.check("the launch command threads the spawner handle",
            "GROUPCHAT_SPAWNED_BY=ada" in cmd, cmd)


def test_corrupt_dismissed_meta_is_failsafe(c):
    # A corrupt 'dismissed' meta must not make released_from_barrier RAISE inside the
    # Stop hook (which would tear the barrier down early) — it reads as "nobody
    # dismissed" so the agent stays parked.
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        conn = db(root)
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('dismissed', ?)",
                     ("not-json{[",))
        conn.commit(); conn.close()
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            c2 = chat.connect()
            try:
                val = chat.released_from_barrier(c2, "s1")
                raised = False
            except Exception:
                val = None; raised = True
            c2.close()
            c.check("released_from_barrier does not raise on corrupt dismissed meta",
                    not raised, "it raised")
            c.check("a corrupt dismissed set reads as nobody-dismissed (stays parked)",
                    val is False, str(val))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_dismissal_is_one_shot_on_revival(c):
    # A dismissed agent that REVIVES (answers a teammate's @mention) must rejoin the
    # barrier — not stay 'released' forever.
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env)
        env["GROUPCHAT_TEAM_SIZE"] = "2"
        env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_alice", "--from", "alice"], env)  # lead
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        cli(["dismiss", "bob", "--from", "alice"], env)
        # A teammate pings bob -> bob's Stop surfaces it (revival) and consumes dismissal.
        cli(["send", "--from", "alice", "@bob one more thing"], env)
        out1 = _stop(env, "s_bob")
        c.check("a dismissed agent still wakes for a fresh @mention",
                _is_block(out1), out1.stdout)
        # Now bob is active again; stopping with the team unfinished must PARK again,
        # not exit on the stale dismissal.
        out2 = _stop(env, "s_bob")
        c.check("a revived (un-dismissed) agent rejoins the barrier",
                _is_block(out2), out2.stdout)


def test_standdown_is_lead_gated(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)  # lead
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        r = cli(["standdown", "--from", "bob"], env)  # a worker
        c.check("a worker cannot call a standdown", r.returncode != 0, r.stdout + r.stderr)
        r2 = cli(["standdown", "--from", "alice"], env)  # the lead
        c.check("the lead can call a standdown", r2.returncode == 0, r2.stdout + r2.stderr)


def test_operator_can_dismiss_and_standdown_bare(c):
    # A bare CLI invocation (no --from) is the operator at the terminal — allowed.
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        c.check("the operator can dismiss bare (no --from)",
                cli(["dismiss", "bob"], env).returncode == 0)
        c.check("the operator can standdown bare (no --from)",
                cli(["standdown"], env).returncode == 0)


def test_negative_spawn_depth_is_clamped(c):
    # A negative GROUPCHAT_SPAWN_DEPTH must not defeat the backstop nor self-propagate.
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_SPAWN_DEPTH="-5")
        cli(["init"], env)
        r = cli(["bootstrap", "1", "--dry-run", "--method", "print"], env)
        c.check("a negative spawn depth is clamped (child depth = 0+1)",
                "GROUPCHAT_SPAWN_DEPTH=1" in r.stdout, r.stdout)


def test_standdown_releases_an_escalating_lead(c):
    # standdown is the explicit teardown — it overrides even a lead awaiting the operator.
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_alice", "--from", "alice"], env)  # sole -> lead
        cli(["send", "--from", "alice", "@human need a decision"], env)     # open escalation
        c.check("the lead parks on an open escalation (control)",
                _is_block(_stop(env, "s_alice")), "expected a block")
        cli(["standdown"], env)
        c.check("standdown releases even an escalating lead",
                not _is_block(_stop(env, "s_alice")), "expected no block")


def main():
    c = Checker("Phase-3 control plane (standdown / dismiss / direct / @team / spawn-guard)")
    for name, fn in (
        ("standdown_releases_parked_agent", test_standdown_releases_parked_agent),
        ("standdown_set_and_clear", test_standdown_set_and_clear),
        ("standdown_auto_expires", test_standdown_auto_expires),
        ("dismiss_releases_one_agent", test_dismiss_releases_one_agent),
        ("dismiss_is_lead_gated", test_dismiss_is_lead_gated),
        ("dismiss_unknown_agent_errors", test_dismiss_unknown_agent_errors),
        ("direct_redirects_with_a_blocking_mention",
         test_direct_redirects_with_a_blocking_mention),
        ("direct_rejects_inactive_target", test_direct_rejects_inactive_target),
        ("team_broadcast_expands_to_all_active", test_team_broadcast_expands_to_all_active),
        ("team_is_a_reserved_handle", test_team_is_a_reserved_handle),
        ("spawn_depth_guard", test_spawn_depth_guard),
        ("fleet_ceiling", test_fleet_ceiling),
        ("lineage_recorded_on_register", test_lineage_recorded_on_register),
        ("spawn_command_threads_lineage", test_spawn_command_threads_lineage),
        ("corrupt_dismissed_meta_is_failsafe", test_corrupt_dismissed_meta_is_failsafe),
        ("dismissal_is_one_shot_on_revival", test_dismissal_is_one_shot_on_revival),
        ("standdown_is_lead_gated", test_standdown_is_lead_gated),
        ("operator_can_dismiss_and_standdown_bare",
         test_operator_can_dismiss_and_standdown_bare),
        ("negative_spawn_depth_is_clamped", test_negative_spawn_depth_is_clamped),
        ("standdown_releases_an_escalating_lead",
         test_standdown_releases_an_escalating_lead),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
