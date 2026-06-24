#!/usr/bin/env python3
"""Per-squad leads + the council hierarchy (chair-topped).

worker --@human--> squad lead --@human--> CHAIR --@human--> operator. Each squad has an
emergent/claimable captain; the chair (global lead) is the sole operator contact. The
per-squad lead absorbs its squad's @human; the captain escalates to the chair; the chair
to the operator. Composes with the existing per-session escalation gate + per-squad
barrier — dormant (byte-identical) when unsharded.

Dependency-free; isolated. Run:  python3 tests/council_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, db, env_for, init_room, tmp_root  # noqa: E402


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


def _reg(root, env, sid, handle, squad=None):
    e = dict(env)
    if squad:
        e["AGORA_SQUAD"] = squad
    cli(["register", "--session", sid, "--from", handle], e)


# --------------------------------------------------------------------------- #
# per-squad lead resolution
# --------------------------------------------------------------------------- #
def test_resolve_lead_per_squad_floor(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_a1", "ada", squad="frontend")   # earliest in frontend
        _reg(root, env, "s_a2", "bob", squad="frontend")
        _reg(root, env, "s_b1", "carol", squad="backend")  # earliest in backend
        chat, conn = _conn(root)
        try:
            c.check("the frontend captain is its earliest-joined member",
                    chat.resolve_lead(conn, "frontend") == "ada",
                    chat.resolve_lead(conn, "frontend"))
            c.check("the backend captain is its own earliest member (not frontend's)",
                    chat.resolve_lead(conn, "backend") == "carol",
                    chat.resolve_lead(conn, "backend"))
            c.check("the chair is the global floor (earliest overall)",
                    chat.resolve_lead(conn, None) == "ada", chat.resolve_lead(conn, None))
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# routing: worker -> squad lead -> chair -> operator
# --------------------------------------------------------------------------- #
def test_routing_chain(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_chair", "ada", squad="frontend")   # chair (global floor) + frontend captain
        _reg(root, env, "s_blead", "bob", squad="backend")    # backend captain
        _reg(root, env, "s_bwork", "carol", squad="backend")  # backend worker
        chat, conn = _conn(root)
        try:
            # a backend worker's @human -> its squad lead (bob), not the chair
            c.check("a worker's @human routes to its squad lead",
                    chat.human_redirect_target(conn, "carol", "@human help") == "bob",
                    chat.human_redirect_target(conn, "carol", "@human help"))
            # the backend captain's @human -> the chair (ada)
            c.check("a squad lead's @human routes to the chair",
                    chat.human_redirect_target(conn, "bob", "@human help") == "ada",
                    chat.human_redirect_target(conn, "bob", "@human help"))
            # the chair's @human -> the operator (no redirect)
            c.check("the chair's @human passes through to the operator",
                    chat.human_redirect_target(conn, "ada", "@human help") is None,
                    str(chat.human_redirect_target(conn, "ada", "@human help")))
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# the gate: a captain parks until the chair answers (the new clear clause)
# --------------------------------------------------------------------------- #
def test_captain_gated_until_chair_answers(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_chair", "ada", squad="frontend")   # chair
        _reg(root, env, "s_blead", "bob", squad="backend")    # backend captain
        chat, conn = _conn(root)
        try:
            # bob (captain) escalates @human -> routed to the chair, but KEPT so bob is gated
            chat.send(conn, "bob", "@human is this API ok?", session_id="s_blead")
            c.check("a captain's escalation parks it (open escalation)",
                    len(chat.session_open_escalations(conn, "s_blead")) >= 1,
                    str(chat.session_open_escalations(conn, "s_blead")))
            # the CHAIR replying to the captain clears it (relaying the answer down)
            chat.send(conn, "ada", "@bob yes, ship it", session_id="s_chair")
            c.check("the chair's reply to the captain clears its escalation",
                    len(chat.session_open_escalations(conn, "s_blead")) == 0,
                    str(chat.session_open_escalations(conn, "s_blead")))
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


def test_chair_still_gated_only_by_operator(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_chair", "ada", squad="frontend")
        _reg(root, env, "s_blead", "bob", squad="backend")
        chat, conn = _conn(root)
        try:
            chat.send(conn, "ada", "@human deploy now?", session_id="s_chair")  # chair -> operator
            c.check("the chair's @human is an open operator escalation",
                    len(chat.session_open_escalations(conn, "s_chair")) >= 1)
            # a non-operator (the captain) replying does NOT clear the chair's operator escalation
            chat.send(conn, "bob", "@ada I think yes", session_id="s_blead")
            c.check("only the operator clears the chair's escalation (a captain reply doesn't)",
                    len(chat.session_open_escalations(conn, "s_chair")) >= 1)
            chat.send(conn, "human", "@ada yes deploy", session_id=None)
            c.check("the operator's reply clears the chair's escalation",
                    len(chat.session_open_escalations(conn, "s_chair")) == 0)
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# dormancy: an unsharded room behaves exactly as the flat (today) leadership
# --------------------------------------------------------------------------- #
def test_unsharded_is_byte_identical(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s1", "ada")   # no squad — the global lead
        _reg(root, env, "s2", "bob")   # no squad — a worker
        chat, conn = _conn(root)
        try:
            c.check("unsharded: a worker's @human routes to the global lead (today)",
                    chat.human_redirect_target(conn, "bob", "@human q") == "ada",
                    chat.human_redirect_target(conn, "bob", "@human q"))
            c.check("unsharded: the lead's @human passes through (today)",
                    chat.human_redirect_target(conn, "ada", "@human q") is None)
            # the new chair-reply clear clause is dormant: no captain exists, so a peer
            # reply must NOT clear the lead's operator escalation.
            chat.send(conn, "ada", "@human ship?", session_id="s1")
            chat.send(conn, "bob", "@ada maybe", session_id="s2")
            c.check("unsharded: a peer reply does NOT clear the lead's escalation",
                    len(chat.session_open_escalations(conn, "s1")) >= 1)
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# surfacing: council view + squad-scoped lead claim
# --------------------------------------------------------------------------- #
def test_council_view_and_scoped_claim(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_a", "ada", squad="frontend")
        _reg(root, env, "s_b", "bob", squad="frontend")
        _reg(root, env, "s_c", "carol", squad="backend")
        # bob claims the frontend captaincy (scoped to his squad)
        cli(["lead", "--claim", "--from", "bob"], env)
        chat, conn = _conn(root)
        try:
            c.check("a scoped lead --claim makes the caller its squad's captain",
                    chat.resolve_lead(conn, "frontend") == "bob",
                    chat.resolve_lead(conn, "frontend"))
            c.check("the other squad's captain is unaffected",
                    chat.resolve_lead(conn, "backend") == "carol")
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)
        out = cli(["council"], env).stdout
        c.check("council shows the chair", "chair" in out.lower(), out)
        c.check("council lists the squads' captains", "frontend" in out and "backend" in out, out)


def test_captain_rename_keeps_captaincy(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_a", "ada", squad="frontend")
        _reg(root, env, "s_b", "bob", squad="frontend")
        cli(["lead", "--claim", "--from", "bob"], env)        # bob is the designated captain
        cli(["rename", "--from", "bob", "frontboss"], env)     # the captain renames
        chat, conn = _conn(root)
        try:
            c.check("a captain that renames keeps its squad captaincy (pointer follows)",
                    chat.resolve_lead(conn, "frontend") == "frontboss",
                    chat.resolve_lead(conn, "frontend"))
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


def test_flat_handoff_does_not_clear_owed_operator_escalation(c):
    # BLOCKER regression: in a FLAT room (no squads), the chair-relay clear-clause must be
    # a strict no-op. After a lead handoff, the new chair @mentioning the former lead in
    # ordinary chat must NOT clear the former lead's still-owed OPERATOR escalation.
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s1", "ada")   # flat: ada is the lead
        _reg(root, env, "s2", "bob")
        chat, conn = _conn(root)
        try:
            chat.send(conn, "ada", "@human deploy now?", session_id="s1")  # owed to operator
            chat.set_lead(conn, "bob")                                     # handoff → bob is chair
            chat.send(conn, "bob", "@ada nice work on the lexer", session_id="s2")  # ordinary chat
            c.check("flat room: a new chair's ordinary @mention does NOT clear an owed "
                    "operator escalation", len(chat.session_open_escalations(conn, "s1")) >= 1,
                    str(chat.session_open_escalations(conn, "s1")))
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


def test_answered_captain_stays_cleared_after_chair_change(c):
    # BLOCKER regression: once the chair answers a captain, the captain's escalation must
    # STAY cleared even if the chair later renames / hands off / fails over — replaying
    # history must be time-invariant (not keyed on the LIVE chair).
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_a", "ada", squad="frontend")    # chair + frontend captain
        _reg(root, env, "s_a2", "amy", squad="frontend")   # so the chair can hand off
        _reg(root, env, "s_b", "bob", squad="backend")     # backend captain
        chat, conn = _conn(root)
        try:
            chat.send(conn, "bob", "@human guidance?", session_id="s_b")   # captain → chair, gated
            chat.send(conn, "ada", "@bob yes ship it", session_id="s_a")   # chair answers → cleared
            c.check("captain cleared after the chair answers",
                    len(chat.session_open_escalations(conn, "s_b")) == 0,
                    str(chat.session_open_escalations(conn, "s_b")))
            # the chair RENAMES — the answered escalation must stay cleared
            chat.rename_agent(conn, "s_a", "ada2")
            c.check("...stays cleared after the chair renames (no re-open)",
                    len(chat.session_open_escalations(conn, "s_b")) == 0,
                    str(chat.session_open_escalations(conn, "s_b")))
            # the chair HANDS OFF to amy — still cleared
            chat.set_lead(conn, "amy")
            c.check("...stays cleared after a chair hand-off (no re-open)",
                    len(chat.session_open_escalations(conn, "s_b")) == 0,
                    str(chat.session_open_escalations(conn, "s_b")))
        finally:
            conn.close(); os.environ.pop("GROUPCHAT_DIR", None)


def test_reclaim_drops_stale_captaincy_pointer(c):
    # A handle-reuser must not inherit a departed captain's captaincy: the lead:<squad>
    # pointer is dropped when its holder's row is reclaimed (mirrors the global lead).
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_a", "ada", squad="frontend")
        cli(["lead", "--claim", "--from", "ada"], env)        # lead:frontend = ada
        conn = db(root)
        conn.execute("UPDATE agents SET last_seen='2000-01-01T00:00:00Z' WHERE handle='ada'")
        conn.commit(); conn.close()
        _reg(root, env, "s_new", "ada", squad="frontend")     # a NEW session reclaims 'ada'
        conn = db(root)
        ptr = conn.execute("SELECT value FROM meta WHERE key='lead:frontend'").fetchone()
        conn.close()
        c.check("a reclaimed handle's stale captaincy pointer is dropped",
                ptr is None, str(ptr and ptr[0]))


def test_who_crowns_designated_captain(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s_a", "ada", squad="frontend")
        _reg(root, env, "s_b", "bob", squad="frontend")
        cli(["lead", "--claim", "--from", "bob"], env)        # bob designated captain
        who = cli(["who"], env).stdout
        c.check("who crowns a designated squad captain (★captain)",
                "★captain" in who, who)


def main():
    c = Checker("per-squad leads + the council hierarchy (chair-topped)")
    for name, fn in (
        ("resolve_lead_per_squad_floor", test_resolve_lead_per_squad_floor),
        ("routing_chain", test_routing_chain),
        ("captain_gated_until_chair_answers", test_captain_gated_until_chair_answers),
        ("chair_still_gated_only_by_operator", test_chair_still_gated_only_by_operator),
        ("unsharded_is_byte_identical", test_unsharded_is_byte_identical),
        ("council_view_and_scoped_claim", test_council_view_and_scoped_claim),
        ("captain_rename_keeps_captaincy", test_captain_rename_keeps_captaincy),
        ("flat_handoff_does_not_clear_owed_operator_escalation",
         test_flat_handoff_does_not_clear_owed_operator_escalation),
        ("answered_captain_stays_cleared_after_chair_change",
         test_answered_captain_stays_cleared_after_chair_change),
        ("reclaim_drops_stale_captaincy_pointer", test_reclaim_drops_stale_captaincy_pointer),
        ("who_crowns_designated_captain", test_who_crowns_designated_captain),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
