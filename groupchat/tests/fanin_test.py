#!/usr/bin/env python3
"""Phase-2 fan-in — structured results, a room summary, and worktree reconciliation.

Phase 1 made the room a coordinator (tasks/assign/goal); Phase 2 closes the loop back
to the orchestrator so it doesn't have to prose-grep the chat:

  * ``result --from <h> "…" [--task N]`` posts a ``kind='result'`` message (no
    @mention → never blocks a teammate's Stop or wedges the barrier); ``--task N``
    also closes that task. ``results`` is the structured read-side query.
  * ``summary`` is a read-only digest: goal + roster + task tally + results in one view.
  * ``worktrees`` (alias ``harvest``) is a read-only, DIFF-ONLY reconciliation for
    ``bootstrap --worktree`` runs: each ``groupchat/<name>`` branch's ahead/behind +
    changed files, with cross-branch file-overlap warnings and a suggested merge order.
    It never merges anything.

Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/fanin_test.py
"""
import json
import os
import subprocess as sp
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, init_room, tmp_root,
)


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def _messages(root, kind=None):
    conn = db(root)
    try:
        q = "SELECT id, sender, kind, body, mentions FROM messages"
        args = ()
        if kind:
            q += " WHERE kind = ?"; args = (kind,)
        q += " ORDER BY id"
        return conn.execute(q, args).fetchall()
    finally:
        conn.close()


def _tasks(root):
    conn = db(root)
    try:
        return conn.execute("SELECT id, owner, status FROM tasks ORDER BY id").fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# result / results
