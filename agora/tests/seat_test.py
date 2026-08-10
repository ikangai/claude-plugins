#!/usr/bin/env python3
"""The seat (P5) — identity hardening for the binding parliament.

Closes the blindspots of docs/plans/2026-08-10-parliament-seat-identity-memory-
blindspot.md:

  B1 document-sovereign thresholds (the BAR marker beats any caller's env),
  B2 presence-weighted tally + agents_gone tombstones (no ghost electorate),
  B3 voter model frozen at cast (no post-vote diversity manufacturing),
  B4 serialized sweeps (enact.lock),
  B5 the stamp is a real @team broadcast that never self-cites,
  B6 the operator's `object` verb (clears the window; re-pass required),
  B7 proposer_session + session-keyed cites (attribution survives recycling).

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/seat_test.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, env_for, tmp_root,
)

OLD = "2000-01-01T00:00:00Z"


def _law(root):
    with open(os.path.join(root, "CONSTITUTION.md")) as fh:
        return fh.read()


def _set_bar(root, bar):
    """Replace (or strip, bar=None) the seeded BAR line in the document."""
    p = os.path.join(root, "CONSTITUTION.md")
    with open(p) as fh:
        txt = fh.read()
    seeded = re.search(r"<!--\s*CONSTITUTION:BAR:[^>]*-->\n?", txt).group(0)
    txt = txt.replace(seeded, (bar + "\n") if bar else "")
    with open(p, "w") as fh:
        fh.write(txt)


def _setup(root, bar="keep"):
    """Autonomous room + three voters on two models. bar: 'keep' the seeded
    defaults, None to strip (env fallback), or a replacement marker line."""
    env = env_for(root)
    cli(["init"], env)
    cli(["constitution", "init", "--autonomous"], env)
    if bar != "keep":
        _set_bar(root, bar)
    for sid, h, model in (("s1", "ada", "claude-opus-4-8"),
                          ("s2", "turing", "claude-opus-4-8"),
                          ("s3", "hopper", "claude-sonnet-5")):
        cli(["register", "--session", sid, "--from", h], env)
        cli(["model", model, "--session", sid], env)
    return env


def _open_motion(env):
    r = cli(["motion", "--from", "ada", "--rule", "R1",
             "--change", "Announce loudly, then edit.",
             "--because", "evidence: #1"], env)
    for tok in r.stdout.split():
        if tok.startswith("M") and tok[1:].rstrip(":").isdigit():
            return int(tok[1:].rstrip(":"))
    return None


def _motion_row(root, mid):
    conn = db(root)
    try:
        return conn.execute("SELECT * FROM motions WHERE id=?", (mid,)).fetchone()
    finally:
        conn.close()


def _age_out(root, sid):
    conn = db(root)
    conn.execute("UPDATE agents SET last_seen=? WHERE session_id=?", (OLD, sid))
    conn.commit()
    conn.close()


def main() -> int:
    c = Checker("the seat (P5)")

    # ---- B1: the document's BAR beats any caller's env ----------------------
    with tmp_root() as root:
        env = _setup(root)  # seeded BAR: quorum=3 window=3600 stated IN the law
        mid = _open_motion(env)
        attacker = env_for(root, GROUPCHAT_AMEND_QUORUM="1",
                           AGORA_ENACT_DIVERSITY="1", AGORA_ENACT_DELAY="0")
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], attacker)
        r = cli(["enact"], attacker)
        c.check("B1: env quorum=1 cannot beat the document's quorum=3",
                "ENACTED" not in r.stdout and
                "Announce loudly" not in _law(root), r.stdout)
        cli(["vote", "--session", "s2", f"M{mid}", "yea"], attacker)
        cli(["vote", "--session", "s3", f"M{mid}", "yea"], attacker)
        r = cli(["enact"], attacker)
        c.check("B1: env delay=0 cannot beat the document's window=3600 "
                "(passing motion stays pending)",
                "ENACTED" not in r.stdout and "enacts in ~" in r.stdout, r.stdout)

    # ---- B2: presence-weighted tally + tombstones ---------------------------
    with tmp_root() as root:
        env = _setup(root, bar="<!-- CONSTITUTION:BAR: supermajority=0.66 "
                               "quorum=2 diversity=2 -->")
        mid = _open_motion(env)
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env)
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        c.check("B2: two present cross-model voters stamp the window",
                "objection window" in r.stdout, r.stdout)
        _age_out(root, "s3")
        r = cli(["enact"], env)
        c.check("B2: a voter leaving the chamber breaks the bar (window cancelled)",
                "cancelled" in r.stdout and
                _motion_row(root, mid)["passed_at"] is None, r.stdout)
        r = cli(["amendments"], env)
        c.check("B2: the departed vote is named, not hidden",
                "departed vote(s) not counted" in r.stdout, r.stdout)
        cli(["register", "--session", "s3"], env)  # returns to the chamber
        r = cli(["enact"], env)
        c.check("B2: a returning voter's vote counts again (re-stamps, no re-cast)",
                "objection window" in r.stdout, r.stdout)

        # Tombstone: recycle a handle, the old identity stays auditable.
        _age_out(root, "s2")
        cli(["register", "--session", "s2b", "--from", "turing"], env)
        conn = db(root)
        gone = conn.execute("SELECT * FROM agents_gone WHERE session_id='s2'").fetchone()
        conn.close()
        c.check("B2: recycling tombstones the old identity (agents_gone)",
                gone is not None and gone["handle"] == "turing"
                and gone["model"] == "claude-opus-4-8",
                str(dict(gone) if gone else None))

    # ---- B3: the model is frozen at cast ------------------------------------
    with tmp_root() as root:
        env = _setup(root, bar=None)  # env-driven thresholds for this case
        e0 = env_for(root, GROUPCHAT_AMEND_QUORUM="2", AGORA_ENACT_DELAY="0")
        mid = _open_motion(env)
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], e0)
        r = cli(["vote", "--session", "s2", f"M{mid}", "yea"], e0)  # both opus
        c.check("B3: single-model pair does not enact", "ENACTED" not in r.stdout,
                r.stdout)
        cli(["model", "claude-sonnet-5", "--session", "s2"], e0)  # post-vote switch
        r = cli(["enact"], e0)
        c.check("B3: a post-vote model change cannot manufacture diversity "
                "(vote stays frozen at cast)",
                "ENACTED" not in r.stdout and
                "Announce loudly" not in _law(root), r.stdout)
        r = cli(["vote", "--session", "s2", f"M{mid}", "yea"], e0)  # re-affirm
        c.check("B3: re-casting under the new model is the legitimate unlock",
                "ENACTED" in r.stdout, r.stdout)

    # ---- B4: sweeps are serialized by enact.lock ----------------------------
    with tmp_root() as root:
        env = _setup(root, bar="<!-- CONSTITUTION:BAR: supermajority=0.66 "
                               "quorum=2 diversity=2 window=0 -->")
        mid = _open_motion(env)
        lock = os.path.join(root, ".groupchat", "enact.lock")
        with open(lock, "w") as fh:
            fh.write("999999")
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env)
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        c.check("B4: a held lock blocks enactment entirely",
                "ENACTED" not in r.stdout and
                "Announce loudly" not in _law(root), r.stdout)
        os.utime(lock, (1, 1))  # ancient mtime -> stale
        r = cli(["enact"], env)
        c.check("B4: a stale lock (crashed sweeper) is broken and the sweep runs",
                "ENACTED" in r.stdout and "Announce loudly" in _law(root), r.stdout)
        c.check("B4: the lock is released after the sweep",
                not os.path.exists(lock))

    # ---- B5: the stamp is a real broadcast and never self-cites -------------
    with tmp_root() as root:
        env = _setup(root, bar="<!-- CONSTITUTION:BAR: supermajority=0.66 "
                               "quorum=2 diversity=2 window=3600 -->")
        mid = _open_motion(env)
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env)
        cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        conn = db(root)
        stamp = conn.execute("SELECT * FROM messages WHERE body LIKE "
                             "'%holds the enactment bar%' "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        cites = conn.execute("SELECT COUNT(*) FROM rule_cites "
                             "WHERE sender='system'").fetchone()[0]
        conn.close()
        ment = stamp["mentions"] if stamp else "[]"
        c.check("B5: the stamp is kind='chat' from system with real mentions",
                stamp is not None and stamp["kind"] == "chat"
                and "ada" in ment and "turing" in ment and "hopper" in ment, ment)
        c.check("B5: the stamp names R1 yet registers no cite (system never cites)",
                cites == 0)

    # ---- B6: the operator's objection ---------------------------------------
    with tmp_root() as root:
        env = _setup(root, bar="<!-- CONSTITUTION:BAR: supermajority=0.66 "
                               "quorum=2 diversity=2 window=3600 -->")
        mid = _open_motion(env)
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env)
        cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        r = cli(["object", f"M{mid}", "--from", "hopper"], env)
        c.check("B6: a worker cannot object (votes nay instead)",
                r.returncode == 1 and "voting nay" in r.stderr, r.stderr)
        r = cli(["object", f"M{mid}", "needs", "a", "human", "read"], env)
        c.check("B6: the operator's objection clears the window",
                r.returncode == 0 and
                _motion_row(root, mid)["passed_at"] is None, r.stdout + r.stderr)
        conn = db(root)
        obj = conn.execute("SELECT * FROM messages WHERE body LIKE '%OBJECTION%' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        c.check("B6: the objection is broadcast to the team",
                obj is not None and obj["kind"] == "chat"
                and "needs a human read" in obj["body"],
                obj["body"] if obj else "(none)")
        c.check("B6: the motion stays open (re-pass allowed, not killed)",
                _motion_row(root, mid)["status"] == "open")

    # ---- B7: attribution survives recycling ---------------------------------
    with tmp_root() as root:
        env = _setup(root, bar=None)
        mid = _open_motion(env)
        c.check("B7: the motion records its proposer's session",
                _motion_row(root, mid)["proposer_session"] == "s1")
        # Cite era 1, recycle the handle, cite era 2 -> two distinct identities.
        cli(["send", "--from", "ada", "--session", "s1", "following R2 here"], env)
        _age_out(root, "s1")
        cli(["register", "--session", "s1b", "--from", "ada"], env)
        cli(["send", "--from", "ada", "--session", "s1b", "R2 again, new era"], env)
        r = cli(["review"], env)
        m = re.search(r"R2[^\n]*\((\d+) cites?\)", r.stdout)
        c.check("B7: cites key by session — a recycled handle is two identities, "
                "not one", m is not None and m.group(1) == "2", r.stdout)

    return c.done()


if __name__ == "__main__":
    sys.exit(main())
