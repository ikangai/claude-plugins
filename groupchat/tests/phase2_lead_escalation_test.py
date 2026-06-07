#!/usr/bin/env python3
"""Phase 2 — lead-side escalation gating in the Stop hook.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/phase2_lead_escalation_test.py

The substrate routes worker→lead and lead→operator; this is the one piece P2 adds
(spec pinned in chat #39): a lead that has an outstanding @human escalation must
NOT let the team tear down — it parks until an operator message @mentions it
(which clears the escalation), reusing the existing @mention-block; the park
ceiling still releases it as a fail-safe. Workers are structurally never gated:
their @human is rewritten to @<lead> before storage, so they never own an
escalation.

Cases A/B exercise the gate (RED until open_escalations() + the stop.py wiring
land in newton's chat.py window); C guards against the gate over-triggering and
is green throughout.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, env_for, hook, parse_hook_json, tmp_root  # noqa: E402

STOP = {"hook_event_name": "Stop", "stop_hook_active": False}


def test_lead_with_open_escalation_parks(c):
    """A lone agent is its own floor lead. After it escalates @human, its Stop must
    BLOCK (park) even though the barrier (team_size=1) would otherwise release it —
    the operator hasn't answered yet."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_PARK_WINDOW=0,
                      GROUPCHAT_POLL_TICK=0.1, GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)   # lone → floor lead
        cli(["send", "--from", "ada", "@human ok to ship?"], env)     # escalation
        r = hook("stop.py", env, {"session_id": "s1", **STOP}, timeout=20)
        obj = parse_hook_json(r.stdout)
        c.check("[P2] lead with an open @human escalation parks instead of exiting",
                bool(obj and obj.get("decision") == "block"), f"got {r.stdout[:200]!r}")
        c.check("[P2] the park reason names the operator/escalation",
                "operator" in (obj or {}).get("reason", "").lower()
                or "escalat" in (obj or {}).get("reason", "").lower(),
                (obj or {}).get("reason", "")[:200])
        c.check("stop still exits 0 (fail-open invariant)", r.returncode == 0, r.stderr)


def test_lead_escalation_cleared_allows_exit(c):
    """Once the operator @mentions the lead (answering), and the lead has read the
    reply, the escalation is cleared and the lead may finish."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_PARK_WINDOW=0,
                      GROUPCHAT_POLL_TICK=0.1, GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["send", "--from", "ada", "@human ok to ship?"], env)     # escalation
        cli(["send", "--from", "human", "@ada yes, ship it"], env)    # operator answers
        cli(["read", "--from", "ada"], env)                           # lead reads the reply
        r = hook("stop.py", env, {"session_id": "s1", **STOP}, timeout=20)
        c.check("[P2] an answered+read escalation lets the lead exit (no block)",
                '"decision"' not in r.stdout, r.stdout[:200])
        c.check("stop exits 0", r.returncode == 0, r.stderr)


def test_lead_without_escalation_exits_normally(c):
    """Regression guard: a lead that never escalated is NOT gated — the new check
    must not hold a finished lead with an empty @human queue."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_PARK_WINDOW=0,
                      GROUPCHAT_POLL_TICK=0.1, GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = hook("stop.py", env, {"session_id": "s1", **STOP}, timeout=20)
        c.check("lead with no escalation exits normally (no false gate)",
                '"decision"' not in r.stdout, r.stdout[:200])


