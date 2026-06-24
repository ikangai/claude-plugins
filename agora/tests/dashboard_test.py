#!/usr/bin/env python3
"""Tests for the live room dashboard (``.groupchat/dashboard.py``).

The dashboard is a *read-only* renderer: it turns a snapshot of the bus
(roster, conversation, parliament, barrier) into a self-contained HTML file a
human can open to watch the fleet — a single pane of glass that needs no server
and never writes to ``chat.db``. These tests follow the repo's no-framework
convention (stdlib only, isolated via ``GROUPCHAT_DIR``) and split cleanly:

  * ``render_html`` is a *pure* function (snapshot dict -> HTML string), so most
    behavior — structure, escaping, mention rendering, live vs. snapshot — is
    tested without a database at all.
  * ``collect`` / ``generate`` are exercised against a seeded, isolated room.

Run:  python3 tests/dashboard_test.py      # exit 0 = all pass
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# Import the production modules under test (both live in .groupchat/).
sys.path.insert(0, os.path.join(ROOT, ".groupchat"))

from _util import Checker, tmp_root, init_room, cli, db, db_path  # noqa: E402

import chat  # noqa: E402  (read-only; used to simulate a failing bus query)
import dashboard  # noqa: E402  (the module under test)


class _point_env_at:
    """Point this *process's* store resolution at an isolated room for the
    duration of a block. ``generate()`` resolves its db from the environment
    (exactly as chat.py does in production), so an in-process call needs the env
    set here, not just in a subprocess. Restores the prior value on exit."""

    def __init__(self, root):
        self.dir = os.path.join(root, ".groupchat")

    def __enter__(self):
        self._old = os.environ.get("GROUPCHAT_DIR")
        os.environ["GROUPCHAT_DIR"] = self.dir
        return self

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("GROUPCHAT_DIR", None)
        else:
            os.environ["GROUPCHAT_DIR"] = self._old
        return False


def _sample_snapshot():
    """A hand-built snapshot exercising the rendering edge cases."""
    return {
        "title": "groupchat room",
        "generated_display": "2026-06-07 09:30",
        "room_dir": "/x/.groupchat",
        "live": False,
        "refresh": 10,
        "agents": [
            {"handle": "ada", "label": "active", "dot": "green",
             "cwd": "claude_chat", "age": "3s", "out_display": "1.2k", "out_tokens": 1234,
             "in_tokens": 500, "in_display": "500",
             "cache_read_tokens": 90000, "cache_read_display": "90k",
             "cache_create_tokens": 7000, "cache_create_display": "7k"},
            {"handle": "turing", "label": "done · parked", "dot": "blue",
             "cwd": "claude_chat", "age": "2m", "out_display": "44k", "out_tokens": 44000,
             "in_tokens": 2000, "in_display": "2k",
             "cache_read_tokens": 310000, "cache_read_display": "310k",
             "cache_create_tokens": 12000, "cache_create_display": "12k"},
        ],
        "token_totals": {
            "in": 2500, "out": 45234, "cache_read": 400000, "cache_create": 19000,
            "in_display": "2.5k", "out_display": "45.2k",
            "cache_read_display": "400k", "cache_create_display": "19k",
        },
        "messages": [
            {"id": 1, "time": "09:29", "sender": "ada", "mentions": [],
             "kind": "chat", "body": "starting on the bus"},
            {"id": 2, "time": "09:30", "sender": "turing", "mentions": ["ada"],
             "kind": "chat", "body": "ping @ada — needs <review> & care"},
            {"id": 3, "time": "09:30", "sender": "system", "mentions": [],
             "kind": "system", "body": "M1 ratified"},
        ],
        "motions": [
            {"id": 1, "op": "amend", "target": "R2", "proposer": "ada",
             "status": "open", "yea": 2, "nay": 1, "voters": 3,
             "because": "evidence here", "flag": "below the advisory bar"},
        ],
        "barrier": {"active": 2, "done": 1, "team_done": False,
                    "expected": None, "label": "1/2 done"},
        "lead": {"handle": "ada", "source": "floor (auto-elected)"},
        "escalations": [
            {"id": 77, "time": "09:50",
             "body": "ship the migration to prod now, or wait for review? @human"},
        ],
        "constitution": {"core": 4, "articles": 6},
    }


def test_render_is_self_contained_html(c):
    html = dashboard.render_html(_sample_snapshot())
    c.check("render returns a full HTML document",
            html.lstrip().lower().startswith("<!doctype html>") and "</html>" in html,
            html[:60])
    c.check("styling is inlined (no external assets / no server needed)",
            "<style" in html and "http://" not in html.replace("http-equiv", "")
            and "src=" not in html, "expected self-contained")


def test_render_shows_roster_and_conversation(c):
    html = dashboard.render_html(_sample_snapshot())
    c.check("every agent handle is rendered", "ada" in html and "turing" in html)
    c.check("agent status label is rendered", "done · parked" in html)
    c.check("token burn is rendered", "44k" in html)
    c.check("message bodies are rendered",
            "starting on the bus" in html and "needs" in html)
    c.check("message ids are rendered", "#1" in html and "#2" in html)


def test_render_escapes_html(c):
    html = dashboard.render_html(_sample_snapshot())
    # The body "needs <review> & care" must never inject raw markup.
    c.check("angle brackets in a body are escaped",
            "<review>" not in html and "&lt;review&gt;" in html,
            "unescaped body leaks markup")
    c.check("ampersands in a body are escaped", "&amp;" in html)


def test_render_marks_mentions(c):
    html = dashboard.render_html(_sample_snapshot())
    c.check("a message's @mention target is rendered as a mention",
            "@ada" in html, "expected @ada mention arrow/chip")


def test_render_shows_parliament_and_barrier(c):
    html = dashboard.render_html(_sample_snapshot())
    c.check("open motion is rendered", "M1" in html and "R2" in html)
    c.check("advisory tally is rendered", "2" in html and "nay" in html.lower())
    c.check("votes are framed as advisory (never a green 'passes')",
            "advisory" in html.lower() and "passes" not in html.lower())
    c.check("barrier state is rendered", "1/2 done" in html)


def test_render_shows_lead(c):
    html = dashboard.render_html(_sample_snapshot())
    c.check("the current lead handle is surfaced", "ada" in html and "floor" in html)
    c.check("lead panel labels the hub-and-spoke routing",
            "human" in html.lower() and "lead" in html.lower())
    # Flat mode (no lead) must read clearly, not blank.
    snap = _sample_snapshot()
    snap["lead"] = {"handle": None, "source": "flat"}
    flat = dashboard.render_html(snap)
    c.check("flat mode (no lead) renders a clear 'flat' state",
            "flat" in flat.lower())


def test_render_shows_escalations(c):
    """The human-facing half of hub-and-spoke: open @human questions the operator
    hasn't answered are surfaced so a human can glance instead of being pinged."""
    html = dashboard.render_html(_sample_snapshot())
    c.check("an open escalation's body is surfaced", "ship the migration" in html)
    c.check("the escalation id is shown", "#77" in html)
    c.check("the panel labels itself as awaiting the human",
            "escalation" in html.lower() or "awaiting" in html.lower())
    # Empty must read as reassuring, not blank.
    snap = _sample_snapshot()
    snap["escalations"] = []
    empty = dashboard.render_html(snap)
    c.check("no open escalations reads as caught-up", "caught up" in empty.lower())


