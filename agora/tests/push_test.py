#!/usr/bin/env python3
"""Push-wake (P1) — native cross-session inbox nudges.

Claude Code >= 2.1.224 binds a per-session Unix inbox socket and exports it to
hooks/Bash as ``$CLAUDE_CODE_MESSAGING_SOCKET``. P1 (see
docs/plans/2026-08-09-native-cross-session-messaging-blindspot.md):

  * ``register`` captures the socket into a new ``agents.inbox`` column
    (refresh updates it; an absent var never erases a stored value);
  * ``send()`` best-effort nudges each @mentioned, active, socketed teammate —
    a JSON ``{"type":"user",...}`` line pinned to the recipient's session_id,
    telling it to READ THE BUS (never carrying the body);
  * everything is advisory + fail-open: a dead socket, a socketless host, or
    ``AGORA_PUSH=0`` changes nothing about bus delivery.

The test stands up a REAL Unix-socket listener and asserts on the exact payload.
Dependency-free; isolated via GROUPCHAT_DIR. Run:  python3 tests/push_test.py
"""
import json
import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import (  # noqa: E402
    Checker, cli, db, env_for, init_room, tmp_root,
)


def _listener(sock_path: str) -> socket.socket:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(4)
    srv.settimeout(2)
    return srv


def _take_payload(srv: socket.socket):
    """Accept one queued connection and parse its JSON line, or None on timeout."""
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        return None
    try:
        conn.settimeout(2)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8"))
    finally:
        conn.close()


def _inbox_of(root: str, session: str):
    conn = db(root)
    try:
        row = conn.execute(
            "SELECT inbox FROM agents WHERE session_id = ?", (session,)).fetchone()
        return row["inbox"] if row else "(no row)"
    finally:
        conn.close()


