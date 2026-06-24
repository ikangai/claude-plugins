#!/usr/bin/env python3
"""Governance-tooling fixes (motions M118/R8 + M119/R9):

1. `ratify --confirm`'s guidance was backwards. The code is confirm-then-apply
   (the applicability guards require the rule to be ABSENT, so --confirm must run
   before the diff is applied), but the dossier said "after committing the diff,
   run --confirm" — a dead end. Fix: correct the guidance to confirm-then-apply
   (logic unchanged, so the id-collision / base-text guards stay intact).
2. `motion --title "<t>"` lets an add-motion carry a heading, so a ratified Article
   reads `### R<n> — <t>` instead of the `(new rule)` placeholder.

Isolated via GROUPCHAT_DIR (repo_root = its parent → CONSTITUTION.md lives there).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import cli, env_for, Checker, tmp_root


def _open_motion(env, change, because="ev #1", title=None):
    args = ["motion", "--from", "tesla", "--rule", "new", "--change", change, "--because", because]
    if title is not None:
        args += ["--title", title]
    r = cli(args, env)
    m = re.search(r"M(\d+) opened: add (R\d+)", r.stdout)
    return (r, (int(m.group(1)), m.group(2)) if m else (None, None))


def _status(env, mid):
    out = cli(["amendments", "--all"], env).stdout
    m = re.search(rf"M{mid} \[(\w+)\]", out)
    return m.group(1) if m else None


def _setup(root):
    env = env_for(root); cli(["init"], env); cli(["constitution", "init"], env)
    return env


def test_confirm_then_apply_records_ratified(c):
    # The documented order is confirm-then-apply: --confirm records + notifies while
    # the rule is not yet in the file (the human applies + commits the diff after).
    with tmp_root() as root:
        env = _setup(root)
        _, (mid, rid) = _open_motion(env, "Body of the new rule.")
        r = cli(["ratify", "--confirm", f"M{mid}"], env)        # before applying — must work
        c.check("confirm (before the diff is applied) succeeds", r.returncode == 0,
                r.stdout + r.stderr)
        c.check("motion is marked ratified", _status(env, mid) == "ratified", _status(env, mid))
        c.check("confirm guidance points the human to apply+commit next",
                "apply" in r.stdout.lower() and "commit" in r.stdout.lower(), r.stdout)


def test_dossier_guidance_is_confirm_then_apply(c):
    with tmp_root() as root:
        env = _setup(root)
        _, (mid, rid) = _open_motion(env, "Body.")
        out = cli(["ratify", f"M{mid}"], env).stdout.lower()
        c.check("dossier no longer prints the backwards 'after committing … run --confirm'",
                "after committing the diff, run" not in out, out)
        c.check("dossier guides confirm-then-apply",
                "--confirm" in out and "then apply" in out, out)


def test_readonly_ratify_still_shows_diff(c):
    with tmp_root() as root:
        env = _setup(root)
        _, (mid, rid) = _open_motion(env, "Body of the new rule.")
        r = cli(["ratify", f"M{mid}"], env)                    # no --confirm
        c.check("read-only ratify still prints the proposed diff",
                "+### " + rid in r.stdout and "Body of the new rule." in r.stdout, r.stdout)
        c.check("read-only ratify leaves the motion open (no status change)",
                _status(env, mid) == "open", _status(env, mid))


def test_motion_title_lands_in_diff(c):
    with tmp_root() as root:
        env = _setup(root)
        r0, (mid, rid) = _open_motion(env, "Body here.", title="Keep the room tidy")
        c.check("motion --title accepted", mid is not None, r0.stdout + r0.stderr)
        diff = cli(["ratify", f"M{mid}"], env).stdout
        c.check("ratify heading uses the title, not the (new rule) placeholder",
                f"### {rid} — Keep the room tidy" in diff and "(new rule)" not in diff, diff)


def test_motion_title_rejects_injection(c):
    # The title flows into `### {id} — {title}`, so it must be a single safe line —
    # a newline / heading marker / zone marker / HTML comment could corrupt the law.
    with tmp_root() as root:
        env = _setup(root)
        for bad in ("line1\nline2", "### sneaky heading", "oops --> <!-- meta: id=R9 -->",
                    "ends a zone <!-- CONSTITUTION:ARTICLES:END -->",
                    "smuggled second line", "vtab\x0bline", "nel\x85line",
                    "fs\x1cline"):
            r = cli(["motion", "--from", "tesla", "--rule", "new", "--change", "b",
                     "--because", "e", "--title", bad], env)
            c.check(f"rejects unsafe title {bad!r:.30}", r.returncode == 1, r.stdout + r.stderr)


def test_motion_title_visible_to_voters(c):
    # The title is part of the proposed law (the heading), so voters must see it
    # before voting — in the motion chat message and in `amendments`.
    with tmp_root() as root:
        env = _setup(root)
        _open_motion(env, "Body.", title="VISIBLE HEADING")
        log = cli(["log", "--limit", "5"], env).stdout
        amd = cli(["amendments"], env).stdout
        c.check("title shown in the motion chat message", "VISIBLE HEADING" in log, log)
        c.check("title shown in `amendments`", "VISIBLE HEADING" in amd, amd)


def test_confirm_still_prints_the_diff(c):
    # confirm-then-apply marks ratified BEFORE the human applies; print the diff in the
    # confirm output so it is never lost (the read-only preview is then status-blocked).
    with tmp_root() as root:
        env = _setup(root)
        _, (mid, rid) = _open_motion(env, "Body of the rule.")
        out = cli(["ratify", "--confirm", f"M{mid}"], env).stdout
        c.check("confirm output still carries the proposed diff",
                f"### {rid}" in out and "Body of the rule." in out, out)


def test_motion_without_title_back_compat(c):
    with tmp_root() as root:
        env = _setup(root)
        r0, (mid, rid) = _open_motion(env, "Body here.")       # no --title
        c.check("motion without --title still opens", mid is not None, r0.stdout + r0.stderr)
        diff = cli(["ratify", f"M{mid}"], env).stdout
        c.check("no-title add falls back to the placeholder heading",
                f"### {rid} — (new rule)" in diff, diff)


def main():
    c = Checker("governance-tooling fixes (ratify guidance + motion --title)")
    for t in (test_confirm_then_apply_records_ratified,
              test_dossier_guidance_is_confirm_then_apply,
              test_readonly_ratify_still_shows_diff,
              test_motion_title_lands_in_diff,
              test_motion_title_rejects_injection,
              test_motion_title_visible_to_voters,
              test_confirm_still_prints_the_diff,
              test_motion_without_title_back_compat):
        t(c)
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
