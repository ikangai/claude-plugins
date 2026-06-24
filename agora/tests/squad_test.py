#!/usr/bin/env python3
"""Squad sharding — sub-teams with independent barriers (the scale keystone).

A 100-agent room shards into bounded SQUADS, each with its OWN team barrier, so a
finished squad tears down independently instead of waiting for the whole fleet. The
LEAD / @human funnel stays GLOBAL (one point of human contact) — only the *work
barrier* shards. NULL squad = today's single global room, byte-identical.

  * `agents.squad` (NULL default); set via `GROUPCHAT_SQUAD` at launch, the `squad`
    verb at runtime, or `bootstrap --squad`;
  * `team_done`/`startup_guard_satisfied`/`cohort_age_seconds`/`expected_team_size`
    take a `squad`; the Stop hook gates on the agent's squad;
  * `expect --squad <name> N` declares a squad's size.

The load-bearing guarantee: an UNSHARDED (no-squad) room behaves bit-for-bit as before.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/squad_test.py
"""
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


def _conn(root):
    chat = _import_chat()
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    return chat, chat.connect()


def _stop(env, sid):
    return hook("stop.py", env,
                {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": False})


def _is_block(out):
    return (parse_hook_json(out.stdout) or {}).get("decision") == "block"


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
def test_squad_recorded_on_register(c):
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_SQUAD="alpha")
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "bob"], init_room(root))  # no squad
        conn = db(root)
        sq = {r["handle"]: r["squad"] for r in conn.execute("SELECT handle, squad FROM agents")}
        conn.close()
        c.check("GROUPCHAT_SQUAD is recorded on register", sq.get("ada") == "alpha", str(sq))
        c.check("no squad env -> NULL squad (the default room)", sq.get("bob") is None, str(sq))


def test_squad_verb_sets_and_shows(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["squad", "beta", "--from", "ada"], env)
        conn = db(root)
        sq = conn.execute("SELECT squad FROM agents WHERE handle='ada'").fetchone()[0]
        conn.close()
        c.check("the `squad` verb joins a squad at runtime", sq == "beta", sq)
        c.check("`squad` shows your squad", "beta" in cli(["squad", "--from", "ada"], env).stdout)


# --------------------------------------------------------------------------- #
# the byte-identity guarantee (unsharded == today)
# --------------------------------------------------------------------------- #
def test_unsharded_room_is_byte_identical(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["expect", "2"], env)
        chat, conn = _conn(root)
        try:
            cli(["done", "--from", "ada"], env)
            c.check("unsharded: team_done is False while a teammate is unfinished",
                    chat.team_done(conn) is False)
            cli(["done", "--from", "bob"], env)
            c.check("unsharded: team_done is True when all are done",
                    chat.team_done(conn) is True)
            c.check("the default-squad scope equals the whole room when unsharded",
                    chat.team_done(conn, squad=None) is True)
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# independent per-squad barriers
# --------------------------------------------------------------------------- #
def test_two_squads_have_independent_barriers(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_a", "--from", "ada"],
            env_for(root, GROUPCHAT_SQUAD="alpha"))
        cli(["register", "--session", "s_b", "--from", "bob"],
            env_for(root, GROUPCHAT_SQUAD="alpha"))
        cli(["register", "--session", "s_c", "--from", "carol"],
            env_for(root, GROUPCHAT_SQUAD="beta"))
        cli(["expect", "--squad", "alpha", "2"], env)
        cli(["expect", "--squad", "beta", "2"], env)  # beta never assembles its 2nd
        cli(["done", "--from", "ada"], env)
        cli(["done", "--from", "bob"], env)
        chat, conn = _conn(root)
        try:
            c.check("squad alpha is done independently (both its agents finished)",
                    chat.team_done(conn, squad="alpha") is True)
            c.check("squad beta is NOT done (its work is unfinished)",
                    chat.team_done(conn, squad="beta") is False)
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


def test_stop_hook_scopes_to_the_agents_squad(c):
    # The e2e discriminator: a finished squad's agent is released by ITS squad's barrier,
    # not held by another squad's still-active agents.
    with tmp_root() as root:
        env = init_room(root); env = dict(env); env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_a", "--from", "ada"],
            {**env, "GROUPCHAT_SQUAD": "alpha"})
        cli(["register", "--session", "s_b", "--from", "bob"],
            {**env, "GROUPCHAT_SQUAD": "alpha"})
        cli(["register", "--session", "s_c", "--from", "carol"],
            {**env, "GROUPCHAT_SQUAD": "beta"})   # beta still working
        cli(["expect", "--squad", "alpha", "2"], env)
        cli(["done", "--from", "bob"], env)
        out = _stop(env, "s_a")  # ada stops; alpha is now all-done; beta's carol is active
        c.check("an agent is released when ITS squad is done, even if another squad works",
                not _is_block(out), out.stdout)


# --------------------------------------------------------------------------- #
# surfacing + dormancy + schema
# --------------------------------------------------------------------------- #
def test_who_surfaces_squads(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)  # no squad
        bare = cli(["who"], env).stdout
        c.check("who is dormant about squads when none set", "squad" not in bare.lower(), bare)
        cli(["register", "--session", "s2", "--from", "bob"],
            env_for(root, GROUPCHAT_SQUAD="alpha"))
        who = cli(["who"], env).stdout
        c.check("who surfaces a squad once set", "alpha" in who, who)


