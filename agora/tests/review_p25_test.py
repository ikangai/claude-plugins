#!/usr/bin/env python3
"""P2.5 — drift-grep + diary-promotion (advisory review heuristics).

Two extra `review` outputs, both advisory (they change nothing):
  * DRIFT-GREP flags a live Article that references a repo path/symbol no longer
    present — a rule that has silently drifted out of sync with the code. Paths are
    checked by existence (glob-aware); code-like symbols by `git grep` (excluding
    CONSTITUTION.md so a rule naming its own deleted symbol still flags). Fail-safe:
    a non-git repo / grep problem never invents drift.
  * DIARY-PROMOTION surfaces `.dev-diary/` lessons (a `LESSON:` line, or any line
    with an `[evidence: #id]` token) as HYPOTHESIS motion candidates, marking whether
    a cited id is corroborated by a real bus message. Leads, never proof.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/review_p25_test.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, env_for, tmp_root  # noqa: E402


def _git(root, *args):
    subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def _insert_article(root, block):
    p = os.path.join(root, "CONSTITUTION.md")
    text = open(p).read()
    text = text.replace("<!-- CONSTITUTION:ARTICLES:END -->",
                        block + "\n<!-- CONSTITUTION:ARTICLES:END -->")
    with open(p, "w") as fh:
        fh.write(text)


def _review(root):
    return cli(["review"], env_for(root)).stdout


def main() -> int:
    c = Checker("P2.5 — drift-grep + diary-promotion")

    # ---- drift-grep: paths + symbols, in a real git repo ---------------------
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["constitution", "init"], env)
        # A tracked source file: one present path, one present symbol.
        with open(os.path.join(root, "present.py"), "w") as fh:
            fh.write("def present_symbol():\n    return 1\n")
        _insert_article(root,
            "### R3 — references\n"
            "Edit `present.py` before `gone/absent_dir/missing.py`. Use the\n"
            "`present_symbol` helper, not the old `vanished_symbol_zzz`.\n"
            "<!-- meta: id=R3 added=2026-08-12 by=human ratified=2026-08-12 -->\n")
        _git(root, "init"); _git(root, "add", "-A")
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")

        out = _review(root)
        c.check("drift section is present", "Drift flags" in out, out)
        c.check("a vanished PATH is flagged", "gone/absent_dir/missing.py" in out, out)
        c.check("a vanished SYMBOL is flagged", "vanished_symbol_zzz" in out, out)
        c.check("a present path is NOT flagged", "`present.py`" not in out.split(
            "Drift flags")[-1], out)
        c.check("a present symbol is NOT flagged", "present_symbol" not in out.split(
            "Drift flags")[-1], out)
        c.check("the drifting Article is named (R3)",
                "R3 — references" in out.split("Drift flags")[-1], out)

    # ---- no false drift on the seeded Articles, and fail-safe off-git --------
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["constitution", "init"], env)   # R1/R2 only; NOT a git repo
        out = _review(root)
        c.check("seeded R1/R2 raise no drift (no cry-wolf)",
                "Drift flags" in out and "(none)" in out.split("Drift flags")[-1], out)

    # ---- diary-promotion: corroboration + fallback + prose is ignored --------
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["constitution", "init"], env)
        cli(["send", "--from", "ada", "real evidence message"], env)  # -> bus id 1
        dd = os.path.join(root, ".dev-diary")
        os.makedirs(dd)
        with open(os.path.join(dd, "2026-08-12-x.md"), "w") as fh:
            fh.write(
                "# Session\n\n"
                "LESSON: claim the lexer before editing it [evidence: #1]\n"
                "LESSON: a caching hunch with no citation\n"
                "Inline prose mentioning [evidence: #99] a dead id.\n"
                "Just ordinary prose with no markers at all.\n")
        out = _review(root)
        c.check("promotion section appears", "Promotion candidates" in out, out)
        c.check("a LESSON corroborated by a real bus id is marked so",
                "claim the lexer" in out and "corroborated by bus msg #1" in out, out)
        c.check("an uncited LESSON is marked uncorroborated",
                "caching hunch" in out and "cite it on the bus" in out, out)
        c.check("a fallback [evidence:] line with a dead id is flagged not-on-bus",
                "#99 — not on this bus" in out, out)
        c.check("plain prose (no marker) is NOT surfaced",
                "ordinary prose" not in out, out)
        c.check("the hypothesis caveat is printed",
                "HYPOTHESIS" in out and "human ratify still required" in out, out)

    # ---- no diary at all -> review still works, no promotion section ---------
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["constitution", "init"], env)
        out = _review(root)
        c.check("review works with no diary (fail-open)",
                "Repeal candidates" in out and "Promotion candidates" not in out, out)

    return c.done()


if __name__ == "__main__":
    sys.exit(main())