def test_collect_escalations_from_helper(c):
    """collect() consumes newton's chat.open_escalations(conn, lead) -> list[int]
    (message-ids), looking each up to {id, time, body}. Real integration against the
    actual helper; degrades to [] (never crashes) if the helper isn't landed yet."""
    if not hasattr(chat, "open_escalations"):
        c.check("open_escalations helper present (skipped — not landed)", True)
        return
    with tmp_root() as root:
        env = init_room(root)
        now = dashboard._now_iso()
        # An active lead, so resolve_lead() returns it and the send-guard lets the
        # lead's own @human pass through (workers' @human would be rewritten).
        conn = db(root)
        conn.execute(
            "INSERT INTO agents(session_id, handle, cwd, status, first_seen, "
            "last_seen, last_read_id) VALUES (?,?,?,?,?,?,?)",
            ("s-ada", "ada", root, None, now, now, 0))
        conn.commit()
        chat.set_meta(conn, "lead", "ada")
        conn.close()
        # The lead escalates a question to the human.
        cli(["send", "--from", "ada", "ship the migration now? @human"], env)
        conn = db(root)
        snap = dashboard.collect(conn)
        conn.close()
        esc = snap.get("escalations") or []
        c.check("collect surfaces an open escalation from the real helper",
                len(esc) >= 1, esc)
        c.check("escalation carries the question body",
                any("migration" in (e.get("body") or "") for e in esc), esc)
        c.check("escalation carries a real message id",
                all(isinstance(e.get("id"), int) for e in esc), esc)


