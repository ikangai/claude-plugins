#!/usr/bin/env python3
"""Parliamentary sessions / agendas / decisions — the governance *framing* layer.

The advisory parliament (motion/vote/ratify) gains the connective tissue of a
deliberative body, WITHOUT changing what binds:

  * a **session** is a bounded deliberation window (a `kind='session'` bookend + a
    meta pointer that auto-expires like `standdown`), so work is framed and a late
    joiner inherits it;
  * an **agenda** is the session's open items — reusing the `motions` table, with a
    new `op='decide'` for non-constitutional questions (votable, but with NO
    CONSTITUTION.md target);
  * a **decision** is a `kind='decision'` RECORD of the room's outcome — advisory,
    queryable, inherited by the next cohort. It binds NOTHING.

The load-bearing safety guarantee, enforced in CODE: a decision can never reach the
law. `ratify` refuses an `op='decide'` motion; only `ratify --confirm` + a human
commit changes the constitution.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/sessions_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, hook, init_room, parse_hook_json, tmp_root,
)


def _msgs(root, kind):
    conn = db(root)
    try:
        return conn.execute("SELECT id, sender, body FROM messages WHERE kind=? ORDER BY id",
                            (kind,)).fetchall()
    finally:
        conn.close()


def _meta(root, key):
    conn = db(root)
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def _mid(out):
    import re
    m = re.search(r"M(\d+)", out)
    return m.group(1) if m else None


def _briefing(env, sid, cwd):
    out = hook("session_start.py", env,
               {"session_id": sid, "cwd": cwd, "hook_event_name": "SessionStart"})
    return (parse_hook_json(out.stdout) or {}).get("hookSpecificOutput", {}).get(
        "additionalContext", "")


# --------------------------------------------------------------------------- #
# session lifecycle
# --------------------------------------------------------------------------- #
def test_session_open_close_show(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = cli(["session", "open", "rework the auth module", "--from", "ada"], env)
        c.check("session open succeeds", r.returncode == 0, r.stdout + r.stderr)
        c.check("an open session stamps the meta pointer",
                _meta(root, "parl_session") is not None)
        c.check("opening posts a kind='session' bookend", len(_msgs(root, "session")) == 1)
        show = cli(["session"], env).stdout
        c.check("session show names the open session", "rework the auth module" in show, show)
        cli(["session", "close", "--from", "ada"], env)
        c.check("close clears the pointer", _meta(root, "parl_session") is None)
        c.check("closing posts a second session bookend", len(_msgs(root, "session")) == 2)


def test_session_open_rejects_when_already_open(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "first", "--from", "ada"], env)
        r = cli(["session", "open", "second", "--from", "ada"], env)
        c.check("a second concurrent session is refused", r.returncode != 0, r.stdout + r.stderr)


def test_session_auto_expires(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "stale one", "--from", "ada"], env)
        conn = db(root)
        conn.execute("UPDATE meta SET value=? WHERE key='parl_session_at'",
                     ("2000-01-01T00:00:00Z",))
        conn.commit(); conn.close()
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            c.check("a long-stale session is inactive (auto-expired)",
                    chat.parl_session(conn) is None)
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# agenda (decide items) + voting
# --------------------------------------------------------------------------- #
def test_decide_and_agenda(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "tooling", "--from", "ada"], env)
        r = cli(["decide", "which test runner: pytest or stdlib?",
                 "--because", "msg #2, perf concern", "--from", "ada"], env)
        c.check("decide adds an agenda item", r.returncode == 0, r.stdout + r.stderr)
        ag = cli(["agenda"], env).stdout
        c.check("agenda lists the open decision item", "which test runner" in ag, ag)
        conn = db(root)
        op = conn.execute("SELECT op FROM motions ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.close()
        c.check("a decision item is op='decide' (no constitution target)", op == "decide", op)


def test_vote_on_decision_item(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["session", "open", "tooling", "--from", "ada"], env)
        mid = _mid(cli(["decide", "adopt ruff?", "--because", "#3", "--from", "ada"], env).stdout)
        cli(["vote", "--session", "s1", "M" + mid, "yea"], env)
        cli(["vote", "--session", "s2", "M" + mid, "yea"], env)
        # Decision items carry an advisory tally like any motion, shown in the AGENDA
        # (not `amendments` — that's the constitutional-only view).
        ag = cli(["agenda"], env).stdout
        c.check("a decision item carries an advisory tally (shown in the agenda)",
                "yea 2" in ag, ag)


# --------------------------------------------------------------------------- #
# decisions (records) + the safety guarantee
# --------------------------------------------------------------------------- #
def test_record_decision(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)  # floor lead
        cli(["session", "open", "tooling", "--from", "ada"], env)
        mid = _mid(cli(["decide", "adopt ruff?", "--because", "#3", "--from", "ada"], env).stdout)
        r = cli(["decision", "M" + mid, "yes — adopt ruff repo-wide", "--from", "ada"], env)
        c.check("the lead can record a decision", r.returncode == 0, r.stdout + r.stderr)
        dec = _msgs(root, "decision")
        c.check("a decision is recorded as kind='decision'", len(dec) == 1, str(dec))
        c.check("the decision references the outcome",
                dec and "adopt ruff repo-wide" in dec[0]["body"], str(dec))
        conn = db(root)
        st = conn.execute("SELECT status FROM motions WHERE id=?", (mid,)).fetchone()[0]
        conn.close()
        c.check("recording a decision marks the item 'decided'", st == "decided", st)
        out = cli(["decisions"], env).stdout
        c.check("decisions lists the recorded outcome", "adopt ruff repo-wide" in out, out)


def test_decision_is_lead_gated(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)   # floor lead
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["session", "open", "x", "--from", "ada"], env)
        mid = _mid(cli(["decide", "q?", "--because", "#1", "--from", "ada"], env).stdout)
        r = cli(["decision", "M" + mid, "outcome", "--from", "bob"], env)  # bob is not lead
        c.check("a non-lead cannot record a decision", r.returncode != 0, r.stdout + r.stderr)


def test_ratify_refuses_a_decision_item(c):
    # THE safety guarantee: a decision item (op='decide') can NEVER be ratified into the
    # constitution. Only constitutional motions reach the law, via human ratify.
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "x", "--from", "ada"], env)
        mid = _mid(cli(["decide", "ship it?", "--because", "#1", "--from", "ada"], env).stdout)
        r = cli(["ratify", "M" + mid], env)
        c.check("ratify refuses a decision item (it can't touch the law)",
                r.returncode != 0 and "decision" in (r.stdout + r.stderr).lower(),
                r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# surfacing (inheritance) + dormancy
# --------------------------------------------------------------------------- #
def test_briefing_surfaces_session_and_decisions(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_ada", "--from", "ada"], env)
        cli(["session", "open", "rework auth", "--from", "ada"], env)
        mid = _mid(cli(["decide", "drop oauth?", "--because", "#1", "--from", "ada"], env).stdout)
        cli(["decision", "M" + mid, "keep oauth, add pkce", "--from", "ada"], env)
        ctx = _briefing(env, "s_bob", root)
        c.check("a joiner's briefing surfaces the open session",
                "rework auth" in ctx, ctx)
        c.check("a joiner inherits the room's decisions",
                "keep oauth" in ctx, ctx)


def test_dormant_when_no_session(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        who = cli(["who"], env).stdout
        c.check("who is dormant about sessions when none open",
                "session:" not in who.lower(), who)
        ctx = _briefing(env, "s2", root)
        c.check("a briefing with no session has no session line",
                "parliamentary session" not in ctx.lower() and "agenda" not in ctx.lower(),
                ctx)


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def _const_env(root):
    """Env with a CLAUDE_PROJECT_DIR so `motion`/`ratify` can find a constitution."""
    env = dict(init_room(root))
    proj = os.path.join(root, "proj"); os.makedirs(proj, exist_ok=True)
    env["CLAUDE_PROJECT_DIR"] = proj
    cli(["constitution", "init"], env)
    return env


def test_session_show_explicit_works(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "topic", "--from", "ada"], env)
        r = cli(["session", "show"], env)
        c.check("the advertised `session show` form works (not just bare `session`)",
                r.returncode == 0 and "topic" in r.stdout, r.stdout + r.stderr)


def test_motion_does_not_supersede_a_decide_item(c):
    # A decide item whose question literally equals a rule id must NOT be collaterally
    # superseded when a constitutional motion on that rule opens (lane isolation).
    with tmp_root() as root:
        env = _const_env(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "x", "--from", "ada"], env)
        cli(["decide", "R2", "--because", "#1", "--from", "ada"], env)  # degenerate question
        cli(["motion", "--from", "ada", "--rule", "R2", "--change", "new text",
             "--because", "#2"], env)
        conn = db(root)
        st = conn.execute("SELECT status FROM motions WHERE op='decide'").fetchone()[0]
        conn.close()
        c.check("a constitutional motion does NOT supersede a colliding decide item",
                st == "open", st)


def test_expired_session_reaps_its_items(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "x", "--from", "ada"], env)
        cli(["decide", "q?", "--because", "#1", "--from", "ada"], env)
        conn = db(root)
        conn.execute("UPDATE meta SET value=? WHERE key='parl_session_at'",
                     ("2000-01-01T00:00:00Z",))
        conn.commit(); conn.close()
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            chat.parl_session(conn)  # detects staleness -> reaps
            st = conn.execute("SELECT status FROM motions WHERE op='decide'").fetchone()[0]
            conn.close()
            c.check("an auto-expired session's open items are reaped (not orphaned)",
                    st == "expired", st)
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_redecide_is_refused(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)  # floor lead
        cli(["session", "open", "x", "--from", "ada"], env)
        mid = _mid(cli(["decide", "q?", "--because", "#1", "--from", "ada"], env).stdout)
        cli(["decision", "M" + mid, "first call", "--from", "ada"], env)
        r = cli(["decision", "M" + mid, "second call", "--from", "ada"], env)
        c.check("re-deciding an already-resolved item is refused",
                r.returncode != 0, r.stdout + r.stderr)
        conn = db(root)
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE kind='decision'").fetchone()[0]
        conn.close()
        c.check("...and no duplicate decision record is written", n == 1, str(n))


def test_amendments_excludes_decide_items(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["session", "open", "x", "--from", "ada"], env)
        cli(["decide", "adopt ruff?", "--because", "#1", "--from", "ada"], env)
        am = cli(["amendments"], env).stdout
        c.check("amendments (the constitutional view) excludes decision items",
                "adopt ruff" not in am, am)


def main():
    c = Checker("parliamentary sessions / agendas / decisions (governance framing)")
    for name, fn in (
        ("session_open_close_show", test_session_open_close_show),
        ("session_open_rejects_when_already_open", test_session_open_rejects_when_already_open),
        ("session_auto_expires", test_session_auto_expires),
        ("decide_and_agenda", test_decide_and_agenda),
        ("vote_on_decision_item", test_vote_on_decision_item),
        ("record_decision", test_record_decision),
        ("decision_is_lead_gated", test_decision_is_lead_gated),
        ("ratify_refuses_a_decision_item", test_ratify_refuses_a_decision_item),
        ("briefing_surfaces_session_and_decisions", test_briefing_surfaces_session_and_decisions),
        ("dormant_when_no_session", test_dormant_when_no_session),
        ("session_show_explicit_works", test_session_show_explicit_works),
        ("motion_does_not_supersede_a_decide_item", test_motion_does_not_supersede_a_decide_item),
        ("expired_session_reaps_its_items", test_expired_session_reaps_its_items),
        ("redecide_is_refused", test_redecide_is_refused),
        ("amendments_excludes_decide_items", test_amendments_excludes_decide_items),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
