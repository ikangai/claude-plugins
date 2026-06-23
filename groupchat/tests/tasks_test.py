#!/usr/bin/env python3
"""Phase-1 coordinator primitives — tasks, assignment, and a shared goal.

These turn the chat *room* into a *coordinator*: a durable, race-safe work-division
primitive on the bus, plus the surfaces that make an agent learn its slice without a
human typing it into each window.

  * a ``tasks`` table + ``task add/claim/list/done`` — open vs claimed vs done work,
    with an ATOMIC claim so two agents can't both grab the same task;
  * ``assign <handle> "…"`` — create a task already owned by a teammate AND @mention
    them, so an assignment is both durable (survives the 15-line scroll) and delivered;
  * a ``goal`` meta key (like ``lead`` / ``team_size``) auto-set by ``bootstrap`` and
    surfaced in the briefing / ``who``;
  * per-agent ``bootstrap`` prompts (``ada:'do X' turing:'do Y'``) so an orchestrator
    can deal out DISTINCT work instead of one identical prompt to everyone.

Everything is dormant-until-used: a room with no tasks and no goal renders exactly as
before. Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/tasks_test.py
"""
import json
import os
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


def _tasks(root):
    conn = db(root)
    try:
        return conn.execute(
            "SELECT id, title, owner, status, paths FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _briefing(env, sid, cwd):
    out = hook("session_start.py", env,
               {"session_id": sid, "cwd": cwd, "hook_event_name": "SessionStart"})
    obj = parse_hook_json(out.stdout) or {}
    return obj.get("hookSpecificOutput", {}).get("additionalContext", "")


# --------------------------------------------------------------------------- #
# tasks: add / list / claim / done
# --------------------------------------------------------------------------- #
def test_task_add_and_list(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)

        r = cli(["task", "add", "build the parser", "--paths", "src/*.py"], env)
        c.check("task add succeeds", r.returncode == 0, r.stdout + r.stderr)
        c.check("task add reports the new id", "#1" in r.stdout, r.stdout)

        rows = _tasks(root)
        c.check("a task row is created", len(rows) == 1, str(rows))
        c.check("a fresh task is open with no owner",
                rows and rows[0]["status"] == "open" and rows[0]["owner"] is None,
                str(rows))
        c.check("the path-glob hint is stored",
                rows and rows[0]["paths"] == "src/*.py", str(rows))

        out = cli(["task", "list"], env).stdout
        c.check("task list shows the title", "build the parser" in out, out)
        c.check("task list shows the open status", "open" in out, out)


def test_claim_is_atomic(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["task", "add", "the only task"], env)

        r = cli(["task", "claim", "1", "--from", "alice"], env)
        c.check("first claim succeeds", r.returncode == 0, r.stdout + r.stderr)
        rows = _tasks(root)
        c.check("claim records the owner and flips status to claimed",
                rows[0]["owner"] == "alice" and rows[0]["status"] == "claimed",
                str(rows))

        r2 = cli(["task", "claim", "1", "--from", "bob"], env)
        c.check("a second agent cannot claim the same task",
                r2.returncode != 0, r2.stdout + r2.stderr)
        c.check("...and is told who already holds it", "alice" in (r2.stdout + r2.stderr),
                r2.stdout + r2.stderr)
        c.check("the owner is unchanged after a losing claim",
                _tasks(root)[0]["owner"] == "alice", str(_tasks(root)))

        r3 = cli(["task", "claim", "1", "--from", "alice"], env)
        c.check("re-claiming your own task is idempotent (not an error)",
                r3.returncode == 0, r3.stdout + r3.stderr)


def test_done_hides_from_default_list(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["task", "add", "finish me"], env)
        cli(["task", "claim", "1", "--from", "alice"], env)

        r = cli(["task", "done", "1", "--from", "alice"], env)
        c.check("task done succeeds", r.returncode == 0, r.stdout + r.stderr)
        c.check("the task is marked done", _tasks(root)[0]["status"] == "done",
                str(_tasks(root)))

        default = cli(["task", "list"], env).stdout
        c.check("a done task is hidden from the default list",
                "finish me" not in default, default)
        allout = cli(["task", "list", "--all"], env).stdout
        c.check("--all reveals done tasks", "finish me" in allout, allout)


def test_done_on_open_task_records_doer(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["task", "add", "quick one"], env)  # never claimed
        r = cli(["task", "done", "1", "--from", "alice"], env)
        c.check("done on an unclaimed task still succeeds", r.returncode == 0,
                r.stdout + r.stderr)
        rows = _tasks(root)
        c.check("done on an open task records the doer as owner",
                rows[0]["owner"] == "alice" and rows[0]["status"] == "done", str(rows))


def test_missing_task_is_an_error(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        r = cli(["task", "claim", "99", "--from", "alice"], env)
        c.check("claiming a nonexistent task errors", r.returncode != 0,
                r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# assign: durable + notified
# --------------------------------------------------------------------------- #
def test_assign_creates_owned_task_and_pings(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        # bob is NOT active yet — assignment must still land (durable in the table,
        # delivered to bob via the cursor when he joins).
        r = cli(["assign", "bob", "write the tests", "--from", "alice"], env)
        c.check("assign succeeds", r.returncode == 0, r.stdout + r.stderr)

        rows = _tasks(root)
        c.check("assign creates a task owned by the assignee, already claimed",
                len(rows) == 1 and rows[0]["owner"] == "bob"
                and rows[0]["status"] == "claimed", str(rows))

        conn = db(root)
        msgs = conn.execute(
            "SELECT body, mentions FROM messages WHERE kind='chat'").fetchall()
        conn.close()
        pinged = any("bob" in (m["mentions"] or "") and "write the tests" in m["body"]
                     for m in msgs)
        c.check("assign @mentions the assignee so it rides their cursor", pinged,
                str([dict(m) for m in msgs]))


# --------------------------------------------------------------------------- #
# goal: a shared objective
# --------------------------------------------------------------------------- #
def test_goal_set_show_clear(c):
    with tmp_root() as root:
        env = init_room(root)
        c.check("an unset goal reports none",
                "no goal" in cli(["goal"], env).stdout.lower(), cli(["goal"], env).stdout)
        cli(["goal", "ship v1 of the parser"], env)
        c.check("a set goal is shown back",
                "ship v1 of the parser" in cli(["goal"], env).stdout,
                cli(["goal"], env).stdout)
        cli(["goal", "--clear"], env)
        c.check("a cleared goal reports none again",
                "no goal" in cli(["goal"], env).stdout.lower(), cli(["goal"], env).stdout)


# --------------------------------------------------------------------------- #
# surfacing: who + briefing (and dormant-until-used)
# --------------------------------------------------------------------------- #
def test_who_surfaces_goal_and_tasks(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)

        bare = cli(["who"], env).stdout
        c.check("who is dormant when there is no goal", "goal:" not in bare.lower(), bare)
        c.check("who is dormant when there are no tasks", "task" not in bare.lower(), bare)

        cli(["goal", "ship v1"], env)
        cli(["task", "add", "a"], env)
        cli(["task", "add", "b"], env)
        cli(["task", "claim", "1", "--from", "alice"], env)
        out = cli(["who"], env).stdout
        c.check("who surfaces the goal once set", "ship v1" in out, out)
        c.check("who surfaces a task tally", "task" in out.lower(), out)
        c.check("who counts open and claimed tasks",
                "1 open" in out and "1 claimed" in out, out)


def test_briefing_surfaces_goal_and_my_assignment(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        cli(["goal", "ship v1"], env)
        # Assign BEFORE the assignee has joined: the durable row must surface on its
        # first turn. Pin the joining handle with GROUPCHAT_HANDLE so 'carol' is the
        # task's owner AND the agent that joins (pool assignment is otherwise nondet).
        env2 = dict(env); env2["GROUPCHAT_HANDLE"] = "carol"
        cli(["assign", "carol", "polish the docs", "--from", "alice"], env)
        ctx2 = _briefing(env2, "s_carol", root)
        c.check("the briefing shows the shared goal", "ship v1" in ctx2, ctx2)
        c.check("the briefing surfaces the joining agent's own assignment",
                "polish the docs" in ctx2, ctx2)


def test_briefing_dormant_without_goal_or_tasks(c):
    with tmp_root() as root:
        env = init_room(root)
        ctx = _briefing(env, "s1", root)
        c.check("a briefing with no goal has no Goal line", "goal:" not in ctx.lower(), ctx)
        c.check("a briefing with no tasks has no task lines",
                "task" not in ctx.lower(), ctx)


# --------------------------------------------------------------------------- #
# bootstrap: auto-set goal + per-agent prompts
# --------------------------------------------------------------------------- #
def test_bootstrap_sets_goal_and_deals_distinct_prompts(c):
    import argparse
    import contextlib
    import io
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".groupchat"))
    import chat  # noqa: E402

    def _ns(**kw):
        base = dict(spec=None, method="terminal", cwd="/x", prompt=None, goal=None,
                    dry_run=False, worktree=False, force=False)
        base.update(kw)
        return argparse.Namespace(**base)

    captured = {}

    def _fake_spawn(names, cwd, **kw):
        captured["names"] = list(names)
        captured["prompt"] = kw.get("prompt")
        captured["prompts"] = kw.get("prompts")
        return [{"name": n, "command": "c", "ok": True, "error": None} for n in names]

    orig, orig_poll = chat.spawn_agents, chat.poll_joined
    chat.spawn_agents = _fake_spawn
    chat.poll_joined = lambda conn, names, **kw: {n: True for n in names}
    try:
        def _boot(root, **ns):
            os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
            os.environ.pop("GROUPCHAT_TEAM_SIZE", None)
            conn = chat.connect(); chat.register(conn, "sb", handle="boss"); conn.close()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                chat.cmd_bootstrap(_ns(**ns))
            return buf.getvalue()

        with tmp_root() as root:
            _boot(root, spec=["ada:do the parser", "turing:do the tests"],
                  goal="ship v1", method="terminal")
            conn = chat.connect()
            goal = chat.get_meta(conn, "goal")
            conn.close()
            c.check("bootstrap auto-sets the shared goal", goal == "ship v1", str(goal))
            c.check("per-agent specs resolve to distinct handles",
                    captured.get("names") == ["ada", "turing"], str(captured))
            c.check("each handle is dealt its own prompt",
                    captured.get("prompts") == {"ada": "do the parser",
                                                "turing": "do the tests"},
                    str(captured.get("prompts")))

        with tmp_root() as root:
            _boot(root, spec=["2"], goal="ship v1", dry_run=True)
            conn = chat.connect(); goal = chat.get_meta(conn, "goal"); conn.close()
            c.check("a dry-run preview does NOT set the goal", goal is None, str(goal))
    finally:
        chat.spawn_agents = orig
        chat.poll_joined = orig_poll
        os.environ.pop("GROUPCHAT_TEAM_SIZE", None)
        os.environ.pop("GROUPCHAT_DIR", None)


# --------------------------------------------------------------------------- #
# assign must not leak title text into routing (the @human / @mention surface)
# --------------------------------------------------------------------------- #
def test_assign_title_does_not_route_or_escalate(c):
    # A lead whose assign TITLE contains @human must not open a phantom escalation
    # (which would wedge the lead-done gate — invariant #5), and an assignment must
    # ping only the assignee, never a handle quoted inside the title.
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_lead", "--from", "lead"], env)  # sole agent = floor lead
        cli(["assign", "carol", "ask @human about the scope", "--from", "lead"], env)
        q = cli(["questions"], env).stdout.lower()
        c.check("an @human inside an assign title opens NO escalation (no lead-gate wedge)",
                "owes you nothing" in q or "no open" in q or "no active lead" in q, q)

        cli(["assign", "dave", "ping @carol and @human now", "--from", "lead"], env)
        conn = db(root)
        m = conn.execute(
            "SELECT mentions FROM messages WHERE kind='chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        ms = json.loads(m["mentions"] or "[]")
        c.check("an assignment pings only the assignee, not handles inside the title",
                ms == ["dave"], str(ms))


def test_assign_mention_blocks_stop_not_barrier(c):
    # The assignment @mention rides the normal mention path: it blocks the assignee's
    # Stop (so they act on it), but it is NOT an escalation and must not wedge the team.
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        cli(["assign", "bob", "write the tests", "--from", "alice"], env)
        out = hook("stop.py", env,
                   {"session_id": "s_bob", "hook_event_name": "Stop", "stop_hook_active": False})
        obj = parse_hook_json(out.stdout) or {}
        c.check("an assignment @mention blocks the assignee's Stop",
                obj.get("decision") == "block", out.stdout)
        c.check("...and hands back the assignment text so it isn't dropped",
                "write the tests" in (obj.get("reason", "")), obj.get("reason", ""))
        q = cli(["questions"], env).stdout.lower()
        c.check("an assignment is not an @human escalation",
                "owes you nothing" in q or "no open" in q or "no active lead" in q, q)


def test_assign_rejects_reserved_handle(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        r = cli(["assign", "human", "do x", "--from", "alice"], env)
        c.check("assign to a reserved handle is rejected", r.returncode != 0, r.stdout + r.stderr)
        c.check("...and creates no task", len(_tasks(root)) == 0, str(_tasks(root)))
        conn = db(root)
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE kind='chat'").fetchone()[0]
        conn.close()
        c.check("...and posts no message", n == 0, str(n))


# --------------------------------------------------------------------------- #
# concurrency: the claim is atomic; completion never clobbers a won claim
# --------------------------------------------------------------------------- #
def test_claim_is_atomic_under_threads(c):
    import threading
    chat = _import_chat()
    with tmp_root() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect(); chat.add_task(conn, "contended"); conn.close()
            N = 16
            outcomes = [None] * N
            start = threading.Barrier(N)
            def worker(i):
                c2 = chat.connect()
                start.wait()
                res, row = chat.claim_task(c2, 1, f"agent{i}")
                outcomes[i] = (res, row["owner"] if row else None)
                c2.close()
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            winners = [o for o in outcomes if o and o[0] == "claimed"]
            c.check("exactly one of N racing threads wins the claim",
                    len(winners) == 1, str(outcomes))
            conn = chat.connect(); owner = chat.task_by_id(conn, 1)["owner"]; conn.close()
            c.check("the ledger owner is that sole winner",
                    bool(winners) and winners[0][1] == owner, str((winners, owner)))
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_complete_does_not_clobber_a_concurrent_claim(c):
    import threading
    chat = _import_chat()
    with tmp_root() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            bad = 0
            TRIALS = 80
            for t in range(TRIALS):
                conn = chat.connect(); tid = chat.add_task(conn, f"task{t}"); conn.close()
                claim_out = [None]
                b = threading.Barrier(2)
                def do_claim():
                    c2 = chat.connect(); b.wait()
                    claim_out[0] = chat.claim_task(c2, tid, "alice"); c2.close()
                def do_complete():
                    c3 = chat.connect(); b.wait()
                    chat.complete_task(c3, tid, "bob"); c3.close()
                ths = [threading.Thread(target=do_claim),
                       threading.Thread(target=do_complete)]
                for x in ths:
                    x.start()
                for x in ths:
                    x.join()
                res, _row = claim_out[0]
                conn = chat.connect(); final = chat.task_by_id(conn, tid); conn.close()
                if res == "claimed" and final["owner"] != "alice":
                    bad += 1  # a completer overwrote the owner of a claim we were told we won
            c.check("a completer never clobbers the owner of a concurrently-won claim",
                    bad == 0, f"{bad}/{TRIALS} trials clobbered the winning claim")
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)


def test_nonowner_completion_preserves_owner(c):
    # The bus is cooperative/unauthenticated, so a non-owner CAN close a task (e.g. a
    # lead tidying up a crashed agent's slice). Document that — and that it must NOT
    # rewrite the original owner (the lost-update the atomic fix prevents).
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s_alice", "--from", "alice"], env)
        cli(["register", "--session", "s_bob", "--from", "bob"], env)
        cli(["task", "add", "shared"], env)
        cli(["task", "claim", "1", "--from", "alice"], env)
        r = cli(["task", "done", "1", "--from", "bob"], env)
        c.check("a non-owner may complete a task (cooperative policy)",
                r.returncode == 0, r.stdout + r.stderr)
        rows = _tasks(root)
        c.check("completing someone else's task preserves the original owner",
                rows[0]["owner"] == "alice" and rows[0]["status"] == "done", str(rows))


def test_out_of_range_task_id_is_clean_error(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        r = cli(["task", "claim", "99999999999999999999999", "--from", "alice"], env)
        c.check("an out-of-range task id errors cleanly, not with a traceback",
                r.returncode != 0 and "Traceback" not in (r.stdout + r.stderr),
                r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# pure-function + real-spawn coverage (per-agent prompt wiring)
# --------------------------------------------------------------------------- #
def test_parse_spec_edge_cases(c):
    chat = _import_chat()
    c.check("a plain name has no prompt", chat._parse_spec("ada") == ("ada", None))
    c.check("name:prompt splits on the first colon",
            chat._parse_spec("ada:do X") == ("ada", "do X"))
    c.check("later colons stay in the prompt",
            chat._parse_spec("ada:run a:b") == ("ada", "run a:b"))
    c.check("an empty prompt after the colon is None",
            chat._parse_spec("ada:") == ("ada", None))
    c.check("a whitespace-only prompt is None",
            chat._parse_spec("ada:   ") == ("ada", None))
    c.check("a leading colon yields an empty handle (caller sanitizes)",
            chat._parse_spec(":foo") == ("", "foo"))


def test_spawn_agents_deals_per_agent_commands(c):
    # method='print' spawns nothing — safe to run anywhere. Verifies the prompt map
    # actually reaches each launch command (the parse→deal wiring end to end).
    chat = _import_chat()
    res = chat.spawn_agents(["ada", "turing"], cwd="/x", method="print",
                            prompts={"ada": "do A"}, prompt="fallback")
    cmds = {r["name"]: r["command"] for r in res}
    c.check("a handle in the prompts map gets its own prompt",
            "do A" in cmds["ada"], cmds["ada"])
    c.check("a handle absent from the map falls back to the uniform prompt",
            "fallback" in cmds["turing"], cmds["turing"])


def main():
    c = Checker("Phase-1 coordinator primitives (tasks / assign / goal / bootstrap)")
    for name, fn in (
        ("task_add_and_list", test_task_add_and_list),
        ("claim_is_atomic", test_claim_is_atomic),
        ("done_hides_from_default_list", test_done_hides_from_default_list),
        ("done_on_open_task_records_doer", test_done_on_open_task_records_doer),
        ("missing_task_is_an_error", test_missing_task_is_an_error),
        ("assign_creates_owned_task_and_pings", test_assign_creates_owned_task_and_pings),
        ("goal_set_show_clear", test_goal_set_show_clear),
        ("who_surfaces_goal_and_tasks", test_who_surfaces_goal_and_tasks),
        ("briefing_surfaces_goal_and_my_assignment",
         test_briefing_surfaces_goal_and_my_assignment),
        ("briefing_dormant_without_goal_or_tasks",
         test_briefing_dormant_without_goal_or_tasks),
        ("bootstrap_sets_goal_and_deals_distinct_prompts",
         test_bootstrap_sets_goal_and_deals_distinct_prompts),
        ("assign_title_does_not_route_or_escalate",
         test_assign_title_does_not_route_or_escalate),
        ("assign_mention_blocks_stop_not_barrier",
         test_assign_mention_blocks_stop_not_barrier),
        ("assign_rejects_reserved_handle", test_assign_rejects_reserved_handle),
        ("claim_is_atomic_under_threads", test_claim_is_atomic_under_threads),
        ("complete_does_not_clobber_a_concurrent_claim",
         test_complete_does_not_clobber_a_concurrent_claim),
        ("nonowner_completion_preserves_owner", test_nonowner_completion_preserves_owner),
        ("out_of_range_task_id_is_clean_error", test_out_of_range_task_id_is_clean_error),
        ("parse_spec_edge_cases", test_parse_spec_edge_cases),
        ("spawn_agents_deals_per_agent_commands", test_spawn_agents_deals_per_agent_commands),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
