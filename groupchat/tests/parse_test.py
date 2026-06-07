#!/usr/bin/env python3
"""Pure-function tests: @mention parsing, mention/kind gating in ``send``, and
message formatting. (Rule-cite parsing + harvest gating live in
``cite_review_test.py`` — not duplicated here.) Run:

    python3 tests/parse_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, tmp_root  # noqa: E402

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402


def test_parse_mentions(c):
    cases = [
        ("hi @bob", ["bob"]),
        ("@Bob and @ALICE", ["alice", "bob"]),          # lowercased + sorted
        ("@bob @bob @carol", ["bob", "carol"]),          # de-duplicated
        ("ping @human please", ["human"]),               # @human is just a token
        ("plain text, no handles", []),
        ("email user@example.com is not a mention", []),  # @ after a word char
        ("path /srv/@deploy ignored", []),               # @ after a slash
        ("hyphen @ada-2 and underscore @foo_bar", ["ada-2", "foo_bar"]),
        ("@1bad starts with a digit", []),               # handle must start a-z
        ("parenthetical (@ada) counts", ["ada"]),        # '(' is not \\w or /
        # Code-span awareness (newton's broad fix, chat #104): a backticked
        # @handle is QUOTING it, not pinging — so it's not a mention. This is the
        # single root home that makes routing / inbox / escalation all consistent
        # (quoting a handle never spuriously pings/wakes/escalates anyone).
        ("see `@bohr` and @ada", ["ada"]),               # backticked dropped, bare kept
        ("`@ada` only", []),                             # inline code span dropped
        ("fenced ```@ghost``` end", []),                 # fenced span dropped
        ("quote `@human` to discuss the feature", []),   # the phantom-escalation fix
    ]
    for body, exp in cases:
        got = chat.parse_mentions(body)
        c.check(f"parse_mentions({body!r}) == {exp}", got == exp, f"got {got}")


def test_send_mention_gating(c):
    """Only ``kind='chat'`` messages carry @mentions; motions/votes/system must
    not — so they never block a Stop or gate the barrier."""
    with tmp_root() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        conn = chat.connect()
        mid_chat = chat.send(conn, "alice", "yo @bob", kind="chat")
        mid_sys = chat.send(conn, "alice", "system note for @bob", kind="system")
        row_chat = conn.execute("SELECT mentions FROM messages WHERE id=?",
                                (mid_chat,)).fetchone()
        row_sys = conn.execute("SELECT mentions FROM messages WHERE id=?",
                               (mid_sys,)).fetchone()
        c.check("chat message carries the mention",
                json.loads(row_chat["mentions"]) == ["bob"], row_chat["mentions"])
        c.check("system message carries NO mentions",
                json.loads(row_sys["mentions"]) == [], row_sys["mentions"])
        conn.close()


def test_format_message(c):
    with tmp_root() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        conn = chat.connect()
        mid = chat.send(conn, "alice", "hello @bob", kind="chat")
        row = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        out = chat.format_message(row)
        c.check("format shows id, sender, body",
                "#" + str(mid) in out and "alice" in out and "hello @bob" in out, out)
        c.check("format renders the mention arrow", "→ @bob" in out, out)
        # highlight stars a message that mentions you.
        star = chat.format_message(row, highlight="bob")
        c.check("highlight adds a star for the mentioned agent",
                star.startswith("★"), star)
        nostar = chat.format_message(row, highlight="carol")
        c.check("no star for an unmentioned agent", not nostar.startswith("★"), nostar)
        # non-chat kind is tagged.
        sid = chat.send(conn, "alice", "ratified", kind="system")
        srow = conn.execute("SELECT * FROM messages WHERE id=?", (sid,)).fetchone()
        c.check("non-chat kind is tagged", "(system)" in chat.format_message(srow))
        conn.close()


def test_fmt_count(c):
    c.check("_fmt_count small", chat._fmt_count(42) == "42")
    c.check("_fmt_count thousands", chat._fmt_count(1500) == "1.5k")
    c.check("_fmt_count millions", chat._fmt_count(2_300_000) == "2.3M")
    c.check("_fmt_count handles None", chat._fmt_count(None) == "0")


def main():
    c = Checker("parsing & formatting (mentions / gating / render)")
    for fn in (test_parse_mentions, test_send_mention_gating, test_format_message,
               test_fmt_count):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
