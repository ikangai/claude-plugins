#!/usr/bin/env python3
"""Leadership WRITE-path tests — the twin of hierarchy_test.py (READ side).

Drives the real `chat.py lead` command (claim / designate / hand-off / release)
via subprocess, exactly as an agent would, and verifies it (a) sets the canonical
meta['lead'] pointer, (b) drives resolve_lead so @human routing follows it,
(c) posts a system announcement on each change, (d) shows the ★lead crown in
`who` only for a DELIBERATE lead (never the implicit floor), and (e) rejects the
reserved handle / missing identity. Isolated via GROUPCHAT_DIR; stdlib only.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import datetime

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
    c = sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))
    c.row_factory = sqlite3.Row
    return c


def register(env, handle):
    run(["register", "--session", f"s_{handle}", "--from", handle], env)


def pointer(root):
    r = db(root).execute("SELECT value FROM meta WHERE key='lead'").fetchone()
    return r["value"] if r else None


def last_system(root):
    r = db(root).execute(
        "SELECT body FROM messages WHERE kind='system' ORDER BY id DESC LIMIT 1").fetchone()
    return r["body"] if r else ""


def age_out(root, handle, minutes=30):
    """Push an agent's last_seen past the 15-min active window."""
    ts = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = db(root)
    c.execute("UPDATE agents SET last_seen=? WHERE handle=?", (ts, handle))
    c.commit(); c.close()


# --------------------------------------------------------------------------- #
def test_show_floor_default(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")   # earliest-joined → the floor
    register(env, "bohr")
    r = run(["lead"], env)
    check("show: floor lead is the earliest-joined (ada)", "@ada" in r.stdout, r.stdout)
    check("show: labelled as the emergent floor default",
          "floor" in r.stdout.lower(), r.stdout)
    check("show: no pointer written by a mere show", pointer(root) is None, pointer(root))


def test_claim_self(root):
    env = env_for(root)
    run(["init"], env); register(env, "ada"); register(env, "bohr")
    r = run(["lead", "--claim", "--from", "bohr"], env)
    check("claim: stdout confirms bohr", r.returncode == 0 and "@bohr" in r.stdout, r.stdout)
    check("claim: meta['lead'] = bohr", pointer(root) == "bohr", pointer(root))
    check("claim: system announcement posted", "@bohr is now the lead" in last_system(root),
          last_system(root))
    # routing now follows the claim: a worker's @human → @bohr
    run(["send", "--from", "ada", "@human is the schema final?"], env)
    row = db(root).execute("SELECT mentions FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    check("claim: worker @human routes to the claimed lead (bohr)",
          "bohr" in json.loads(row["mentions"]), row["mentions"])


def test_designate_and_handoff(root):
    env = env_for(root)
    run(["init"], env); register(env, "ada"); register(env, "bohr")
    run(["lead", "--claim", "--from", "ada"], env)
    r = run(["lead", "bohr"], env)            # hand off to bohr
    check("handoff: stdout confirms bohr", "@bohr" in r.stdout, r.stdout)
    check("handoff: meta moved to bohr", pointer(root) == "bohr", pointer(root))


def test_release_back_to_floor(root):
    env = env_for(root)
    run(["init"], env); register(env, "ada"); register(env, "bohr")
    run(["lead", "--claim", "--from", "bohr"], env)
    r = run(["lead", "--release"], env)
    check("release: rc 0", r.returncode == 0, r.stderr)
    check("release: meta['lead'] cleared", pointer(root) is None, pointer(root))
    check("release: system announces it", "released" in last_system(root).lower(),
          last_system(root))
    r2 = run(["lead"], env)
    check("release: show falls back to the floor (ada)",
          "@ada" in r2.stdout and "floor" in r2.stdout.lower(), r2.stdout)


def test_crown_only_for_deliberate_lead(root):
    env = env_for(root)
    run(["init"], env); register(env, "ada"); register(env, "bohr")
    # floor-only: who must NOT show a crown (flat rooms stay uncluttered)
    r = run(["who"], env)
    check("crown: floor-only room shows NO ★lead", "★lead" not in r.stdout, r.stdout)
    # explicit claim: who shows ★lead on the lead
    run(["lead", "--claim", "--from", "bohr"], env)
    r2 = run(["who"], env)
    check("crown: explicit lead shows ★lead on bohr",
          "bohr ★lead" in r2.stdout, r2.stdout)
    check("crown: non-lead ada has no crown",
          "ada ★lead" not in r2.stdout, r2.stdout)


def test_designated_lead_failover_note(root):
    env = env_for(root)
    run(["init"], env); register(env, "ada"); register(env, "bohr")
    run(["lead", "--claim", "--from", "bohr"], env)
    age_out(root, "bohr")                      # the claimed lead crashes/parks
    r = run(["lead"], env)
    check("failover: show falls over to the floor (ada)", "@ada" in r.stdout, r.stdout)
    check("failover: show notes the designated lead is inactive",
          "inactive" in r.stdout.lower(), r.stdout)
    check("failover: pointer NOT auto-cleared (bohr)", pointer(root) == "bohr", pointer(root))
    rw = run(["who"], env)
    check("failover: no crown while the designated lead is inactive",
          "★lead" not in rw.stdout, rw.stdout)


def test_reserved_and_identity_guards(root):
    env = env_for(root)
    run(["init"], env); register(env, "ada")
    r = run(["lead", "human"], env)
    check("guard: 'human' rejected as lead", r.returncode == 1, r.stdout + r.stderr)
    r2 = run(["lead", "--claim"], env)         # no --from / --session
    check("guard: --claim without identity errors", r2.returncode == 1, r2.stdout + r2.stderr)


def test_designate_requires_active_agent(root):
    # A lead must be an active agent — a pointer to an inactive/unknown handle
    # silently falls through to the floor, so designating one must be refused with no
    # misleading 'route to @<h>' broadcast (audit #70). --claim is self-active so OK.
    env = env_for(root)
    run(["init"], env); register(env, "ada"); register(env, "bohr")
    r = run(["lead", "zeta"], env)             # nonexistent handle
    check("designate nonexistent handle is refused", r.returncode == 1, r.stdout + r.stderr)
    check("refusal writes no pointer", pointer(root) is None, str(pointer(root)))
    check("refusal posts no misleading system broadcast", last_system(root) == "",
          last_system(root))
    register(env, "curie"); age_out(root, "curie")
    r2 = run(["lead", "curie"], env)           # inactive registered handle
    check("designate inactive registered handle is refused", r2.returncode == 1,
          r2.stdout + r2.stderr)
    r3 = run(["lead", "bohr"], env)            # active handle still works
    check("designate active handle works", r3.returncode == 0 and pointer(root) == "bohr",
          r3.stdout + r3.stderr)


TESTS = [
    test_show_floor_default,
    test_claim_self,
    test_designate_and_handoff,
    test_release_back_to_floor,
    test_crown_only_for_deliberate_lead,
    test_designated_lead_failover_note,
    test_reserved_and_identity_guards,
    test_designate_requires_active_agent,
]


def main():
    print("\n=== leadership WRITE path (claim / designate / handoff / release / crown) ===")
    for t in TESTS:
        with tempfile.TemporaryDirectory(prefix="gc_leadwrite_") as root:
            t(root)
    if _failures:
        print(f"--- leadership write: {len(_failures)} FAILED "
              f"({', '.join(_failures)})")
        return 1
    print("--- leadership write: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
