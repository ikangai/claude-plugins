#!/usr/bin/env python3
"""Autonomous enactment (P4) — the parliament applies its own passing motions.

Active ONLY when the constitution CORE declares ``PROCEDURE:autonomous`` (seeded
by ``constitution init --autonomous``; a human edit of Core is the only other
path — motions can't touch Core, so the parliament can never grant itself this).
The bar: supermajority of cast votes + quorum + voters from >= 2 distinct KNOWN
models, held through an objection window (``AGORA_ENACT_DELAY``); then the sweep
(vote / amendments / motion / enact — never a hook) writes the amendment and
audit-commits it. Human-ratified rooms must remain byte-identical.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/autonomy_test.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, env_for, tmp_root,
)

# Thresholds every call in this module uses: 2/3 supermajority, quorum 2,
# diversity 2, window 1h unless a case overrides AGORA_ENACT_DELAY.
BASE = dict(GROUPCHAT_AMEND_QUORUM="2", AGORA_ENACT_DELAY="3600")


def _law(root):
    with open(os.path.join(root, "CONSTITUTION.md")) as fh:
        return fh.read()


def _motion_row(root, mid):
    conn = db(root)
    try:
        return conn.execute("SELECT * FROM motions WHERE id=?", (mid,)).fetchone()
    finally:
        conn.close()


def _setup(root, autonomous=True, git=True):
    """An isolated room + constitution + three voters on two models."""
    env = env_for(root, **BASE)
    cli(["init"], env)
    if git:
        subprocess.run(["git", "-C", root, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", root, "-c", "user.email=t@t", "-c",
                        "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "root"], capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                       capture_output=True)
    args = ["constitution", "init"] + (["--autonomous"] if autonomous else [])
    cli(args, env)
    for sid, h, model in (("s1", "ada", "claude-opus-4-8"),
                          ("s2", "turing", "claude-opus-4-8"),
                          ("s3", "hopper", "claude-sonnet-5")):
        cli(["register", "--session", sid, "--from", h], env)
        cli(["model", model, "--session", sid], env)
    return env


def _open_motion(env):
    r = cli(["motion", "--from", "ada", "--rule", "R1",
             "--change", "Announce in chat AND set focus before touching a file.",
             "--because", "evidence: #1-#3"], env)
    for tok in r.stdout.split():
        if tok.startswith("M") and tok[1:].rstrip(":").isdigit():
            return int(tok[1:].rstrip(":")), r
    return None, r


def main() -> int:
    c = Checker("autonomous enactment (P4)")

    # ---- the seeded document ------------------------------------------------
    with tmp_root() as root:
        env = _setup(root, autonomous=True)
        law = _law(root)
        c.check("init --autonomous seeds the PROCEDURE marker in CORE",
                "CONSTITUTION:PROCEDURE:autonomous" in law)
        c.check("autonomous C1 replaces human-final-authority",
                "The parliament is sovereign" in law)
        r = cli(["constitution"], env)
        c.check("show labels the articles AUTONOMOUS",
                "AUTONOMOUS enactment" in r.stdout, r.stdout)

        # ---- window opens, nothing enacts early ----------------------------
        mid, r = _open_motion(env)
        c.check("motion opens (autonomous wording, no 'a human ratifies')",
                mid is not None and "a human ratifies" not in r.stdout, r.stdout)
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env)
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        c.check("vote says 'counts toward enactment' in autonomous rooms",
                "counts toward enactment" in r.stdout, r.stdout)
        c.check("passing motion is stamped (objection window opens)",
                "enactment bar" in r.stdout or "objection window" in r.stdout,
                r.stdout)
        c.check("window not expired -> law unchanged",
                "Announce in chat AND set focus" not in _law(root))
        c.check("motion still open during the window",
                _motion_row(root, mid)["status"] == "open")
        r = cli(["amendments"], env)
        c.check("amendments shows the pending window",
                "enacts in ~" in r.stdout, r.stdout)

        # ---- objection: a nay that breaks the bar cancels the stamp --------
        cli(["vote", "--session", "s2", f"M{mid}", "nay"], env)  # 2/3 = .66 < .67? no: 2/3 >= .66 passes
        # 2 yea / 1 nay = 66.7% -> still passing at the 0.66 default. Push below:
        r = cli(["vote", "--session", "s3", f"M{mid}", "nay"], env)  # 1 yea / 2 nay
        c.check("tally breaking the bar cancels enactment",
                "cancelled" in r.stdout, r.stdout)
        c.check("stamp cleared in the db",
                _motion_row(root, mid)["passed_at"] is None)
        # Re-pass and let the window expire (delay 0 for the rest of the case).
        cli(["vote", "--session", "s2", f"M{mid}", "yea"], env)
        cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        env0 = env_for(root, **{**BASE, "AGORA_ENACT_DELAY": "0"})
        r = cli(["enact"], env0)
        c.check("expired window -> enact sweep ENACTS", "ENACTED" in r.stdout, r.stdout)
        law = _law(root)
        c.check("the law file changed",
                "Announce in chat AND set focus" in law)
        c.check("provenance records the parliament + motion",
                "by=parliament" in law and f"source=M{mid}" in law)
        c.check("motion status is 'enacted'",
                _motion_row(root, mid)["status"] == "enacted")
        gl = subprocess.run(["git", "-C", root, "log", "--oneline", "-1"],
                            capture_output=True, text=True)
        c.check("enactment is audit-committed",
                f"enact M{mid}" in gl.stdout, gl.stdout)
        conn = db(root)
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE kind='system' "
                         "AND body LIKE '%ENACTED%'").fetchone()[0]
        conn.close()
        c.check("system message announces the enactment", n >= 1)
        r = cli(["ratify", f"M{mid}"], env)
        c.check("ratify refuses an already-enacted motion",
                r.returncode == 1 and "enacted" in r.stderr, r.stderr)

    # ---- diversity wall: a single-model sweep never enacts ------------------
    with tmp_root() as root:
        env = _setup(root, autonomous=True)
        mid, _ = _open_motion(env)
        env0 = env_for(root, **{**BASE, "AGORA_ENACT_DELAY": "0"})
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env0)  # opus
        r = cli(["vote", "--session", "s2", f"M{mid}", "yea"], env0)  # opus again
        c.check("single-model supermajority does NOT stamp",
                "objection window" not in r.stdout and
                _motion_row(root, mid)["passed_at"] is None, r.stdout)
        r = cli(["enact"], env0)
        c.check("enact sweep refuses a single-model motion",
                "ENACTED" not in r.stdout, r.stdout)
        c.check("law untouched", "Announce in chat AND set focus" not in _law(root))
        # The third voter is a different model -> diversity satisfied; at delay 0
        # the same vote's sweep stamps AND enacts in one pass.
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env0)
        c.check("cross-model third vote unlocks enactment",
                "ENACTED" in r.stdout, r.stdout)

    # ---- TOCTOU: base text changed under the motion -> lapse, not misapply --
    with tmp_root() as root:
        env = _setup(root, autonomous=True)
        mid, _ = _open_motion(env)
        p = os.path.join(root, "CONSTITUTION.md")
        with open(p) as fh:
            txt = fh.read()
        with open(p, "w") as fh:
            fh.write(txt.replace("Post \"starting on <path>\" before editing",
                                 "Post loudly before editing"))
        env0 = env_for(root, **{**BASE, "AGORA_ENACT_DELAY": "0"})
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env0)
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env0)
        c.check("changed base text -> motion lapses instead of misapplying",
                "lapsed" in r.stdout and
                _motion_row(root, mid)["status"] == "lapsed", r.stdout)
        c.check("lapsed motion leaves the law alone",
                "Announce in chat AND set focus" not in _law(root))

    # ---- Core stays out of reach even in autonomous rooms -------------------
    with tmp_root() as root:
        env = _setup(root, autonomous=True)
        r = cli(["motion", "--from", "ada", "--rule", "C1",
                 "--change", "The parliament may amend its Core.",
                 "--because", "power grab"], env)
        c.check("motion against Core is still rejected", r.returncode != 0,
                r.stdout + r.stderr)

    # ---- ratify --confirm = early enactment (writes the file) ---------------
    with tmp_root() as root:
        env = _setup(root, autonomous=True)
        mid, _ = _open_motion(env)
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env)
        cli(["vote", "--session", "s3", f"M{mid}", "yea"], env)
        r = cli(["ratify", f"M{mid}", "--confirm"], env)  # bare invocation = operator
        c.check("ratify --confirm enacts early in autonomous rooms",
                "enacted" in r.stdout and
                "Announce in chat AND set focus" in _law(root), r.stdout + r.stderr)

    # ---- no git repo: the law still changes, honestly reported --------------
    with tmp_root() as root:
        env = _setup(root, autonomous=True, git=False)
        mid, _ = _open_motion(env)
        env0 = env_for(root, **{**BASE, "AGORA_ENACT_DELAY": "0"})
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env0)
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env0)
        c.check("git-less room: enactment still lands",
                "ENACTED" in r.stdout and
                "Announce in chat AND set focus" in _law(root), r.stdout)
        c.check("git-less room: audit-commit failure is reported, not hidden",
                "audit commit failed" in r.stderr, r.stderr)

    # ---- human-ratified rooms are byte-identical ----------------------------
    with tmp_root() as root:
        env = _setup(root, autonomous=False)
        mid, r = _open_motion(env)
        c.check("human room: motion wording still advisory",
                "a human ratifies" in r.stdout, r.stdout)
        env0 = env_for(root, **{**BASE, "AGORA_ENACT_DELAY": "0"})
        cli(["vote", "--session", "s1", f"M{mid}", "yea"], env0)
        r = cli(["vote", "--session", "s3", f"M{mid}", "yea"], env0)
        c.check("human room: vote stays '(advisory)', nothing stamps",
                "(advisory)" in r.stdout and "ENACTED" not in r.stdout, r.stdout)
        r = cli(["enact"], env0)
        c.check("human room: enact explains and does nothing",
                "human-ratified procedure" in r.stdout, r.stdout)
        c.check("human room: law untouched",
                "Announce in chat AND set focus" not in _law(root))
        c.check("human room: motion still open",
                _motion_row(root, mid)["status"] == "open")

    return c.done()


if __name__ == "__main__":
    sys.exit(main())
