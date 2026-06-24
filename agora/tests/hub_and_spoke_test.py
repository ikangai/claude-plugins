#!/usr/bin/env python3
"""End-to-end hub-and-spoke scenario test — the full human-in-the-loop.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/hub_and_spoke_test.py     # exit 0 = all pass

Where hierarchy_test.py pins the resolve_lead/send-guard *matrix* in isolation,
this walks the whole LOOP as a story, end to end, on current primitives:

    worker ──@human──▶ (rewritten) ──▶ lead        # the funnel
    lead   ──@human──────────────────▶ operator     # one escalation
    operator ──@<lead>──────────────▶ lead          # the reply lands (loop closes)
    lead   ──@<worker>───────────────▶ worker        # answer relayed

plus the SPOF-killer in motion: when the lead ages out, the funnel target
silently fails over to the floor (the next earliest-joined active agent), and an
explicit claim (meta['lead']) overrides the floor. These three — operator
reply-back, lead relay, and funnel failover — are the loop pieces the unit matrix
doesn't exercise.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHAT = os.path.join(ROOT, ".groupchat", "chat.py")

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        _failures.append(name)


def env_for(root, **extra):
    env = dict(os.environ)
    env["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("GROUPCHAT_LEAD", None)
    env.update(extra)
    return env


def run(args, env):
    return subprocess.run([sys.executable, CHAT, *args],
                          capture_output=True, text=True, env=env)


def db(root):
    return sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))


def register(env, handle):
    run(["register", "--session", f"s_{handle}", "--from", handle], env)


def mentions_of_last(root):
    row = db(root).execute(
        "SELECT mentions FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0] or "[]")


def age_out(root, handle, minutes=20):
    """Push an agent's last_seen into the past so it drops out of the active window
    (ACTIVE_WINDOW is 15 min) — simulates a parked/crashed lead for failover tests."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    c = db(root)
    c.execute("UPDATE agents SET last_seen=? WHERE handle=?", (past, handle))
    c.commit()
    c.close()


def set_pointer(root, handle):
    c = db(root)
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('lead', ?)", (handle,))
    c.commit()
    c.close()


# --------------------------------------------------------------------------- #
def test_full_loop_closes(root):
    """worker → lead → operator → lead → worker, end to end."""
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")     # earliest join → floor lead
    register(env, "bohr")    # worker
    register(env, "curie")   # worker

    # 1. two workers each ask the operator; both funnel to the lead
    run(["send", "--from", "bohr", "@human can we drop column X?"], env)
    check("funnel: bohr's @human → @ada", mentions_of_last(root) == ["ada"],
          mentions_of_last(root))
    run(["send", "--from", "curie", "@human is the schema frozen?"], env)
    check("funnel: curie's @human → @ada", mentions_of_last(root) == ["ada"],
          mentions_of_last(root))

    # 2. the lead batches + escalates ONE touchpoint to the operator
    run(["send", "--from", "ada", "@human two Qs from the team: drop X? schema frozen?"], env)
    check("escalation: lead's @human passes through to the operator",
          mentions_of_last(root) == ["human"], mentions_of_last(root))

    # 3. the operator replies (a human running the CLI directly); it reaches the lead
    run(["send", "--from", "human", "@ada yes to both"], env)
    inbox = run(["inbox", "--from", "ada"], env)
    check("reply-back: operator's @ada lands in the lead's inbox (loop closes)",
          "yes to both" in inbox.stdout, inbox.stdout)

    # 4. the lead relays the answer down to a worker
    run(["send", "--from", "ada", "@bohr operator says: yes, drop X"], env)
    binbox = run(["inbox", "--from", "bohr"], env)
    check("relay: worker bohr receives the lead's answer",
          "drop X" in binbox.stdout, binbox.stdout)


def test_funnel_fails_over_when_lead_ages_out(root):
    """The SPOF-killer in motion: lead parks/crashes → funnel moves to the floor."""
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")     # floor lead
    register(env, "bohr")
    register(env, "curie")

    run(["send", "--from", "bohr", "@human first question"], env)
    check("before failover: funnel → ada", mentions_of_last(root) == ["ada"],
          mentions_of_last(root))

    age_out(root, "ada")     # the lead goes silent (parked / crashed)
    run(["send", "--from", "curie", "@human after the lead went dark"], env)
    check("after failover: funnel auto-moves to the next floor (bohr)",
          mentions_of_last(root) == ["bohr"], mentions_of_last(root))
    # and the new floor lead can address the operator directly
    run(["send", "--from", "bohr", "@human taking over as lead"], env)
    check("after failover: new lead's @human passes through",
          mentions_of_last(root) == ["human"], mentions_of_last(root))


def test_explicit_claim_overrides_floor(root):
    """An explicit claim (meta['lead']) beats the floor — emergent self-promotion."""
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")     # floor would be ada
    register(env, "bohr")
    register(env, "curie")
    set_pointer(root, "curie")   # curie claims the lead role
    run(["send", "--from", "bohr", "@human who do I ask?"], env)
    check("claim: funnel → the claimed lead (curie), not the floor (ada)",
          mentions_of_last(root) == ["curie"], mentions_of_last(root))
    # the claimed lead owns the human channel
    run(["send", "--from", "curie", "@human I've got this"], env)
    check("claim: claimed lead's @human passes through",
          mentions_of_last(root) == ["human"], mentions_of_last(root))


def main():
    tests = [
        test_full_loop_closes,
        test_funnel_fails_over_when_lead_ages_out,
        test_explicit_claim_overrides_floor,
    ]
    for t in tests:
        print(f"\n# {t.__name__}")
        with tempfile.TemporaryDirectory() as root:
            try:
                t(root)
            except Exception as e:
                check(t.__name__ + " (no exception)", False, repr(e))
    print(f"\n{'=' * 50}")
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