def test_awaiting_lead_holds_the_whole_team(c):
    """Multi-agent team-hold: a lead awaiting the operator keeps the WHOLE team
    parked even when every worker has finished — the barrier never tears down with
    a question to the human still open. (Lone-lead cases above isolate the gate;
    this is the real use case the gate exists for.)"""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=2, GROUPCHAT_PARK_WINDOW=0,
                      GROUPCHAT_POLL_TICK=0.1, GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)   # floor lead
        cli(["register", "--session", "s2", "--from", "bohr"], env)  # worker
        cli(["send", "--from", "ada", "@human blocking question?"], env)  # lead escalates
        ra = hook("stop.py", env, {"session_id": "s1", **STOP}, timeout=20)
        oa = parse_hook_json(ra.stdout)
        c.check("the awaiting lead parks (not done)",
                bool(oa and oa.get("decision") == "block"), ra.stdout[:200])
        # A finished worker cannot tear the team down while the lead isn't done.
        rb = hook("stop.py", env, {"session_id": "s2", **STOP}, timeout=20)
        ob = parse_hook_json(rb.stdout)
        c.check("a finished worker still parks at the barrier while the lead awaits",
                bool(ob and ob.get("decision") == "block"), rb.stdout[:200])


def test_quoted_token_does_not_wedge_the_barrier(c):
    """gauss #85: a LEAD discussing the escalation token must not create a phantom
    escalation that gates itself and wedges the team barrier. A backticked `@human`
    in the lead's own message is documentation, not a question — open_escalations
    must be code-span aware (not just the send-guard). Bare still escalates."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=1, GROUPCHAT_PARK_WINDOW=0,
                      GROUPCHAT_POLL_TICK=0.1, GROUPCHAT_MAX_PARK=3600)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)   # lone → floor lead
        cli(["send", "--from", "ada",
             "cross-CLI note: a worker writes `@human` and it routes to the lead"], env)
        q = cli(["questions"], env)
        c.check("quoted `@human` by the lead is NOT a phantom escalation",
                "no open escalation" in q.stdout.lower(), q.stdout)
        # ...and such a lead is NOT barrier-wedged: it can reach done and exit.
        r = hook("stop.py", env, {"session_id": "s1", **STOP}, timeout=20)
        c.check("a lead that only DISCUSSED the token can still exit (no wedge)",
                '"decision"' not in r.stdout, r.stdout[:200])
        # a bare escalation still gates, proving the fix didn't disable the feature
        cli(["send", "--from", "ada", "@human real question?"], env)
        q2 = cli(["questions"], env)
        c.check("a bare @human by the lead still escalates",
                "real question" in q2.stdout, q2.stdout)


def test_handoff_orphans_escalation_is_documented(c):
    """DOCUMENTED limitation (tesla's review #68): a leadership *handoff* while an
    @human escalation is pending leaves it un-gated — the new lead owns the channel
    and is not held by the old lead's question, and the operator view (which tracks
    the current lead) no longer surfaces it. Accepted over a room-wide gate because
    the common leadership-change is failover (the asker ages out → the question is
    inherently best-effort anyway); the message itself still sits in `log`. Pinned
    so a future room-wide gate is a conscious change, not an accidental regression."""
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_TEAM_SIZE=2)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "ada"], env)   # floor lead
        cli(["register", "--session", "s2", "--from", "bohr"], env)
        cli(["send", "--from", "ada", "@human pending question?"], env)  # ada escalates
        q1 = cli(["questions"], env)
        c.check("before handoff: the lead's escalation is visible to the operator",
                "pending question" in q1.stdout, q1.stdout)
        cli(["lead", "bohr"], env)   # hand the lead to bohr while ada's q is open
        q2 = cli(["questions"], env)
        c.check("[documented] after handoff the orphaned escalation drops from the "
                "current-lead view (known limitation, not a room-wide gate)",
                "no open escalation" in q2.stdout.lower(), q2.stdout)


def main():
    c = Checker("phase 2 — lead escalation gating (stop hook)")
    for t in (test_lead_with_open_escalation_parks,
              test_lead_escalation_cleared_allows_exit,
              test_lead_without_escalation_exits_normally,
              test_awaiting_lead_holds_the_whole_team,
              test_quoted_token_does_not_wedge_the_barrier,
              test_handoff_orphans_escalation_is_documented):
        try:
            t(c)
        except Exception as e:
            c.check(t.__name__ + " (no exception)", False, repr(e))
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