def main() -> int:
    c = Checker("push-wake (native inbox nudge)")

    # --- env hygiene: a live session's socket must never leak into tests -----
    os.environ["CLAUDE_CODE_MESSAGING_SOCKET"] = "/tmp/leak-canary.sock"
    try:
        leaked = "CLAUDE_CODE_MESSAGING_SOCKET" in env_for("/tmp/x")
    finally:
        del os.environ["CLAUDE_CODE_MESSAGING_SOCKET"]
    c.check("env_for scrubs the live session's inbox socket", not leaked)

    with tmp_root() as root:
        env = init_room(root)
        # Short dir for sockets — macOS caps AF_UNIX paths at ~104 bytes, and the
        # isolated room dir under tmp_root can exceed that.
        sockdir = tempfile.mkdtemp(prefix="agora-push-")
        bob_sock = os.path.join(sockdir, "b.sock")

        # --- capture at register --------------------------------------------
        r = cli(["register", "--session", "s-bob", "--from", "bob"],
                env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=bob_sock))
        c.check("register exits 0", r.returncode == 0, r.stderr)
        c.check("register captures $CLAUDE_CODE_MESSAGING_SOCKET into agents.inbox",
                _inbox_of(root, "s-bob") == bob_sock)

        # Refresh without the var must NOT erase the stored socket.
        cli(["register", "--session", "s-bob"], env)
        c.check("refresh without the var keeps the stored inbox",
                _inbox_of(root, "s-bob") == bob_sock)

        # Refresh WITH a new value (resume → new pid → new socket) updates it.
        bob_sock2 = os.path.join(sockdir, "b2.sock")
        cli(["register", "--session", "s-bob"],
            env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=bob_sock2))
        c.check("refresh with a new value updates the inbox",
                _inbox_of(root, "s-bob") == bob_sock2)
        cli(["register", "--session", "s-bob"],
            env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=bob_sock))  # back for the tests

        # A socketless host (bridge fleet / old version) records NULL.
        cli(["register", "--session", "s-ada", "--from", "ada"], env)
        c.check("no var → inbox stays NULL", _inbox_of(root, "s-ada") is None)

        # --- the nudge ------------------------------------------------------
        srv = _listener(bob_sock)
        r = cli(["send", "--from", "ada", "@bob lexer question for you"], env)
        c.check("send with @mention exits 0", r.returncode == 0, r.stderr)
        p = _take_payload(srv)
        c.check("mentioned teammate's socket receives a nudge", p is not None)
        if p:
            c.check("payload is a type=user message with string content",
                    p.get("type") == "user"
                    and isinstance(p.get("message", {}).get("content"), str))
            c.check("payload pins the RECIPIENT's session_id",
                    p.get("session_id") == "s-bob")
            body = p["message"]["content"]
            c.check("nudge names the bus message id and sender",
                    "#" in body and "ada" in body, body)
            c.check("nudge instructs a bus read, not a reply-in-place",
                    f"read --from bob" in body, body)
            c.check("nudge does NOT carry the message body (cursor stays the "
                    "single delivery path)", "lexer question" not in body, body)

        # A second distinct message must produce a distinguishable nudge (the
        # receiver's identical-repeat throttle would swallow a byte-identical one).
        cli(["send", "--from", "ada", "@bob second question"], env)
        p2 = _take_payload(srv)
        c.check("second mention → second nudge", p2 is not None)
        if p and p2:
            c.check("nudges differ per message (throttle-safe)",
                    p["message"]["content"] != p2["message"]["content"])

        # --- who is NOT nudged ----------------------------------------------
        # Plain broadcast (no @mention) → no nudge.
        cli(["send", "--from", "ada", "morning everyone"], env)
        c.check("un-mentioned chatter does not nudge", _take_payload(srv) is None)

        # Self-mention → no self-nudge.
        cli(["send", "--from", "bob", "@bob note to self"], env)
        c.check("self-mention does not nudge", _take_payload(srv) is None)

        # Non-chat kinds carry no mentions → no nudge (result never blocks/wakes).
        cli(["result", "--from", "bob", "@bob-ish looking result text"], env)
        c.check("a result never nudges", _take_payload(srv) is None)

        # AGORA_PUSH=0 disables the whole channel.
        cli(["send", "--from", "ada", "@bob but push is off"],
            env_for(root, AGORA_PUSH="0"))
        c.check("AGORA_PUSH=0 disables nudging", _take_payload(srv) is None)

        # P3 reachability: an agent aged out of the active window is STILL nudged
        # while its inbox socket is bound — a retired-idle worker's live socket is a
        # truer liveness signal than its stale last_seen.
        conn = db(root)
        conn.execute("UPDATE agents SET last_seen='2000-01-01T00:00:00Z' "
                     "WHERE session_id='s-bob'")
        conn.commit(); conn.close()
        cli(["send", "--from", "ada", "@bob still reachable while idle"], env)
        c.check("retired-idle agent (stale last_seen, live socket) is still nudged",
                _take_payload(srv) is not None)

        # ...but once the socket is gone (process exited), an inactive agent is not
        # nudged — nothing to reach, and the stale window no longer vouches for it.
        conn = db(root)
        conn.execute("UPDATE agents SET last_seen='2000-01-01T00:00:00Z', "
                     "inbox='/tmp/cc-socks/p3-gone-nonexistent.sock' "
                     "WHERE session_id='s-bob'")
        conn.commit(); conn.close()
        cli(["send", "--from", "ada", "@bob anyone home"], env)
        c.check("inactive agent with a vanished socket is not nudged",
                _take_payload(srv) is None)
        cli(["register", "--session", "s-bob"],
            env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=bob_sock))  # revive

        # --- failure is invisible -------------------------------------------
        srv.close(); os.unlink(bob_sock)  # dead socket: nobody listening
        r = cli(["send", "--from", "ada", "@bob dead socket test"], env)
        c.check("dead recipient socket: send still exits 0", r.returncode == 0,
                r.stderr)
        conn = db(root)
        n = conn.execute("SELECT COUNT(*) FROM messages "
                         "WHERE body LIKE '%dead socket test%'").fetchone()[0]
        conn.close()
        c.check("dead recipient socket: message still stored on the bus", n == 1)

        # answer rides send(): the escalation reply nudges the asker.
        srv = _listener(bob_sock)
        cli(["register", "--session", "s-bob"],
            env_for(root, CLAUDE_CODE_MESSAGING_SOCKET=bob_sock))
        # Make bob the lead so its @human passes through (the floor ties on
        # same-second first_seen and would elect ada, redirecting the mention).
        cli(["lead", "bob"], env)
        r = cli(["send", "--from", "bob", "--session", "s-bob", "@human ship it?"], env)
        conn = db(root)
        mid = conn.execute("SELECT MAX(id) FROM messages").fetchone()[0]
        conn.close()
        _take_payload(srv)  # drain any nudge from the escalation itself
        r = cli(["answer", str(mid), "yes, ship"], env)
        c.check("answer exits 0", r.returncode == 0, r.stderr)
        p = _take_payload(srv)
        c.check("operator's answer push-wakes the asker",
                p is not None and p.get("session_id") == "s-bob")

    return c.done()


if __name__ == "__main__":
    sys.exit(main())
