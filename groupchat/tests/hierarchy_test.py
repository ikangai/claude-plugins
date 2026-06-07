#!/usr/bin/env python3
"""Hierarchy substrate tests — the READ + GUARD half: resolve_lead() + @human routing.

Dependency-free; isolated via GROUPCHAT_DIR. Run:
    python3 tests/hierarchy_test.py     # exit 0 = all pass

Human contact is hub-and-spoke: only the lead may address @human; a worker's
@human is redirected to @<lead> so questions funnel to one node. WHO the lead is
comes from resolve_lead(), whose order (agreed in chat #20) is:

  1. meta['lead']      — the canonical shared pointer, IF its holder is active
  2. $GROUPCHAT_LEAD   — operator env override, IF its holder is active
  3. floor             — earliest-joined active agent (deterministic, zero-config):
                         a lead ALWAYS exists and fails over when one ages out
  4. None              — only when no agent is active (degenerate → flat)

This suite owns the read/guard contract. The WRITE side (claim / hand-off / release
that *sets* meta['lead']) is a separate track; here we simulate its single effect
(meta['lead']=<handle>) by writing the pointer directly, so the two stay decoupled.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

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
    env.pop("GROUPCHAT_LEAD", None)  # clean designation each test
    env.update(extra)
    return env


def run(args, env):
    return subprocess.run([sys.executable, CHAT, *args],
                          capture_output=True, text=True, env=env)


def db(root):
    return sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))


def register(env, handle):
    run(["register", "--session", f"s_{handle}", "--from", handle], env)


def set_pointer(root, handle):
    """Simulate the write track's one effect: meta['lead'] = handle."""
    c = db(root)
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('lead', ?)", (handle,))
    c.commit()
    c.close()


