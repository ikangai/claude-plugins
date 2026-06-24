#!/usr/bin/env python3
"""Handle recycling + name-at-launch (`GROUPCHAT_HANDLE`).

New behavior under test:
  * a preferred handle held only by an INACTIVE agent is reclaimed for a new
    session (so a restarted shell keeps its GROUPCHAT_HANDLE);
  * auto-pool handles recycle once their holder ages out (the pool stops
    marching forward / exhausting, and the table stops growing unbounded);
  * the SessionStart hook honors `$GROUPCHAT_HANDLE` as the preferred handle.

Guard (must stay green): an ACTIVE agent never loses its handle to a newcomer —
the "a session keeps its handle for life" invariant survives, scoped to *active*.

Isolated via GROUPCHAT_DIR; dependency-free.
"""
import datetime
import importlib.util
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import cli, hook, env_for, db, Checker, tmp_root, CHAT


def _age_out(root, handle, minutes=30):
    ts = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = db(root)
    c.execute("UPDATE agents SET last_seen=? WHERE handle=?", (ts, handle))
    c.commit(); c.close()


def _handle(env, sid):
    return cli(["whoami", "--session", sid], env).stdout.strip()


def _clean_env(root):
    env = env_for(root)
    env.pop("GROUPCHAT_HANDLE", None)  # these cases must not inherit a stray name
    return env


def test_inactive_preferred_handle_reclaimed(c):
    with tmp_root() as root:
        env = _clean_env(root); cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "frontend"], env)
        c.check("preferred handle assigned to first session",
                _handle(env, "s1") == "frontend", _handle(env, "s1"))
        _age_out(root, "frontend")                       # s1's session closed / went idle
        cli(["register", "--session", "s2", "--from", "frontend"], env)
        c.check("inactive 'frontend' reclaimed by the new session",
                _handle(env, "s2") == "frontend", _handle(env, "s2"))
        rows = db(root).execute("SELECT COUNT(*) n FROM agents WHERE handle='frontend'").fetchone()["n"]
        c.check("no duplicate/stale 'frontend' row left behind", rows == 1, f"rows={rows}")


def test_active_preferred_handle_not_stolen(c):
    with tmp_root() as root:
        env = _clean_env(root); cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "frontend"], env)   # stays active
        cli(["register", "--session", "s2", "--from", "frontend"], env)
        c.check("active holder keeps its handle", _handle(env, "s1") == "frontend",
                _handle(env, "s1"))
        c.check("newcomer does NOT steal an active handle",
                _handle(env, "s2") != "frontend", _handle(env, "s2"))


def test_pool_handle_recycles_after_aging_out(c):
    with tmp_root() as root:
        env = _clean_env(root); cli(["init"], env)
        cli(["register", "--session", "s1"], env)        # auto -> pool[0]
        first = _handle(env, "s1")
        _age_out(root, first)
        cli(["register", "--session", "s2"], env)        # auto again
        c.check(f"pool name '{first}' recycled (not advanced to the next name)",
                _handle(env, "s2") == first, _handle(env, "s2"))


def test_session_start_honors_env_handle(c):
    with tmp_root() as root:
        env = env_for(root, GROUPCHAT_HANDLE="frontend"); cli(["init"], env)
        hook("session_start.py", env,
             {"session_id": "s1", "cwd": "/x", "hook_event_name": "SessionStart"})
        c.check("SessionStart honors $GROUPCHAT_HANDLE",
                _handle(env, "s1") == "frontend", _handle(env, "s1"))


def test_reclaiming_lead_handle_does_not_transfer_leadership(c):
    # Recycling must not hand the lead role to whoever reuses a dead lead's name —
    # the stale meta['lead'] pointer is dropped, so the floor re-elects instead.
    with tmp_root() as root:
        env = _clean_env(root); cli(["init"], env)
        cli(["register", "--session", "s0", "--from", "ada"], env)    # earliest → floor
        cli(["register", "--session", "s1", "--from", "boss"], env)
        cli(["lead", "boss"], env)                                    # designate boss the lead
        _age_out(root, "boss")                                       # the lead's session dies
        cli(["register", "--session", "s2", "--from", "boss"], env)  # a new shell reuses 'boss'
        shown = cli(["lead"], env).stdout
        c.check("reusing a dead lead's name does NOT inherit leadership (floor → ada)",
                "@ada" in shown, shown)


def test_toctou_guarded_reclaim_keeps_active_holder(c):
    # TOCTOU: if the handle's holder revives between the staleness check and the
    # reclaim DELETE, an unguarded delete-by-session_id would wipe a now-ACTIVE
    # session (stealing its handle + read cursor) with no IntegrityError to retry on.
    # We simulate the stale verdict deterministically by forcing _is_active False
    # while the DB row is genuinely active (real last_seen=now); the guarded DELETE
    # (last_seen < cutoff) must still refuse to remove it.
    with tempfile.TemporaryDirectory(prefix="gc_toctou_") as root:
        prev = os.environ.get("GROUPCHAT_DIR")
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        os.environ.pop("GROUPCHAT_HANDLE", None)
        try:
            spec = importlib.util.spec_from_file_location("chat_toctou", CHAT)
            chat = importlib.util.module_from_spec(spec); spec.loader.exec_module(chat)
            conn = chat.connect()
            chat.register(conn, "s1", handle="frontend")      # active holder (last_seen=now)
            orig = chat._is_active
            chat._is_active = lambda ls: False                # the "stale snapshot" verdict
            try:
                h2 = chat.register(conn, "s2", handle="frontend")
            finally:
                chat._is_active = orig
            s1 = chat.agent_by_session(conn, "s1")
            c.check("active holder survives a TOCTOU reclaim (keeps 'frontend')",
                    s1 is not None and s1["handle"] == "frontend",
                    None if not s1 else s1["handle"])
            c.check("the newcomer did not steal the active handle", h2 != "frontend", h2)
        finally:
            if prev is None:
                os.environ.pop("GROUPCHAT_DIR", None)
            else:
                os.environ["GROUPCHAT_DIR"] = prev


def main():
    c = Checker("handle recycling + name-at-launch ($GROUPCHAT_HANDLE)")
    for t in (test_inactive_preferred_handle_reclaimed,
              test_active_preferred_handle_not_stolen,
              test_pool_handle_recycles_after_aging_out,
              test_session_start_honors_env_handle,
              test_reclaiming_lead_handle_does_not_transfer_leadership,
              test_toctou_guarded_reclaim_keeps_active_holder):
        t(c)
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
