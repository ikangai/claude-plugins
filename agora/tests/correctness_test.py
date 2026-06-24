#!/usr/bin/env python3
"""Phase-5 correctness & mixed-fleet.

Two real defects the gap analysis flagged, plus the mixed-fleet barrier gap:

  * **Escalation-orphan on rename / handoff.** The lead-done gate keyed @human
    escalations on the lead's *handle*, frozen at author time. A lead that renamed (or
    handed off) made its open question invisible to the gate — the team could tear down
    with the operator's answer still owed. Fix: key authorship by SESSION id (stable
    across a rename) and gate the AUTHORING session (so a handoff doesn't orphan it);
    the operator clears by @mentioning the author's CURRENT handle.
  * **Mixed-fleet `done`.** A non-hook host (opencode / generic) never marks `done`, so
    it held a Claude/Codex team at the barrier until it aged out. Fix: a `parks` flag —
    `team_done` only requires barrier-capable (hook) agents to be done.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/correctness_test.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, hook, init_room, parse_hook_json, tmp_root,
)


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def _send_id(env, frm, body):
    out = cli(["send", "--from", frm, body], env).stdout
    m = re.search(r"#(\d+)", out)
    return m.group(1) if m else None


def _stop(env, sid):
    return hook("stop.py", env,
                {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": False})


def _is_block(out):
    return (parse_hook_json(out.stdout) or {}).get("decision") == "block"


def _open_for(root, sid):
    chat = _import_chat()
    os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    try:
        conn = chat.connect()
        try:
            return chat.session_open_escalations(conn, sid)
        finally:
            conn.close()
    finally:
        os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# escalation survives a rename
# --------------------------------------------------------------------------- #
def test_escalation_survives_rename(c):
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_ada", "--from", "ada"], env)  # sole -> lead
        mid = _send_id(env, "ada", "@human need a decision on the schema")
        cli(["rename", "--from", "ada", "chief"], env)

        c.check("a renamed lead's open escalation is still tracked (by session)",
                bool(_open_for(root, "s_ada")), "expected non-empty")
        out = _stop(env, "s_ada")
        c.check("a renamed lead is still parked on its open escalation",
                _is_block(out), out.stdout)

        q = cli(["questions"], env).stdout
        c.check("the operator still sees the escalation after a rename",
                "decision on the schema" in q, q)
        # answer must reach the author's CURRENT handle (@chief), clearing it.
        cli(["answer", mid, "go with option A"], env)
        c.check("answering a renamed lead clears its escalation",
                not _open_for(root, "s_ada"), "expected empty after answer")


# --------------------------------------------------------------------------- #
# escalation survives a lead handoff
# --------------------------------------------------------------------------- #
def test_escalation_survives_handoff(c):
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_ada", "--from", "ada"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        cli(["lead", "--claim", "--from", "ada"], env)  # ada is the lead
        mid = _send_id(env, "ada", "@human which database should we use")
        cli(["lead", "bob"], env)  # hand the lead to bob

        c.check("the asking agent stays gated after handing off the lead",
                bool(_open_for(root, "s_ada")), "expected non-empty")
        out = _stop(env, "s_ada")
        c.check("the former lead is still parked on its unanswered question",
                _is_block(out), out.stdout)
        q = cli(["questions"], env).stdout
        c.check("the operator sees the orphaned question room-wide",
                "which database" in q, q)
        cli(["answer", mid, "postgres"], env)  # -> @ada (author's current handle)
        c.check("answering clears the former lead's escalation",
                not _open_for(root, "s_ada"), "expected empty after answer")


def test_normal_escalation_still_works(c):
    # The happy path (no rename/handoff) must be unchanged.
    with tmp_root() as root:
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_PARK_WINDOW"] = "0"
        cli(["register", "--session", "s_ada", "--from", "ada"], env)
        mid = _send_id(env, "ada", "@human approve the release?")
        c.check("a lead parks on its open escalation",
                _is_block(_stop(env, "s_ada")), "expected a block")
        cli(["answer", mid, "approved"], env)
        c.check("the answered escalation is cleared", not _open_for(root, "s_ada"))


# --------------------------------------------------------------------------- #
# mixed-fleet done
# --------------------------------------------------------------------------- #
def test_nonhook_agent_does_not_hold_the_barrier(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        # ada is a hook (barrier-capable) agent; oc is an opencode-style agent with no
        # Stop hook (registered --no-barrier), so it never marks done.
        cli(["register", "--session", "s_ada", "--from", "ada"], env)
        cli(["register", "--session", "s_oc", "--from", "oc", "--no-barrier"], env)
        cli(["expect", "2"], env)  # team assembled
        cli(["done", "--from", "ada"], env)  # the only hook agent finishes
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            td = chat.team_done(conn)
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)
        c.check("a non-hook agent does NOT hold the barrier "
                "(team is done once the hook agents are)", td is True, str(td))


def test_no_barrier_flag_recorded(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada", "--no-barrier"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        conn = db(root)
        rows = {r["handle"]: r["parks"] for r in
                conn.execute("SELECT handle, parks FROM agents").fetchall()}
        conn.close()
        c.check("--no-barrier records parks=0", rows.get("ada") == 0, str(rows))
        c.check("a normal (hook) agent defaults to parks=1", rows.get("bob") == 1, str(rows))


def test_hook_team_barrier_unaffected(c):
    # An all-hook team behaves exactly as before (no parks=0 agents).
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["expect", "2"], env)
        cli(["done", "--from", "ada"], env)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            td_partial = chat.team_done(conn)  # bob not done yet
            conn.close()
            cli(["done", "--from", "bob"], env)
            conn = chat.connect()
            td_all = chat.team_done(conn)
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)
        c.check("an all-hook team is NOT done while a hook agent is unfinished",
                td_partial is False, str(td_partial))
        c.check("an all-hook team is done when every hook agent is done",
                td_all is True, str(td_all))


def test_corrupt_mentions_does_not_disable_gate(c):
    # A corrupt 'mentions' on a human-sender row must NOT make the escalation gate raise
    # inside the Stop hook (fail-open would then silently drop a real open escalation).
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_ada", "--from", "ada"], env)
        cli(["send", "--from", "ada", "@human a real question"], env)
        conn = db(root)
        conn.execute("INSERT INTO messages(ts,sender,kind,body,mentions) VALUES (?,?,?,?,?)",
                     ("2026-06-23T12:00:00Z", "human", "chat", "@ada noise", "NOT-JSON{"))
        conn.commit(); conn.close()
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            try:
                ids = chat.session_open_escalations(conn, "s_ada"); raised = False
            except Exception:
                ids = None; raised = True
            conn.close()
            c.check("a corrupt mentions row does not crash the escalation gate", not raised)
            c.check("...and the real open escalation is still detected", bool(ids), str(ids))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_null_session_escalation_does_not_gate_recycled_handle(c):
    # A session-less @human authored under a handle must NOT gate a DIFFERENT session
    # that later recycles that handle (the handle-vs-session conflation Phase 5 kills).
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        conn = db(root)
        conn.execute("INSERT INTO messages(ts,sender,session_id,kind,body,mentions) "
                     "VALUES (?,?,?,?,?,?)",
                     ("2026-06-23T12:00:00Z", "ada", None, "chat", "@human orphan", '["human"]'))
        conn.commit(); conn.close()
        cli(["register", "--session", "s_new", "--from", "ada"], env)  # recycles 'ada'
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            ids = chat.session_open_escalations(conn, "s_new")
            conn.close()
            c.check("a session-less escalation does not gate a recycled handle",
                    ids == [], str(ids))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_reregister_no_barrier_downgrades_parks(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)               # parks=1
        cli(["register", "--session", "s1", "--from", "ada", "--no-barrier"], env)  # downgrade
        conn = db(root)
        p = conn.execute("SELECT parks FROM agents WHERE session_id='s1'").fetchone()[0]
        conn.close()
        c.check("re-registering --no-barrier downgrades parks to 0", p == 0, str(p))
        cli(["register", "--session", "s1", "--from", "ada"], env)  # a plain refresh
        conn = db(root)
        p2 = conn.execute("SELECT parks FROM agents WHERE session_id='s1'").fetchone()[0]
        conn.close()
        c.check("a plain refresh does NOT re-upgrade parks", p2 == 0, str(p2))


def test_dashboard_escalation_survives_rename(c):
    chat = _import_chat()
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import dashboard  # noqa: E402
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_ada", "--from", "ada"], env)
        cli(["send", "--from", "ada", "@human decide the schema?"], env)
        cli(["rename", "--from", "ada", "chief"], env)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            items = dashboard._collect_escalations(conn, "chief")
            conn.close()
            c.check("the dashboard still shows the escalation after a rename",
                    any("decide the schema" in i["body"] for i in items), str(items))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def main():
    c = Checker("Phase-5 correctness & mixed-fleet (escalation rename/handoff / barrier)")
    for name, fn in (
        ("escalation_survives_rename", test_escalation_survives_rename),
        ("escalation_survives_handoff", test_escalation_survives_handoff),
        ("normal_escalation_still_works", test_normal_escalation_still_works),
        ("nonhook_agent_does_not_hold_the_barrier",
         test_nonhook_agent_does_not_hold_the_barrier),
        ("no_barrier_flag_recorded", test_no_barrier_flag_recorded),
        ("hook_team_barrier_unaffected", test_hook_team_barrier_unaffected),
        ("corrupt_mentions_does_not_disable_gate",
         test_corrupt_mentions_does_not_disable_gate),
        ("null_session_escalation_does_not_gate_recycled_handle",
         test_null_session_escalation_does_not_gate_recycled_handle),
        ("reregister_no_barrier_downgrades_parks",
         test_reregister_no_barrier_downgrades_parks),
        ("dashboard_escalation_survives_rename", test_dashboard_escalation_survives_rename),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
