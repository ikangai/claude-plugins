#!/usr/bin/env python3
"""Concurrency stress test — the bus's core promise: "safe under concurrent
access (WAL + busy timeout)". The whole product is parallel agents hammering one
SQLite file, yet nothing exercised genuine contention. This launches N writer
*processes* (separate connections, like real sessions) sending simultaneously
and asserts no write is lost, ids stay unique+monotonic, and a reader's single
cursor still delivers every message exactly once.

    python3 tests/concurrency_test.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import Checker, GROUPCHAT, cli, db, env_for, tmp_root  # noqa: E402

WRITER_SRC = """
import os, sys
sys.path.insert(0, os.environ["GC_CODE"])
import chat
wid, n = sys.argv[1], int(sys.argv[2])
conn = chat.connect()
for i in range(n):
    chat.send(conn, wid, f"{wid}-msg-{i}")
conn.close()
"""


def test_parallel_writes_lose_nothing(c):
    N, M = 6, 25  # 6 concurrent writers x 25 messages = 150 contended inserts
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        # A reader registered BEFORE the burst — its cursor should later deliver
        # every message exactly once.
        cli(["register", "--session", "r1", "--from", "reader"], env)

        writer_py = os.path.join(root, "writer.py")
        with open(writer_py, "w") as fh:
            fh.write(WRITER_SRC)
        wenv = dict(env)
        wenv["GC_CODE"] = GROUPCHAT  # where chat.py lives (real repo .groupchat)

        procs = [subprocess.Popen([sys.executable, writer_py, f"w{k}", str(M)],
                                  env=wenv, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for k in range(N)]
        errs = []
        for p in procs:
            _, err = p.communicate(timeout=60)
            if p.returncode != 0:
                errs.append(err[:200])
        c.check("all writer processes exited cleanly (no busy-timeout failures)",
                not errs, "; ".join(errs))

        conn = db(root)
        ids = [r["id"] for r in conn.execute("SELECT id FROM messages ORDER BY id")]
        c.check(f"no writes lost: exactly {N*M} messages", len(ids) == N * M,
                f"got {len(ids)}")
        c.check("all ids unique", len(set(ids)) == len(ids))
        c.check("ids are strictly monotonic",
                all(b > a for a, b in zip(ids, ids[1:])) if len(ids) > 1 else True)
        c.check("ids are contiguous 1..N*M (no failed-insert gaps)",
                ids == list(range(1, N * M + 1)) if ids else False,
                f"min={ids[0] if ids else None} max={ids[-1] if ids else None}")
        # Every writer's full set landed.
        per_writer_ok = True
        for k in range(N):
            cnt = conn.execute("SELECT COUNT(*) c FROM messages WHERE sender=?",
                               (f"w{k}",)).fetchone()["c"]
            if cnt != M:
                per_writer_ok = False
        c.check(f"every writer landed all {M} of its messages", per_writer_ok)
        conn.close()

        # The reader's single cursor delivers all of them once, then nothing.
        r1 = cli(["read", "--from", "reader"], env)
        delivered = r1.stdout.count("-msg-")
        c.check("reader's cursor delivers every message once",
                delivered == N * M, f"delivered {delivered}")
        r2 = cli(["read", "--from", "reader"], env)
        c.check("reader is caught up after one read (no re-delivery)",
                "no new messages" in r2.stdout, r2.stdout[:120])


def test_concurrent_handle_assignment_is_unique(c):
    """Many sessions registering at once must never collide on a handle (the
    INSERT retry loop in register()). Race them and check uniqueness."""
    N = 10
    with tmp_root() as root:
        env = env_for(root)
        cli(["init"], env)
        reg_py = os.path.join(root, "reg.py")
        with open(reg_py, "w") as fh:
            fh.write('import os,sys\nsys.path.insert(0,os.environ["GC_CODE"])\n'
                     'import chat\nconn=chat.connect()\n'
                     'print(chat.register(conn, sys.argv[1]))\n')
        wenv = dict(env)
        wenv["GC_CODE"] = GROUPCHAT
        procs = [subprocess.Popen([sys.executable, reg_py, f"sess{k}"],
                                  env=wenv, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for k in range(N)]
        handles = []
        for p in procs:
            out, _ = p.communicate(timeout=60)
            handles.append(out.strip())
        c.check(f"all {N} concurrent registrations got a handle",
                all(handles) and len(handles) == N, handles)
        c.check("no two sessions share a handle",
                len(set(handles)) == N, f"{len(set(handles))} unique of {N}: {handles}")


def main():
    c = Checker("concurrency (parallel writers / WAL / handle races)")
    for fn in (test_parallel_writes_lose_nothing,
               test_concurrent_handle_assignment_is_unique):
        try:
            fn(c)
        except Exception as e:
            c.check(f"[{fn.__name__}] ran without crashing", False,
                    f"{type(e).__name__}: {e}")
    return c.done()


if __name__ == "__main__":
    sys.exit(main())
