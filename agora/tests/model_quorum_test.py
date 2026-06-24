#!/usr/bin/env python3
"""Heterogeneous-model quorum — make the homogeneous-fleet CAPTURE threat VISIBLE.

A vote tally on a homogeneous LLM fleet is "one opinion counted N times" — which is
exactly why a vote can never bind (a human ratifies from evidence). This layer records
each voter's MODEL and annotates the ADVISORY tally with model DIVERSITY: a unanimous
sweep from a single model is flagged low-independence; support across several models is a
genuinely stronger signal. It NEVER changes whether anything binds — it just surfaces the
capture risk so the human ratifier can weigh it.

  * `agents.model` (NULL = unknown); set via `$AGORA_MODEL` or the `model` verb;
  * `motion_tally` reports distinct models among the casting voters + a single_model flag;
  * `amendments` / `agenda` show the diversity and warn on a single-model sweep.

Dependency-free; isolated. Run:  python3 tests/model_quorum_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, cli, db, env_for, init_room, tmp_root  # noqa: E402


def _import_chat():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".groupchat")
    sys.path.insert(0, here)
    import chat  # noqa: E402
    return chat


def _reg(root, env, sid, handle, model=None):
    e = dict(env)
    if model:
        e["AGORA_MODEL"] = model
    cli(["register", "--session", sid, "--from", handle], e)


def _decide_id(root, env, question):
    cli(["decide", question, "--because", "testing model quorum", "--from", "ada"], env)
    conn = db(root)
    mid = conn.execute("SELECT MAX(id) FROM motions").fetchone()[0]
    conn.close()
    return mid


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
def test_model_recorded_on_register(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s1", "ada", model="opus")
        _reg(root, env, "s2", "bob")  # no model
        conn = db(root)
        m = {r["handle"]: r["model"] for r in conn.execute("SELECT handle, model FROM agents")}
        conn.close()
        c.check("$AGORA_MODEL is recorded on register", m.get("ada") == "opus", str(m))
        c.check("no model env -> NULL (unknown)", m.get("bob") is None, str(m))


def test_model_verb_sets_and_shows(c):
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s1", "ada")
        cli(["model", "claude-opus-4-8", "--from", "ada"], env)
        conn = db(root)
        mv = conn.execute("SELECT model FROM agents WHERE handle='ada'").fetchone()[0]
        conn.close()
        c.check("the `model` verb sets your model", mv == "claude-opus-4-8", mv)
        c.check("`model` shows your model", "claude-opus-4-8" in cli(["model", "--from", "ada"], env).stdout)


# --------------------------------------------------------------------------- #
# the tally surfaces model diversity (the capture signal)
# --------------------------------------------------------------------------- #
def test_single_model_sweep_is_flagged(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        for sid, h in (("s1", "ada"), ("s2", "bob"), ("s3", "carol")):
            _reg(root, env, sid, h, model="opus")  # a homogeneous fleet
        mid = _decide_id(root, env, "adopt ruff?")
        for sid in ("s1", "s2", "s3"):
            cli(["vote", "--session", sid, "M" + str(mid), "yea"], env)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            t = chat.motion_tally(conn, mid)
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)
        c.check("a 3-0 sweep counts 3 voters", t["yea"] == 3 and t["voters"] == 3, str(t))
        c.check("...but only ONE distinct model among them", t["models"] == 1, str(t))
        c.check("single-model unanimity is flagged (low independence)",
                t["single_model"] is True, str(t))


def test_cross_model_support_is_counted(c):
    chat = _import_chat()
    with tmp_root() as root:
        env = init_room(root)
        _reg(root, env, "s1", "ada", model="opus")
        _reg(root, env, "s2", "bob", model="codex")
        _reg(root, env, "s3", "carol", model="opencode-glm")
        mid = _decide_id(root, env, "adopt ruff?")
        for sid in ("s1", "s2", "s3"):
            cli(["vote", "--session", sid, "M" + str(mid), "yea"], env)
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        try:
            conn = chat.connect()
            t = chat.motion_tally(conn, mid)
            conn.close()
        finally:
            os.environ.pop("GROUPCHAT_DIR", None)
        c.check("support across 3 distinct models is counted as 3",
                t["models"] == 3, str(t))
        c.check("cross-model support is NOT flagged single-model",
                t["single_model"] is False, str(t))


def test_amendments_surfaces_diversity_and_warns(c):
    with tmp_root() as root:
        env = init_room(root)
        for sid, h in (("s1", "ada"), ("s2", "bob")):
            _reg(root, env, sid, h, model="opus")
        mid = _decide_id(root, env, "adopt ruff?")
        for sid in ("s1", "s2"):
            cli(["vote", "--session", sid, "M" + str(mid), "yea"], env)
        ag = cli(["agenda"], env).stdout
        c.check("the agenda annotates model count", "model" in ag.lower(), ag)
        c.check("a single-model sweep is warned about in the surface",
                ("single-model" in ag.lower() or "single model" in ag.lower()
                 or "one model" in ag.lower()), ag)


def test_diversity_is_advisory_only(c):
    # The safety invariant: model diversity NEVER makes a vote bind. Even a perfectly
    # diverse unanimous vote is still only ADVISORY — the surface says so.
    with tmp_root() as root:
        env = init_room(root)
        for sid, h, mdl in (("s1", "ada", "opus"), ("s2", "bob", "codex")):
            _reg(root, env, sid, h, mdl)
        mid = _decide_id(root, env, "adopt ruff?")
        for sid in ("s1", "s2"):
            cli(["vote", "--session", sid, "M" + str(mid), "yea"], env)
        ag = cli(["agenda"], env).stdout.lower()
        c.check("the tally stays framed as advisory regardless of diversity",
                "advisory" in ag, ag)


def test_spawn_command_threads_model(c):
    # The headline capture case is a same-host bootstrap (one orchestrator, one model) —
    # so a bootstrapped fleet must self-declare its model, else the layer is inert.
    chat = _import_chat()
    cmd = chat._spawn_command("bob", "/x", None, model="claude-opus-4-8")
    c.check("a spawned child carries AGORA_MODEL so a bootstrapped fleet self-declares",
            "AGORA_MODEL=claude-opus-4-8" in cmd, cmd)
    plain = chat._spawn_command("bob", "/x", None)
    c.check("no model -> no AGORA_MODEL in the spawn command (dormant)",
            "AGORA_MODEL" not in plain, plain)


def test_junk_model_is_refused(c):
    with tmp_root() as root:
        env = init_room(root)
        cli(["register", "--session", "s1", "--from", "ada"], env)
        r = cli(["model", "!!!", "--from", "ada"], env)
        c.check("a junk model id is refused (not silently cleared)",
                r.returncode != 0, r.stdout + r.stderr)
        conn = db(root)
        mv = conn.execute("SELECT model FROM agents WHERE handle='ada'").fetchone()[0]
        conn.close()
        c.check("...and the prior (unset) model is untouched", mv is None, str(mv))


def main():
    c = Checker("heterogeneous-model quorum (capture-visible advisory tally)")
    for name, fn in (
        ("model_recorded_on_register", test_model_recorded_on_register),
        ("model_verb_sets_and_shows", test_model_verb_sets_and_shows),
        ("single_model_sweep_is_flagged", test_single_model_sweep_is_flagged),
        ("cross_model_support_is_counted", test_cross_model_support_is_counted),
        ("amendments_surfaces_diversity_and_warns", test_amendments_surfaces_diversity_and_warns),
        ("diversity_is_advisory_only", test_diversity_is_advisory_only),
        ("spawn_command_threads_model", test_spawn_command_threads_model),
        ("junk_model_is_refused", test_junk_model_is_refused),
    ):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{name}] ran without crashing", False, f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