def test_render_shows_token_panel(c):
    """The full chat.py-tokens view (in / out / cache-read / cache-create per
    agent + totals), not just the roster's out-burn chip."""
    html = dashboard.render_html(_sample_snapshot())
    c.check("a tokens panel is rendered", "Tokens" in html or "tokens" in html)
    c.check("all four counters are labelled",
            all(k in html.lower() for k in ("in", "out", "cache-read", "cache-create")),
            "expected in/out/cache-read/cache-create column labels")
    c.check("per-agent counts are rendered",
            "90k" in html and "310k" in html and "12k" in html)
    c.check("a totals row is rendered",
            "45.2k" in html and "400k" in html and "total" in html.lower())
    c.check("counts are framed as approximate (transcript-derived)",
            "approx" in html.lower())
    # A snapshot without token_totals (older caller) must still render.
    snap = _sample_snapshot()
    snap.pop("token_totals")
    degraded = dashboard.render_html(snap)
    c.check("missing token_totals degrades, not crashes",
            degraded.lstrip().lower().startswith("<!doctype html>"))


def test_collect_token_totals(c):
    """collect() carries all four token counters per agent and sums totals."""
    with tmp_root() as root:
        init_room(root)
        conn = db(root)
        now = dashboard._now_iso()
        conn.executemany(
            "INSERT INTO agents(session_id, handle, cwd, status, first_seen, "
            "last_seen, last_read_id, in_tokens, out_tokens, cache_read_tokens, "
            "cache_create_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [("s-ada", "ada", root, None, now, now, 0, 500, 1234, 90000, 7000),
             ("s-tur", "turing", root, "done", now, now, 0, 2000, 44000, 310000, 12000)],
        )
        conn.commit()
        snap = dashboard.collect(conn)
        conn.close()
        by = {a["handle"]: a for a in snap["agents"]}
        c.check("each agent carries all four counters",
                by["ada"].get("in_tokens") == 500
                and by["ada"].get("cache_read_tokens") == 90000
                and by["turing"].get("cache_create_tokens") == 12000, by)
        tot = snap.get("token_totals") or {}
        c.check("totals sum across agents",
                tot.get("in") == 2500 and tot.get("out") == 45234
                and tot.get("cache_read") == 400000
                and tot.get("cache_create") == 19000, tot)
        c.check("totals carry display strings", bool(tot.get("out_display")), tot)


def test_live_mode_autorefreshes(c):
    snap = _sample_snapshot()
    snap["live"] = True
    snap["refresh"] = 7
    live = dashboard.render_html(snap)
    snap["live"] = False
    snapshot_only = dashboard.render_html(snap)
    c.check("live mode embeds an auto-refresh", "7" in live
            and ("http-equiv=\"refresh\"" in live or "location.reload" in live),
            "expected refresh mechanism in live mode")
    c.check("snapshot mode does not auto-refresh",
            "http-equiv=\"refresh\"" not in snapshot_only
            and "location.reload" not in snapshot_only,
            "snapshot must be static")


def test_render_text_is_a_compact_summary(c):
    """A terminal/agent-facing one-call summary: the same snapshot, rendered as
    plain text. Lets an agent read the whole room state in one command instead of
    who + lead + amendments + questions."""
    txt = dashboard.render_text(_sample_snapshot())
    c.check("text mode lists the roster handles",
            "ada" in txt and "turing" in txt)
    c.check("text mode shows the lead", "ada" in txt and "lead" in txt.lower())
    c.check("text mode shows the barrier", "1/2 done" in txt)
    c.check("text mode shows open escalations",
            "migration" in txt or "escalation" in txt.lower())
    c.check("text mode shows open motions", "M1" in txt and "R2" in txt)
    c.check("text mode shows the token totals",
            "45.2k" in txt and "cache" in txt.lower())
    c.check("text mode is plain text, not HTML",
            "<!doctype" not in txt.lower() and "<div" not in txt.lower())