# --------------------------------------------------------------------------- #
def test_result_posts_and_lists(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = cli(["result", "--from", "ada", "lexer done; 3 files, all tests green"], env)
        c.check("result succeeds", r.returncode == 0, r.stdout + r.stderr)

        res = _messages(root, kind="result")
        c.check("a result is stored as kind='result'", len(res) == 1, str(res))
        c.check("a result carries NO @mention (cannot block a Stop / wedge the barrier)",
                res and json.loads(res[0]["mentions"] or "[]") == [], str(res))

        out = cli(["results"], env).stdout
        c.check("results lists the report body", "lexer done" in out, out)
        c.check("results attributes it to the sender", "ada" in out, out)


def test_results_dormant_when_none(c):
    with tmp_root() as root:
        env = init_room(root)
        out = cli(["results"], env).stdout
        c.check("results is dormant when nothing reported",
                "no result" in out.lower(), out)


def test_result_with_task_closes_it(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["task", "add", "build the lexer"], env)
        cli(["task", "claim", "1", "--from", "ada"], env)
        r = cli(["result", "--from", "ada", "shipped it", "--task", "1"], env)
        c.check("result --task succeeds", r.returncode == 0, r.stdout + r.stderr)
        rows = _tasks(root)
        c.check("result --task closes the referenced task",
                rows[0]["status"] == "done", str(rows))
        out = cli(["results"], env).stdout
        c.check("the result references the task id", "#1" in out, out)


def test_results_filter_by_sender(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["result", "--from", "ada", "ada result alpha"], env)
        cli(["result", "--from", "bob", "bob result beta"], env)
        out = cli(["results", "--from", "ada"], env).stdout
        c.check("a filtered results view shows the sender's report",
                "ada result alpha" in out, out)
        c.check("a filtered results view excludes other senders",
                "bob result beta" not in out, out)


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def test_summary_digests_the_room(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["goal", "ship v1"], env)
        cli(["task", "add", "write docs"], env)
        cli(["result", "--from", "ada", "lexer complete"], env)
        out = cli(["summary"], env).stdout
        c.check("summary shows the goal", "ship v1" in out, out)
        c.check("summary shows the roster", "ada" in out, out)
        c.check("summary shows the task tally", "open" in out.lower(), out)
        c.check("summary shows reported results", "lexer complete" in out, out)


# --------------------------------------------------------------------------- #
# worktrees / harvest  (read-only, diff-only)
# --------------------------------------------------------------------------- #
def _git(args, cwd):
    return sp.run(["git", "-C", cwd, *args], capture_output=True, text=True)


def _setup_worktree_repo(root):
    """A repo with two groupchat/<name> branches off main; ada and bob both touch
    f.txt (overlap), bob also touches g.txt (unique)."""
    repo = os.path.join(root, "proj")
    os.makedirs(repo)
    sp.run(["git", "init", "-q", "-b", "main", repo], check=True)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    for name, txt in (("f.txt", "base\n"), ("g.txt", "base\n")):
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(txt)
    _git(["add", "-A"], repo); _git(["commit", "-qm", "base"], repo)
    # ada: edit f.txt
    _git(["checkout", "-q", "-b", "groupchat/ada"], repo)
    with open(os.path.join(repo, "f.txt"), "w") as fh:
        fh.write("ada change\n")
    _git(["commit", "-aqm", "ada work"], repo)
    # bob (from main): edit f.txt (overlap) + g.txt (unique)
    _git(["checkout", "-q", "main"], repo)
    _git(["checkout", "-q", "-b", "groupchat/bob"], repo)
    for name, txt in (("f.txt", "bob change\n"), ("g.txt", "bob change\n")):
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(txt)
    _git(["commit", "-aqm", "bob work"], repo)
    _git(["checkout", "-q", "main"], repo)
    return repo


def test_worktree_report(c):
    chat = _import_chat()
    with tmp_root() as root:
        repo = _setup_worktree_repo(root)
        rep = chat.worktree_report(repo, base="main")
        branches = {b["name"]: b for b in rep["branches"]}
        c.check("both groupchat branches are reported",
                set(branches) == {"ada", "bob"}, str(list(branches)))
        c.check("ahead/behind is computed against the base",
                branches.get("ada", {}).get("ahead") == 1
                and branches["ada"]["behind"] == 0, str(branches.get("ada")))
        c.check("each branch reports its changed files",
                set(branches["ada"]["files"]) == {"f.txt"}
                and set(branches["bob"]["files"]) == {"f.txt", "g.txt"},
                str({k: v["files"] for k, v in branches.items()}))
        overlaps = {o["file"]: set(o["branches"]) for o in rep["overlaps"]}
        c.check("a file touched by two branches is flagged as an overlap",
                "f.txt" in overlaps, str(rep["overlaps"]))
        c.check("a file touched by only one branch is NOT an overlap",
                "g.txt" not in overlaps, str(rep["overlaps"]))
        c.check("a suggested merge order covers every branch",
                set(rep["order"]) == {"groupchat/ada", "groupchat/bob"}, str(rep["order"]))


def test_worktrees_cli_is_diff_only(c):
    chat = _import_chat()
    with tmp_root() as root:
        repo = _setup_worktree_repo(root)
        before = _git(["rev-parse", "main"], repo).stdout.strip()
        env = init_room(root)
        env = dict(env); env["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        r = cli(["worktrees", "--base", "main", "--cwd", repo], env)
        c.check("worktrees CLI runs", r.returncode == 0, r.stdout + r.stderr)
        c.check("it surfaces an overlap warning", "f.txt" in r.stdout, r.stdout)
        c.check("harvest is an alias",
                cli(["harvest", "--base", "main", "--cwd", repo], env).returncode == 0)
        after = _git(["rev-parse", "main"], repo).stdout.strip()
        c.check("it is DIFF-ONLY — base is untouched (nothing merged)",
                before == after, f"{before} != {after}")
        # No new branches/merges created.
        n_branches = len([b for b in _git(["branch"], repo).stdout.splitlines() if b.strip()])
        c.check("no branches were created or merged", n_branches == 3,
                _git(["branch"], repo).stdout)


def test_worktrees_dormant_without_groupchat_branches(c):
    with tmp_root() as root:
        repo = os.path.join(root, "plain")
        os.makedirs(repo)
        sp.run(["git", "init", "-q", "-b", "main", repo], check=True)
        _git(["config", "user.email", "t@t"], repo)
        _git(["config", "user.name", "t"], repo)
        with open(os.path.join(repo, "a"), "w") as fh:
            fh.write("x\n")
        _git(["add", "-A"], repo); _git(["commit", "-qm", "c"], repo)
        env = init_room(root)
        r = cli(["worktrees", "--base", "main", "--cwd", repo], env)
        c.check("worktrees is dormant when there are no groupchat branches",
                r.returncode == 0 and "no groupchat" in r.stdout.lower(),
                r.stdout + r.stderr)


def test_result_missing_task_errors(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = cli(["result", "--from", "ada", "done my slice", "--task", "999"], env)
        c.check("result --task on a nonexistent task errors", r.returncode != 0,
                r.stdout + r.stderr)
        res = _messages(root, kind="result")
        c.check("...and posts NO phantom result", len(res) == 0, str(res))


def test_result_already_done_task_is_honest(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["task", "add", "x"], env)
        cli(["result", "--from", "ada", "first close", "--task", "1"], env)
        r = cli(["result", "--from", "ada", "redundant close", "--task", "1"], env)
        c.check("a second result on an already-done task still succeeds",
                r.returncode == 0, r.stdout + r.stderr)
        c.check("...but does not falsely claim it just closed it",
                "closed task #1" not in r.stdout, r.stdout)


def test_result_does_not_harvest_rule_cites(c):
    # A result is not chat — naming R<n> in a result must NOT register a constitution
    # cite (only real chat citations count). Positive control: a chat message does.
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["result", "--from", "ada", "implemented per R1 and R2, all green"], env)
        conn = db(root)
        n_after_result = conn.execute("SELECT COUNT(*) FROM rule_cites").fetchone()[0]
        conn.close()
        c.check("a result naming R<n> harvests NO rule cite", n_after_result == 0,
                str(n_after_result))
        cli(["send", "--from", "ada", "following R3 here"], env)
        conn = db(root)
        n_after_chat = conn.execute("SELECT COUNT(*) FROM rule_cites").fetchone()[0]
        conn.close()
        c.check("(control) a chat message naming R<n> DOES harvest a cite",
                n_after_chat == 1, str(n_after_chat))


def test_result_with_mention_in_body_is_inert(c):
    # Even with a literal @handle in the body, a result carries no mention — so it can
    # never block that agent's Stop or wedge the barrier (the load-bearing guarantee).
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        cli(["register", "--session", "s2", "--from", "bob"], env)
        cli(["result", "--from", "ada", "handing off to @bob now"], env)
        res = _messages(root, kind="result")
        c.check("a result with @bob in the body still has no @mention",
                res and json.loads(res[0]["mentions"] or "[]") == [], str(res))


def test_worktrees_nonexistent_base_errors(c):
    with tmp_root() as root:
        repo = _setup_worktree_repo(root)
        env = init_room(root)
        r = cli(["worktrees", "--base", "no-such-ref", "--cwd", repo], env)
        c.check("an unresolvable --base errors instead of a false 'all clean'",
                r.returncode != 0 and "no-such-ref" in (r.stdout + r.stderr),
                r.stdout + r.stderr)


def test_worktree_report_base_from_main_worktree(c):
    # Run the report from INSIDE a groupchat worktree: the default base must resolve to
    # the MAIN worktree's branch, not the cwd's own branch (which would zero its work).
    chat = _import_chat()
    with tmp_root() as root:
        repo = os.path.join(root, "proj")
        os.makedirs(repo)
        sp.run(["git", "init", "-q", "-b", "main", repo], check=True)
        _git(["config", "user.email", "t@t"], repo)
        _git(["config", "user.name", "t"], repo)
        with open(os.path.join(repo, "f.txt"), "w") as fh:
            fh.write("base\n")
        _git(["add", "-A"], repo); _git(["commit", "-qm", "base"], repo)
        wt = os.path.join(root, "proj-worktrees", "ada")
        _git(["worktree", "add", "-q", wt, "-b", "groupchat/ada"], repo)
        with open(os.path.join(wt, "f.txt"), "w") as fh:
            fh.write("ada change\n")
        _git(["commit", "-aqm", "ada work"], wt)
        # cwd = the ada worktree, base defaulted: must compare ada vs main, not vs ada.
        rep = chat.worktree_report(wt, base=None)
        ada = next((b for b in rep["branches"] if b["name"] == "ada"), None)
        c.check("the default base is the main worktree's branch (not self)",
                ada is not None and ada["ahead"] == 1, str(rep))


def main():
    c = Checker("Phase-2 fan-in (result / results / summary / worktrees)")
    for name, fn in (
        ("result_posts_and_lists", test_result_posts_and_lists),
        ("results_dormant_when_none", test_results_dormant_when_none),
        ("result_with_task_closes_it", test_result_with_task_closes_it),
        ("results_filter_by_sender", test_results_filter_by_sender),
        ("summary_digests_the_room", test_summary_digests_the_room),
        ("worktree_report", test_worktree_report),
        ("worktrees_cli_is_diff_only", test_worktrees_cli_is_diff_only),
        ("worktrees_dormant_without_groupchat_branches",
         test_worktrees_dormant_without_groupchat_branches),
        ("result_missing_task_errors", test_result_missing_task_errors),
        ("result_already_done_task_is_honest", test_result_already_done_task_is_honest),
        ("result_does_not_harvest_rule_cites", test_result_does_not_harvest_rule_cites),
        ("result_with_mention_in_body_is_inert", test_result_with_mention_in_body_is_inert),
        ("worktrees_nonexistent_base_errors", test_worktrees_nonexistent_base_errors),
        ("worktree_report_base_from_main_worktree",
         test_worktree_report_base_from_main_worktree),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
