#!/usr/bin/env python3
"""End-to-end integration of the three subsystems that compose at the Stop hook:
the team barrier (euler), leadership/@human routing (newton+tesla), and the P2
lead-escalation gate (newton). The promised post-P2 cross-cutting probe (chat
#50): nothing tested the realistic MULTI-AGENT case — a worker parking at the
barrier *because* the lead is held open by an unanswered @human escalation, and
the whole team releasing once the operator answers. phase2 covers the lead alone
(team_size=1); hub_and_spoke covers routing at the CLI level. This drives the
real stop.py hook for both roles.

    python3 tests/barrier_escalation_e2e_test.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, cli, env_for, hook, parse_hook_json, tmp_root  # noqa: E402

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402

ENV_TUNABLES = dict(GROUPCHAT_TEAM_SIZE=2, GROUPCHAT_PARK_WINDOW=0,
                    GROUPCHAT_POLL_TICK=0.1, GROUPCHAT_MAX_PARK=3600)


def _setup(root):
    env = env_for(root, **ENV_TUNABLES)
    cli(["init"], env)
    cli(["register", "--session", "s1", "--from", "ada"], env)     # the lead
    cli(["register", "--session", "s2", "--from", "turing"], env)  # a worker
    cli(["lead", "--claim", "--from", "ada"], env)
    esc = cli(["send", "--from", "ada", "@human need a decision"], env)  # lead escalates
    mid = re.search(r"#(\d+)", esc.stdout)
    return env, (mid.group(1) if mid else None)


def test_worker_parks_while_lead_escalation_gated(c):
    with tmp_root() as root:
        env, _ = _setup(root)
        r = hook("stop.py", env,
                 {"session_id": "s2", "hook_event_name": "Stop", "stop_hook_active": False},
                 timeout=20)
        obj = parse_hook_json(r.stdout)
        c.check("worker is held at the barrier (team not done — lead is busy)",
                bool(obj and obj.get("decision") == "block"), r.stdout[:160])
        c.check("the re-park reason names the unfinished lead",
                obj and "ada" in obj.get("reason", "") and "barrier" in obj.get("reason", ""),
                obj.get("reason", "") if obj else "")


def test_lead_with_open_escalation_is_gated(c):
    with tmp_root() as root:
        env, _ = _setup(root)
        r = hook("stop.py", env,
                 {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
                 timeout=20)
        obj = parse_hook_json(r.stdout)
        c.check("lead with an open @human escalation is blocked (P2 gate)",
                bool(obj and obj.get("decision") == "block"), r.stdout[:160])


def test_operator_answer_clears_escalation(c):
    with tmp_root() as root:
        env, mid = _setup(root)
        # operator answers (sender=human, @mentions the lead) → clears the queue.
        cli(["answer", mid, "go with option A"], env)
        # open_escalations for the lead is now empty (verified via the real helper).
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        conn = chat.connect()
        opens = chat.open_escalations(conn, "ada")
        conn.close()
        c.check("operator answer clears the lead's open escalation",
                opens == [], f"still open: {opens}")
        # The lead now has an unread @mention (the operator's reply) → its Stop
        # surfaces that (wakes it to read the answer) rather than the P2 gate.
        r = hook("stop.py", env,
                 {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
                 timeout=20)
        obj = parse_hook_json(r.stdout)
        c.check("after the answer the lead wakes on the operator's reply",
                bool(obj and obj.get("decision") == "block"
                     and "option A" in obj.get("reason", "")),
                obj.get("reason", "")[:160] if obj else "")


def test_team_releases_after_answer_read_and_all_done(c):
    """Once the escalation is answered+read and both agents are done, the barrier
    finally releases the whole team together."""
    with tmp_root() as root:
        env, mid = _setup(root)
        cli(["answer", mid, "go"], env)
        # lead reads the operator reply (clears the mention), then both mark done.
        cli(["read", "--from", "ada"], env)
        cli(["done", "--from", "turing"], env)
        cli(["done", "--from", "ada"], env)
        # lead's Stop now: no escalation, empty inbox, team all done → allowed.
        r = hook("stop.py", env,
                 {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
                 timeout=20)
        c.check("team tears down once escalation answered+read and all done",
                '"decision"' not in r.stdout, r.stdout[:160])
        c.check("stop exits 0", r.returncode == 0, r.stderr)


def test_codespan_human_is_not_a_phantom_escalation(c):
    """Regression for the phantom-escalation barrier-wedge (gauss #85, diagnosed
    #90, fixed by newton's code-span-aware `open_escalations`). A lead DISCUSSING
    the token in a code span — `@human` — must NOT self-create an escalation (it
    would wedge the team barrier), while a BARE `@human` still escalates (the fix
    must not over-correct and break real escalations)."""
    with tmp_root() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        conn = chat.connect()
        chat.register(conn, "s1", handle="ada")
        chat.set_lead(conn, "ada")
        chat.send(conn, "ada", "docs: quoting `@human` should not escalate", kind="chat")
        c.check("a lead's backticked `@human` does not create a phantom escalation",
                chat.open_escalations(conn, "ada") == [],
                f"phantom: {chat.open_escalations(conn, 'ada')}")
        bare = chat.send(conn, "ada", "operator, I need a real decision @human", kind="chat")
        c.check("a bare @human still escalates (fix didn't over-correct)",
                bare in chat.open_escalations(conn, "ada"),
                f"opens={chat.open_escalations(conn, 'ada')} expected {bare}")
        conn.close()


def main():
    c = Checker("barrier × leadership × P2-escalation (multi-agent E2E)")
    for fn in (test_worker_parks_while_lead_escalation_gated,
               test_lead_with_open_escalation_is_gated,
               test_operator_answer_clears_escalation,
               test_team_releases_after_answer_read_and_all_done,
               test_codespan_human_is_not_a_phantom_escalation):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