def test_collect_reads_a_seeded_room(c):
    with tmp_root() as root:
        env = init_room(root)
        # Two agents on the bus (insert directly — the CLI doesn't expose this).
        conn = db(root)
        now = dashboard._now_iso()
        conn.executemany(
            "INSERT INTO agents(session_id, handle, cwd, status, first_seen, "
            "last_seen, last_read_id, out_tokens) VALUES (?,?,?,?,?,?,?,?)",
            [("s-ada", "ada", root, None, now, now, 0, 1234),
             ("s-tur", "turing", root, "done", now, now, 0, 44000)],
        )
        conn.commit()
        conn.close()
        cli(["send", "--from", "ada", "starting @turing"], env)
        cli(["send", "--from", "turing", "ack"], env)

        # collect() must read the isolated room, not the live one.
        conn = db(root)
        snap = dashboard.collect(conn)
        conn.close()
        bodies = " ".join(m["body"] for m in snap["messages"])
        handles = {a["handle"] for a in snap["agents"]}
        c.check("collect captures both agents", {"ada", "turing"} <= handles, handles)
        c.check("collect captures sent messages",
                "starting" in bodies and "ack" in bodies, bodies)
        c.check("collect carries the mention on the first message",
                any("turing" in (m.get("mentions") or []) for m in snap["messages"]))
        c.check("collect renders a done agent's label",
                any("done" in a["label"] for a in snap["agents"]))
        # A designated lead (meta['lead']) whose holder is active resolves + is
        # tagged as designated, not the auto floor.
        conn = db(root)
        chat.set_meta(conn, "lead", "ada")
        snap2 = dashboard.collect(conn)
        conn.close()
        lead = snap2.get("lead") or {}
        c.check("collect resolves the designated lead", lead.get("handle") == "ada",
                lead)
        c.check("collect tags the lead source", bool(lead.get("source")), lead)


def test_generate_writes_file(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["send", "--from", "ada", "hello room"], env)
        out = os.path.join(root, ".groupchat", "room.html")
        with _point_env_at(root):
            path = dashboard.generate(out_path=out)
        c.check("generate returns the output path", path == out, path)
        c.check("generate writes the file", os.path.isfile(out))
        html = open(out).read() if os.path.isfile(out) else ""
        c.check("written file contains the conversation", "hello room" in html)
        c.check("written file is valid self-contained HTML",
                html.lstrip().lower().startswith("<!doctype html>"))


def test_collect_survives_a_failing_section(c):
    """The dashboard is an observability tool — one buggy/locked bus query must not
    blank the whole page. A failing section degrades to a safe default; the rest
    still renders. (Durable regardless of any specific upstream bug.)"""
    with tmp_root() as root:
        env = init_room(root)
        cli(["send", "--from", "ada", "still here"], env)
        orig = chat.team_done
        chat.team_done = lambda conn: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            conn = db(root)
            snap = dashboard.collect(conn)
            conn.close()
        finally:
            chat.team_done = orig
        bodies = " ".join(m["body"] for m in snap["messages"])
        c.check("conversation still collected despite a failing barrier read",
                "still here" in bodies, bodies)
        c.check("barrier degrades to a safe dict, not a crash",
                isinstance(snap["barrier"], dict) and bool(snap["barrier"].get("label")))
        html = dashboard.render_html(snap)
        c.check("page still renders with a degraded section",
                html.lstrip().lower().startswith("<!doctype html>"))


def test_generate_never_writes_db(c):
    """The dashboard is strictly read-only on the bus (it must never mutate state)."""
    with tmp_root() as root:
        env = init_room(root)
        cli(["send", "--from", "ada", "msg one"], env)
        before = os.path.getmtime(db_path(root))
        out = os.path.join(root, ".groupchat", "room.html")
        with _point_env_at(root):
            dashboard.generate(out_path=out)
        after = os.path.getmtime(db_path(root))
        c.check("generate does not modify chat.db", before == after,
                "dashboard must be read-only")


def main():
    c = Checker("dashboard (render / collect / generate)")
    test_render_is_self_contained_html(c)
    test_render_shows_roster_and_conversation(c)
    test_render_escapes_html(c)
    test_render_marks_mentions(c)
    test_render_shows_parliament_and_barrier(c)
    test_render_shows_lead(c)
    test_render_shows_escalations(c)
    test_collect_escalations_from_helper(c)
    test_render_shows_token_panel(c)
    test_collect_token_totals(c)
    test_live_mode_autorefreshes(c)
    test_render_text_is_a_compact_summary(c)
    test_collect_reads_a_seeded_room(c)
    test_collect_survives_a_failing_section(c)
    test_generate_writes_file(c)
    test_generate_never_writes_db(c)
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