def last_msg(root):
    row = db(root).execute(
        "SELECT body, mentions, kind FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    return {"body": row[0], "mentions": json.loads(row[1] or "[]"), "kind": row[2]}


# --------------------------------------------------------------------------- #
def test_no_active_agents_is_flat(root):
    env = env_for(root)
    run(["init"], env)
    # nobody registered → no active agents → resolve_lead None → @human untouched
    r = run(["send", "--from", "ghost", "@human anyone there?"], env)
    check("flat: send ok", r.returncode == 0, r.stderr)
    m = last_msg(root)
    check("flat: @human left untouched when no lead can resolve",
          m["mentions"] == ["human"], m["mentions"])
    check("flat: no redirect note", "redirect" not in r.stdout.lower(), r.stdout)


def test_floor_default_lead(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")    # earliest-joined → the floor lead
    register(env, "bohr")
    r = run(["send", "--from", "bohr", "@human is the schema final?"], env)
    m = last_msg(root)
    check("floor: worker @human → earliest-joined active agent (ada)",
          m["mentions"] == ["ada"], m["mentions"])
    check("floor: body rewritten @human→@ada",
          "@ada" in m["body"] and "@human" not in m["body"], m["body"])
    check("floor: worker gets a redirect note",
          "redirect" in r.stdout.lower() and "ada" in r.stdout.lower(), r.stdout)
    # the floor lead itself owns the human channel — passthrough
    r2 = run(["send", "--from", "ada", "@human ready to ship?"], env)
    m2 = last_msg(root)
    check("floor: the lead's own @human passes through",
          m2["mentions"] == ["human"], m2["mentions"])
    check("floor: lead gets no redirect note", "redirect" not in r2.stdout.lower(), r2.stdout)


def test_env_override_active(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")    # floor would be ada
    register(env, "bohr")
    env2 = env_for(root, GROUPCHAT_LEAD="bohr")  # operator names bohr the lead
    r = run(["send", "--from", "ada", "@human override check"], env2)
    m = last_msg(root)
    check("env: active env override beats the floor (→ bohr)",
          m["mentions"] == ["bohr"], m["mentions"])


def test_env_inactive_falls_to_floor(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    env2 = env_for(root, GROUPCHAT_LEAD="ghost")  # not a registered/active agent
    r = run(["send", "--from", "bohr", "@human who's lead?"], env2)
    m = last_msg(root)
    check("env: inactive env lead is ignored → floor (ada)",
          m["mentions"] == ["ada"], m["mentions"])


def test_meta_pointer_active_wins(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    register(env, "curie")
    set_pointer(root, "bohr")                       # canonical claim: bohr is lead
    env2 = env_for(root, GROUPCHAT_LEAD="curie")     # env says curie — pointer must win
    r = run(["send", "--from", "ada", "@human escalate"], env2)
    m = last_msg(root)
    check("meta: active shared pointer wins over env + floor (→ bohr)",
          m["mentions"] == ["bohr"], m["mentions"])


def test_meta_pointer_inactive_falls_through(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    set_pointer(root, "ghost")  # a parked/dead lead the pointer still names
    r = run(["send", "--from", "bohr", "@human lead died?"], env)
    m = last_msg(root)
    check("meta: a stale pointer (inactive holder) fails over to the floor (ada)",
          m["mentions"] == ["ada"], m["mentions"])


def test_redirect_preserves_other_mentions(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    run(["send", "--from", "bohr", "hey @human and @curie, blocked on auth"], env)
    m = last_msg(root)
    check("preserve: @human→@ada, @curie kept",
          m["mentions"] == ["ada", "curie"], m["mentions"])
    check("preserve: body keeps @curie", "@curie" in m["body"], m["body"])


def test_humanish_tokens_not_redirected(root):
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    run(["send", "--from", "bohr", "ping @humanity and @human-ops, not the operator"], env)
    m = last_msg(root)
    check("lookalike: @humanity untouched", "humanity" in m["mentions"], m["mentions"])
    check("lookalike: @human-ops untouched", "human-ops" in m["mentions"], m["mentions"])
    check("lookalike: no spurious lead redirect", "ada" not in m["mentions"], m["mentions"])


def test_reserved_handle_human(root):
    env = env_for(root)
    run(["init"], env)
    r = run(["register", "--session", "s_h", "--from", "human"], env)
    check("reserved: register prints a handle", bool(r.stdout.strip()), r.stderr)
    check("reserved: 'human' is never assigned as a handle",
          r.stdout.strip() != "human", r.stdout)


def test_quoted_handle_is_not_a_mention(root):
    """Root fix (#90): a backticked @handle is not recorded as a mention at all, so
    quoting a teammate's handle in docs/chat never pings, wakes, or blocks them — the
    single consistent home for the code-span rule (routing + inbox + barrier)."""
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    run(["send", "--from", "ada", "see `@bohr`'s patch — quoting, not pinging"], env)
    m = last_msg(root)
    check("quoted `@bohr` is NOT recorded as a mention", "bohr" not in m["mentions"],
          m["mentions"])
    inb = run(["inbox", "--from", "bohr"], env)
    check("quoted `@bohr` does not hit bohr's inbox",
          "no unread mention" in inb.stdout.lower(), inb.stdout)
    run(["send", "--from", "ada", "@bohr please review"], env)
    inb2 = run(["inbox", "--from", "bohr"], env)
    check("a bare @bohr still reaches bohr's inbox", "please review" in inb2.stdout, inb2.stdout)


def test_quoted_human_not_redirected(root):
    """A `@human` inside a code span is documentation, not an escalation — the guard
    must leave it literal (dogfooding caught the whole team mangling quoted tokens)."""
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")   # floor lead
    register(env, "bohr")  # worker
    r = run(["send", "--from", "bohr", "to escalate, write `@human your question`"], env)
    m = last_msg(root)
    check("quoted: backticked @human stays literal in the body",
          "@human" in m["body"] and "@ada" not in m["body"], m["body"])
    check("quoted: no redirect to the lead", "ada" not in m["mentions"], m["mentions"])
    check("quoted: no redirect note printed", "redirect" not in r.stdout.lower(), r.stdout)


def test_bare_human_still_redirected_alongside_quote(root):
    """A real (unquoted) @human still escalates even when a quoted one is present —
    only the bare occurrence is rewritten."""
    env = env_for(root)
    run(["init"], env)
    register(env, "ada")
    register(env, "bohr")
    run(["send", "--from", "bohr", "like `@human`: @human can we ship?"], env)
    m = last_msg(root)
    check("mixed: the bare @human → @ada", "ada" in m["mentions"], m["mentions"])
    check("mixed: the quoted `@human` stays literal", "@human" in m["body"], m["body"])
    check("mixed: the bare occurrence was rewritten (one @ada present)",
          m["body"].count("@ada") == 1, m["body"])


def main():
    tests = [
        test_no_active_agents_is_flat,
        test_floor_default_lead,
        test_env_override_active,
        test_env_inactive_falls_to_floor,
        test_meta_pointer_active_wins,
        test_meta_pointer_inactive_falls_through,
        test_redirect_preserves_other_mentions,
        test_humanish_tokens_not_redirected,
        test_reserved_handle_human,
        test_quoted_handle_is_not_a_mention,
        test_quoted_human_not_redirected,
        test_bare_human_still_redirected_alongside_quote,
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
