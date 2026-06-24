#!/usr/bin/env python3
"""Token-metering tests: transcript summing, idempotent recording, and the
``tokens`` CLI rendering. Run:

    python3 tests/token_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, cli, env_for, tmp_root  # noqa: E402

sys.path.insert(0, GROUPCHAT)
import chat  # noqa: E402


def _write_transcript(path, turns):
    """turns: list of (in, out, cache_read, cache_create)."""
    with open(path, "w") as fh:
        for i, o, cr, cc in turns:
            fh.write(json.dumps({"message": {"usage": {
                "input_tokens": i, "output_tokens": o,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cc,
            }}}) + "\n")


def test_sum_transcript_tokens(c):
    with tmp_root() as root:
        tp = os.path.join(root, "t.jsonl")
        _write_transcript(tp, [(10, 5, 100, 7), (20, 8, 50, 3)])
        tot = chat.sum_transcript_tokens(tp)
        c.check("input summed", tot["in_tokens"] == 30, tot)
        c.check("output summed", tot["out_tokens"] == 13, tot)
        c.check("cache-read summed", tot["cache_read_tokens"] == 150, tot)
        c.check("cache-create summed", tot["cache_create_tokens"] == 10, tot)


def test_sum_tolerates_garbage(c):
    with tmp_root() as root:
        tp = os.path.join(root, "t.jsonl")
        with open(tp, "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"message": {"usage": {"output_tokens": 9}}}) + "\n")
            fh.write("\n")  # blank line
            fh.write(json.dumps({"no_message": True}) + "\n")  # no usage
        tot = chat.sum_transcript_tokens(tp)
        c.check("garbage/blank/no-usage lines skipped; valid one counted",
                tot["out_tokens"] == 9, tot)

    c.check("missing transcript -> all zeros",
            chat.sum_transcript_tokens("/no/such/file.jsonl")
            == {k: 0 for k in chat.TOKEN_FIELDS})
    c.check("None transcript -> all zeros",
            chat.sum_transcript_tokens(None) == {k: 0 for k in chat.TOKEN_FIELDS})


def test_record_tokens_is_idempotent(c):
    with tmp_root() as root:
        os.environ["GROUPCHAT_DIR"] = os.path.join(root, ".groupchat")
        conn = chat.connect()
        chat.register(conn, "s1", handle="alice")
        chat.record_tokens(conn, "s1", {"in_tokens": 100, "out_tokens": 50,
                                         "cache_read_tokens": 0, "cache_create_tokens": 0})
        # Re-recording OVERWRITES (totals recomputed from full transcript each
        # park) — must not double to 200.
        chat.record_tokens(conn, "s1", {"in_tokens": 100, "out_tokens": 50,
                                        "cache_read_tokens": 0, "cache_create_tokens": 0})
        row = conn.execute("SELECT in_tokens, out_tokens FROM agents WHERE handle='alice'").fetchone()
        c.check("record_tokens overwrites (not accumulates)",
                row["in_tokens"] == 100 and row["out_tokens"] == 50,
                dict(row))
        conn.close()


def test_tokens_cli(c):
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        cli(["register", "--session", "s1", "--from", "alice"], env)
        # Set token counts directly, then verify the CLI renders them.
        import sqlite3
        conn = sqlite3.connect(os.path.join(root, ".groupchat", "chat.db"))
        conn.execute("UPDATE agents SET out_tokens=1500, in_tokens=2300000 WHERE handle='alice'")
        conn.commit()
        conn.close()
        r = cli(["tokens", "--all"], env)
        c.check("tokens CLI exits 0", r.returncode == 0, r.stderr)
        c.check("tokens renders k-suffix", "1.5k" in r.stdout, r.stdout)
        c.check("tokens renders M-suffix", "2.3M" in r.stdout, r.stdout)
        c.check("tokens prints a TEAM total row", "TEAM" in r.stdout, r.stdout)


def main():
    c = Checker("token metering (sum / record / cli)")
    for fn in (test_sum_transcript_tokens, test_sum_tolerates_garbage,
               test_record_tokens_is_idempotent, test_tokens_cli):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