def test_doctor_knows_squad(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = cli(["doctor", "-q"], env)
        c.check("doctor does not flag the squad column as drift",
                "unexpected column" not in r.stdout.lower() and "is missing" not in r.stdout.lower(),
                r.stdout)


def test_stale_squad_size_is_reclaimed(c):
    chat = _import_chat()
    with tmp_root() as root:
        init_room(root)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            chat.set_team_size(conn, 2, "alpha")  # a now-departed squad declared 2
            conn.execute("UPDATE meta SET value=? WHERE key='team_size_at:alpha'",
                         ("2000-01-01T00:00:00Z",))
            conn.commit(); conn.close()
            os.environ["GROUPCHAT_SQUAD"] = "alpha"  # a fresh SOLO agent joins squad alpha
            conn = chat.connect()
            chat.register(conn, "s_new", handle="ada")
            stale = chat.expected_team_size(conn, "alpha")
            conn.close()
            c.check("a stale per-squad size is reclaimed for a fresh solo squad agent",
                    stale is None, str(stale))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None); os.environ.pop("GROUPCHAT_SQUAD", None)


def test_global_reclaim_not_defeated_by_other_squad(c):
    chat = _import_chat()
    with tmp_root() as root:
        init_room(root)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            chat.set_team_size(conn, 2)  # stale GLOBAL size
            conn.execute("UPDATE meta SET value=? WHERE key='team_size_at'",
                         ("2000-01-01T00:00:00Z",))
            conn.commit()
            os.environ["GROUPCHAT_SQUAD"] = "beta"
            chat.register(conn, "s_beta", handle="bob")    # an active agent in another squad
            os.environ.pop("GROUPCHAT_SQUAD", None)
            chat.register(conn, "s_solo", handle="ada")    # a fresh solo default-room agent
            stale = chat.expected_team_size(conn)
            conn.close()
            c.check("the global stale-size reclaim is not defeated by an agent in another squad",
                    stale is None, str(stale))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None); os.environ.pop("GROUPCHAT_SQUAD", None)


def test_squad_change_restamps_cohort(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        conn = db(root)
        conn.execute("UPDATE agents SET first_seen=? WHERE handle='ada'",
                     ("2000-01-01T00:00:00Z",))
        conn.commit(); conn.close()
        cli(["squad", "alpha", "--from", "ada"], env)
        conn = db(root)
        fs = conn.execute("SELECT first_seen FROM agents WHERE handle='ada'").fetchone()[0]
        conn.close()
        c.check("joining a squad re-stamps first_seen (honest new-cohort age)",
                fs > "2020", fs)


def test_junk_squad_name_is_refused(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = cli(["squad", "!!!", "--from", "ada"], env)
        c.check("a junk squad name is refused (not silently the default room)",
                r.returncode != 0, r.stdout + r.stderr)


def test_briefing_scopes_to_squad(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_b", "--from", "bob"],
            env_for(root, GROUPCHAT_SQUAD="alpha"))
        cli(["register", "--session", "s_c", "--from", "carol"],
            env_for(root, GROUPCHAT_SQUAD="beta"))
        cli(["expect", "--squad", "alpha", "2"], env)
        out = hook("session_start.py", env_for(root, GROUPCHAT_SQUAD="alpha"),
                   {"session_id": "s_dave", "cwd": root, "hook_event_name": "SessionStart"})
        ctx = (parse_hook_json(out.stdout) or {}).get(
            "hookSpecificOutput", {}).get("additionalContext", "")
        c.check("a squadded joiner's briefing names its squad", "alpha" in ctx, ctx)
        c.check("...and scopes the barrier count to the squad (2/2), not the fleet (3)",
                "2/2" in ctx, ctx)


def test_who_per_squad_breakdown(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_a", "--from", "ada"],
            env_for(root, GROUPCHAT_SQUAD="alpha"))
        cli(["register", "--session", "s_b", "--from", "bob"],
            env_for(root, GROUPCHAT_SQUAD="alpha"))
        cli(["expect", "--squad", "alpha", "2"], env)
        who = cli(["who"], env).stdout
        c.check("who shows a per-squad breakdown",
                "squad alpha:" in who and "expecting 2" in who, who)


def main():
    c = Checker("squad sharding (independent per-squad barriers; unsharded byte-identical)")
    for name, fn in (
        ("squad_recorded_on_register", test_squad_recorded_on_register),
        ("squad_verb_sets_and_shows", test_squad_verb_sets_and_shows),
        ("unsharded_room_is_byte_identical", test_unsharded_room_is_byte_identical),
        ("two_squads_have_independent_barriers", test_two_squads_have_independent_barriers),
        ("stop_hook_scopes_to_the_agents_squad", test_stop_hook_scopes_to_the_agents_squad),
        ("who_surfaces_squads", test_who_surfaces_squads),
        ("doctor_knows_squad", test_doctor_knows_squad),
        ("stale_squad_size_is_reclaimed", test_stale_squad_size_is_reclaimed),
        ("global_reclaim_not_defeated_by_other_squad",
         test_global_reclaim_not_defeated_by_other_squad),
        ("squad_change_restamps_cohort", test_squad_change_restamps_cohort),
        ("junk_squad_name_is_refused", test_junk_squad_name_is_refused),
        ("briefing_scopes_to_squad", test_briefing_scopes_to_squad),
        ("who_per_squad_breakdown", test_who_per_squad_breakdown),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
