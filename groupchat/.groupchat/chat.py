#!/usr/bin/env python3
"""groupchat — a shared chat bus for parallel Claude Code instances on one repo.

All instances working on the same repository share a single SQLite database on
disk. Each instance ("agent") gets a short, memorable handle (e.g. ``curie``).
Agents post messages, @mention each other, and track which messages they have
already seen via a per-agent read cursor.

This file is BOTH:
  * an importable module (the hook scripts ``import chat`` and call functions), and
  * a command-line tool (``python3 chat.py <command> ...``).

Storage location resolution (first match wins):
  1. ``$GROUPCHAT_DIR``                         — explicit override
  2. ``<git common dir parent>/.groupchat``     — shared across all worktrees
  3. ``$CLAUDE_PROJECT_DIR/.groupchat``         — project root when run via a hook
  4. ``<cwd>/.groupchat``                       — fallback

Design goals: zero third-party dependencies (Python 3 stdlib only), safe under
concurrent access (WAL + busy timeout), and never crash a Claude session — the
hook wrappers swallow all errors.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# Memorable handles, assigned in order to each new agent. Scientists &
# mathematicians; if the pool is exhausted we fall back to ``agent-N``.
HANDLE_POOL = [
    "ada", "turing", "hopper", "lovelace", "curie", "bohr", "tesla", "newton",
    "euler", "gauss", "noether", "ramanujan", "fermi", "feynman", "dirac",
    "shannon", "babbage", "kepler", "galois", "pascal", "fourier", "laplace",
    "hilbert", "riemann", "cantor", "godel", "church", "knuth", "dijkstra",
    "liskov", "kay", "ritchie", "thompson", "torvalds", "berners", "engelbart",
]

MENTION_RE = re.compile(r"(?<![\w/])@([a-z][a-z0-9_-]*)", re.IGNORECASE)
ACTIVE_WINDOW_SECONDS = 15 * 60  # an agent is "active" if seen within 15 min

# --- Team barrier (parallel /goal coordination) -------------------------------
# A finished agent does not exit on its own; it waits at a barrier until the
# whole team is done. These tune that wait (see docs/plans/*-team-barrier-*).
DONE_STATUS = "done"
STARTUP_GRACE_SECONDS = 90        # how long to wait for a staggered launch when
                                  # the team size is unknown (no GROUPCHAT_TEAM_SIZE
                                  # / `expect`), before the barrier may complete
MAX_PARK_SECONDS = 2 * 60 * 60    # ceiling: release a parked agent after this much
                                  # continuous waiting regardless of the barrier,
                                  # so a mis-set team size can't hang everyone forever.
                                  # Override per-run with GROUPCHAT_MAX_PARK (seconds;
                                  # 0 = release immediately). Raised from 30m so long
                                  # goals don't drop teammates mid-run.


# --------------------------------------------------------------------------- #
# Storage location & connection
# --------------------------------------------------------------------------- #
def store_dir() -> str:
    """Return the directory holding the shared chat database for this repo."""
    env = os.environ.get("GROUPCHAT_DIR")
    if env:
        return os.path.abspath(env)

    # All worktrees of one repo share a single git "common dir"; anchoring the
    # room there means agents in different worktrees still see each other.
    try:
        # --path-format=absolute (git >= 2.31) avoids a relative ".git" that would
        # resolve against the wrong cwd; abspath() is a fallback for older git.
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=3,
        )
        out = common.stdout.strip()
        if common.returncode != 0 or not out:
            common = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, timeout=3,
            )
            out = common.stdout.strip()
        if common.returncode == 0 and out:
            git_common = os.path.abspath(out)
            # parent of the .git dir == main worktree root
            root = os.path.dirname(git_common) or os.getcwd()
            return os.path.join(root, ".groupchat")
    except Exception:
        pass

    cpd = os.environ.get("CLAUDE_PROJECT_DIR")
    if cpd and os.path.isdir(cpd):
        return os.path.join(os.path.abspath(cpd), ".groupchat")

    return os.path.join(os.getcwd(), ".groupchat")


def db_path() -> str:
    return os.path.join(store_dir(), "chat.db")


def connect() -> sqlite3.Connection:
    d = store_dir()
    os.makedirs(d, exist_ok=True)
    # In a plugin install the repo's .groupchat/ holds only the runtime db, so
    # drop a gitignore on first creation. Guarded by exists() — a committed
    # .gitignore (e.g. this dev repo's) is left untouched.
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        try:
            with open(gi, "w") as fh:
                fh.write("# group chat runtime — do not commit\n*\n")
        except Exception:
            pass
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _add_column_if_missing(conn, table: str, col: str, decl: str) -> None:
    have = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            sender      TEXT    NOT NULL,
            session_id  TEXT,
            kind        TEXT    NOT NULL DEFAULT 'chat',
            body        TEXT    NOT NULL,
            mentions    TEXT    NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS agents (
            session_id   TEXT PRIMARY KEY,
            handle       TEXT UNIQUE NOT NULL,
            cwd          TEXT,
            pid          INTEGER,
            status       TEXT,
            first_seen   TEXT,
            last_seen    TEXT,
            last_read_id INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_id ON messages(id);
        """
    )
    # Token-usage columns (added post-v1; guarded so old dbs upgrade in place).
    for _col in ("in_tokens", "out_tokens", "cache_read_tokens", "cache_create_tokens"):
        _add_column_if_missing(conn, "agents", _col, "INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hhmm(ts: str) -> str:
    """Render an ISO timestamp as local HH:MM for compact display."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%H:%M")
    except Exception:
        return ts[11:16] if len(ts) >= 16 else ts


def parse_mentions(body: str) -> list[str]:
    return sorted({m.group(1).lower() for m in MENTION_RE.finditer(body)})


def _now_epoch() -> float:
    return time.time()


def iso_age_seconds(ts: str | None) -> float:
    """Seconds elapsed since an ISO timestamp; +inf if unparseable/empty."""
    if not ts:
        return float("inf")
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return _now_epoch() - dt.timestamp()
    except Exception:
        return float("inf")


def _is_active(last_seen: str | None) -> bool:
    return iso_age_seconds(last_seen) <= ACTIVE_WINDOW_SECONDS


def _env_int(name: str) -> int | None:
    v = os.environ.get(name)
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Token accounting (best-effort; from the local Claude Code transcript)
# --------------------------------------------------------------------------- #
TOKEN_FIELDS = ("in_tokens", "out_tokens", "cache_read_tokens", "cache_create_tokens")
_USAGE_MAP = {
    "in_tokens": "input_tokens",
    "out_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_create_tokens": "cache_creation_input_tokens",
}


def sum_transcript_tokens(transcript_path: str | None) -> dict:
    """Sum per-turn ``usage`` across assistant messages in a Claude Code
    transcript (JSONL). Returns the four cumulative counts; zeros on any error.

    Approximate by design (see docs/plans/2026-06-02-groupchat-plugin-design.md):
    the transcript's input/output counts can undercount; cache counts are
    reliable. Good enough for *relative* per-agent burn and idle verification.
    """
    totals = {k: 0 for k in TOKEN_FIELDS}
    if not transcript_path or not os.path.isfile(transcript_path):
        return totals
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                usage = (rec.get("message") or {}).get("usage") or {}
                if not usage:
                    continue
                for col, src in _USAGE_MAP.items():
                    totals[col] += int(usage.get(src) or 0)
    except Exception:
        pass
    return totals


# --------------------------------------------------------------------------- #
# Meta key/value store (small bits of room-wide state)
# --------------------------------------------------------------------------- #
def get_meta(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def del_meta(conn, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Agent registry & identity
# --------------------------------------------------------------------------- #
def _assign_handle(conn: sqlite3.Connection, preferred: str | None = None) -> str:
    taken = {r["handle"] for r in conn.execute("SELECT handle FROM agents")}
    if preferred:
        cand = re.sub(r"[^a-z0-9_-]", "", preferred.lower()) or "agent"
        if cand not in taken:
            return cand
        i = 2
        while f"{cand}-{i}" in taken:
            i += 1
        return f"{cand}-{i}"
    for h in HANDLE_POOL:
        if h not in taken:
            return h
    i = 1
    while f"agent-{i}" in taken:
        i += 1
    return f"agent-{i}"


def register(conn: sqlite3.Connection, session_id: str, cwd: str | None = None,
             pid: int | None = None, handle: str | None = None,
             status: str | None = None) -> str:
    """Idempotently ensure an agent row for ``session_id``; return its handle.

    Re-running (e.g. on every prompt) refreshes ``last_seen`` without changing
    the handle, so an agent keeps a stable identity for its whole session.
    """
    row = conn.execute(
        "SELECT handle FROM agents WHERE session_id = ?", (session_id,)
    ).fetchone()
    ts = now_iso()
    if row:
        sets = ["last_seen = ?"]
        params: list = [ts]
        if cwd is not None:
            sets.append("cwd = ?"); params.append(cwd)
        if pid is not None:
            sets.append("pid = ?"); params.append(pid)
        if status is not None:
            sets.append("status = ?"); params.append(status)
        params.append(session_id)
        conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE session_id = ?", params)
        conn.commit()
        return row["handle"]

    # New agent: assign a handle, retrying on the rare race where two sessions
    # grab the same one concurrently.
    for _ in range(len(HANDLE_POOL) + 50):
        h = _assign_handle(conn, handle)
        try:
            conn.execute(
                "INSERT INTO agents(session_id, handle, cwd, pid, status, "
                "first_seen, last_seen, last_read_id) VALUES (?,?,?,?,?,?,?, "
                "(SELECT COALESCE(MAX(id),0) FROM messages))",
                (session_id, h, cwd, pid, status, ts, ts),
            )
            conn.commit()
            return h
        except sqlite3.IntegrityError:
            handle = None  # collided; let the pool pick the next free one
            continue
    raise RuntimeError("could not assign a unique handle")


def agent_by_session(conn, session_id: str):
    return conn.execute(
        "SELECT * FROM agents WHERE session_id = ?", (session_id,)
    ).fetchone()


def agent_by_handle(conn, handle: str):
    return conn.execute(
        "SELECT * FROM agents WHERE handle = ?", (handle.lower(),)
    ).fetchone()


def resolve_agent(conn, session_id: str | None, handle: str | None):
    if session_id:
        a = agent_by_session(conn, session_id)
        if a:
            return a
    if handle:
        return agent_by_handle(conn, handle)
    return None


def active_agents(conn) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM agents ORDER BY handle").fetchall()
    return [r for r in rows if _is_active(r["last_seen"])]


def set_status(conn, session_id: str, status: str) -> None:
    conn.execute(
        "UPDATE agents SET status = ?, last_seen = ? WHERE session_id = ?",
        (status, now_iso(), session_id),
    )
    conn.commit()


def record_tokens(conn, session_id: str, totals: dict) -> None:
    """Overwrite an agent's cumulative token counts (idempotent — totals are
    recomputed from the full transcript each call, so re-parks can't double-count)."""
    conn.execute(
        "UPDATE agents SET in_tokens=?, out_tokens=?, cache_read_tokens=?, "
        "cache_create_tokens=? WHERE session_id=?",
        (int(totals.get("in_tokens", 0)), int(totals.get("out_tokens", 0)),
         int(totals.get("cache_read_tokens", 0)), int(totals.get("cache_create_tokens", 0)),
         session_id),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Team barrier — when may a finished agent actually exit?
# --------------------------------------------------------------------------- #
def expected_team_size(conn) -> int | None:
    """Declared team size, if any: ``$GROUPCHAT_TEAM_SIZE`` wins, else ``expect``."""
    n = _env_int("GROUPCHAT_TEAM_SIZE")
    if n is not None:
        return n
    mv = get_meta(conn, "team_size")
    return int(mv) if mv and mv.isdigit() else None


def max_park_seconds() -> int:
    v = _env_int("GROUPCHAT_MAX_PARK")  # 0 is a valid (release-now) override
    return MAX_PARK_SECONDS if v is None else v


def cohort_age_seconds(conn) -> float:
    """Age of the *current cohort* — seconds since the earliest-joined active
    agent first registered. Approximates "time since this run started" without a
    persisted session marker, so a fresh burst of agents on an old room still
    gets its startup grace."""
    ages = [iso_age_seconds(a["first_seen"]) for a in active_agents(conn)]
    finite = [a for a in ages if a != float("inf")]
    return max(finite) if finite else 0.0


def startup_guard_satisfied(conn) -> bool:
    """Has the team finished assembling enough to trust the barrier?

    Closes the ragged-startup race where a fast agent stops before slower
    teammates have even registered (trivially satisfying an empty barrier).
    """
    size = expected_team_size(conn)
    if size:
        n = conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"]
        return n >= size
    return cohort_age_seconds(conn) >= STARTUP_GRACE_SECONDS


def team_done(conn) -> bool:
    """True when every active agent has finished its slice — the barrier.

    Crashed/silent teammates age out of the active window and stop counting, so
    a dead agent can't wedge the team forever.
    """
    if not startup_guard_satisfied(conn):
        return False
    active = active_agents(conn)
    if not active:
        return False
    return all((a["status"] or "") == DONE_STATUS for a in active)


# --------------------------------------------------------------------------- #
# Messaging
# --------------------------------------------------------------------------- #
def send(conn, sender: str, body: str, session_id: str | None = None,
         kind: str = "chat") -> int:
    mentions = parse_mentions(body)
    cur = conn.execute(
        "INSERT INTO messages(ts, sender, session_id, kind, body, mentions) "
        "VALUES (?,?,?,?,?,?)",
        (now_iso(), sender, session_id, kind, body, json.dumps(mentions)),
    )
    conn.commit()
    return cur.lastrowid


def messages_since(conn, after_id: int, limit: int | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM messages WHERE id > ? ORDER BY id ASC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, (after_id,)).fetchall()


def recent_messages(conn, limit: int = 20) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return list(reversed(rows))


def mark_read(conn, session_id: str, up_to_id: int) -> None:
    conn.execute(
        "UPDATE agents SET last_read_id = MAX(last_read_id, ?) WHERE session_id = ?",
        (up_to_id, session_id),
    )
    conn.commit()


def unread_for(conn, agent_row, include_own: bool = False) -> list[sqlite3.Row]:
    msgs = messages_since(conn, agent_row["last_read_id"])
    if not include_own:
        msgs = [m for m in msgs if m["sender"] != agent_row["handle"]]
    return msgs


def format_message(m: sqlite3.Row, highlight: str | None = None) -> str:
    mentions = json.loads(m["mentions"] or "[]")
    arrow = ""
    if mentions:
        arrow = " → " + " ".join("@" + x for x in mentions)
    tag = "" if m["kind"] == "chat" else f" ({m['kind']})"
    star = ""
    if highlight and highlight.lower() in [x.lower() for x in mentions]:
        star = "★ "
    return f"{star}[#{m['id']} {_hhmm(m['ts'])} {m['sender']}{arrow}]{tag} {m['body']}"


def format_messages(msgs, highlight: str | None = None) -> str:
    return "\n".join(format_message(m, highlight) for m in msgs)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve_for_cli(conn, args):
    """Resolve the acting agent from --session / --from, registering if needed."""
    session_id = getattr(args, "session", None)
    handle = getattr(args, "from_handle", None)
    a = resolve_agent(conn, session_id, handle)
    if a:
        return a
    if session_id:
        register(conn, session_id, cwd=os.getcwd(), pid=os.getpid(), handle=handle)
        return agent_by_session(conn, session_id)
    return None


def cmd_init(args):
    conn = connect()
    print(f"groupchat initialized at {db_path()}")
    conn.close()


def cmd_register(args):
    conn = connect()
    h = register(conn, args.session, cwd=args.cwd or os.getcwd(),
                 pid=args.pid, handle=args.from_handle, status=args.status)
    print(h)
    conn.close()


def cmd_whoami(args):
    conn = connect()
    a = resolve_agent(conn, getattr(args, "session", None), getattr(args, "from_handle", None))
    print(a["handle"] if a else "(unregistered)")
    conn.close()


def cmd_send(args):
    conn = connect()
    a = _resolve_for_cli(conn, args)
    sender = a["handle"] if a else (args.from_handle or "anon")
    body = args.message if isinstance(args.message, str) else " ".join(args.message)
    body = body.strip()
    if not body:
        print("nothing to send (empty message)", file=sys.stderr)
        conn.close()
        return 1
    mid = send(conn, sender, body, session_id=(a["session_id"] if a else None))
    print(f"sent #{mid} as {sender}")
    conn.close()
    return 0


def cmd_read(args):
    conn = connect()
    a = _resolve_for_cli(conn, args)
    if not a:
        print("(no agent identity; pass --session or --from)", file=sys.stderr)
        conn.close()
        return 1
    msgs = unread_for(conn, a, include_own=args.include_own)
    if not msgs:
        print("(no new messages)")
    else:
        print(format_messages(msgs, highlight=a["handle"]))
        if not args.peek:
            mark_read(conn, a["session_id"], msgs[-1]["id"])
    conn.close()
    return 0


def cmd_inbox(args):
    conn = connect()
    a = _resolve_for_cli(conn, args)
    if not a:
        print("(no agent identity; pass --session or --from)", file=sys.stderr)
        conn.close()
        return 1
    msgs = [m for m in unread_for(conn, a)
            if a["handle"].lower() in [x.lower() for x in json.loads(m["mentions"] or "[]")]]
    if not msgs:
        print("(no unread mentions)")
    else:
        print(format_messages(msgs, highlight=a["handle"]))
        if not args.peek:
            mark_read(conn, a["session_id"], msgs[-1]["id"])
    conn.close()
    return 0


def cmd_log(args):
    conn = connect()
    msgs = recent_messages(conn, args.limit)
    print(format_messages(msgs) if msgs else "(no messages yet)")
    conn.close()


def _fmt_count(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def cmd_tokens(args):
    conn = connect()
    rows = active_agents(conn) if not args.all else conn.execute(
        "SELECT * FROM agents ORDER BY handle").fetchall()
    if not rows:
        print("(no agents)")
        conn.close()
        return 0
    print("~approx (from local transcript)")
    tot = {k: 0 for k in TOKEN_FIELDS}
    for r in rows:
        for k in TOKEN_FIELDS:
            tot[k] += int(r[k] or 0)
        print(f"{r['handle']:<10} out {_fmt_count(r['out_tokens'])}  "
              f"in {_fmt_count(r['in_tokens'])}  "
              f"cache-read {_fmt_count(r['cache_read_tokens'])}  "
              f"cache-create {_fmt_count(r['cache_create_tokens'])}")
    print(f"{'TEAM':<10} out {_fmt_count(tot['out_tokens'])}  "
          f"in {_fmt_count(tot['in_tokens'])}  "
          f"cache-read {_fmt_count(tot['cache_read_tokens'])}  "
          f"cache-create {_fmt_count(tot['cache_create_tokens'])}")
    conn.close()
    return 0


def cmd_who(args):
    conn = connect()
    rows = active_agents(conn) if not args.all else conn.execute(
        "SELECT * FROM agents ORDER BY handle").fetchall()
    if not rows:
        print("(no agents)")
    for r in rows:
        flag = "●" if _is_active(r["last_seen"]) else "○"
        status = f" — {r['status']}" if r["status"] else ""
        cwd = f"  [{r['cwd']}]" if r["cwd"] else ""
        toks = f" · {_fmt_count(r['out_tokens'])} out" if r["out_tokens"] else ""
        print(f"{flag} {r['handle']}{status}{cwd}  (seen {_hhmm(r['last_seen'] or '')}){toks}")
    conn.close()


def cmd_heartbeat(args):
    conn = connect()
    register(conn, args.session, cwd=args.cwd, pid=args.pid, status=args.status)
    conn.close()


def cmd_done(args):
    """Mark this agent's slice complete. The Stop hook does this automatically;
    this is the explicit version for clarity."""
    conn = connect()
    a = _resolve_for_cli(conn, args)
    if not a:
        print("(no agent identity; pass --session or --from)", file=sys.stderr)
        conn.close()
        return 1
    set_status(conn, a["session_id"], DONE_STATUS)
    done = team_done(conn)
    print(f"{a['handle']} marked done."
          + (" Team is all done." if done else " Waiting for teammates."))
    conn.close()
    return 0


def cmd_expect(args):
    """Declare how many agents this run should have (closes the startup race
    exactly). With no number, print the current expectation."""
    conn = connect()
    if args.n is None:
        size = expected_team_size(conn)
        print(f"expected team size: {size}" if size else "expected team size: (unset — using startup grace)")
    else:
        set_meta(conn, "team_size", str(args.n))
        print(f"expected team size set to {args.n}")
    conn.close()
    return 0


# Hook wiring appended to a target repo's .claude/settings.json by `install`.
HOOK_ENTRIES = {
    "SessionStart": 'python3 "$CLAUDE_PROJECT_DIR/.groupchat/hooks/session_start.py"',
    "UserPromptSubmit": 'python3 "$CLAUDE_PROJECT_DIR/.groupchat/hooks/user_prompt_submit.py"',
    "Stop": 'python3 "$CLAUDE_PROJECT_DIR/.groupchat/hooks/stop.py"',
}

# Extra per-hook options merged alongside the command. The Stop hook parks a
# finished agent at the team barrier, so it needs a long timeout (it returns on
# its own before this) and a status line while it blocks.
HOOK_OPTIONS = {
    "UserPromptSubmit": {"timeout": 15},
    "Stop": {"timeout": 600,
             "statusMessage": "⏳ waiting for teammates at the group-chat barrier…"},
}


def _merge_settings(settings: dict) -> tuple[dict, int]:
    """Idempotently add our hook commands to a settings dict. Returns (dict, added)."""
    import copy
    settings = copy.deepcopy(settings) if settings else {}
    hooks = settings.setdefault("hooks", {})
    added = 0
    for event, command in HOOK_ENTRIES.items():
        groups = hooks.setdefault(event, [])
        already = any(
            h.get("command") == command
            for g in groups for h in g.get("hooks", [])
        )
        if not already:
            entry = {"type": "command", "command": command}
            entry.update(HOOK_OPTIONS.get(event, {}))
            groups.append({"hooks": [entry]})
            added += 1
    return settings, added


def cmd_install(args):
    import shutil
    src = os.path.dirname(os.path.abspath(__file__))          # this .groupchat dir
    target_root = os.path.abspath(args.target)
    dst = os.path.join(target_root, ".groupchat")

    if os.path.abspath(dst) != src:
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(os.path.join(src, "chat.py"), os.path.join(dst, "chat.py"))
        gi = os.path.join(src, ".gitignore")
        if os.path.exists(gi):
            shutil.copy2(gi, os.path.join(dst, ".gitignore"))
        hooks_dst = os.path.join(dst, "hooks")
        os.makedirs(hooks_dst, exist_ok=True)
        for f in os.listdir(os.path.join(src, "hooks")):
            if f.endswith(".py"):
                shutil.copy2(os.path.join(src, "hooks", f), os.path.join(hooks_dst, f))
        print(f"copied group-chat files -> {dst}")
    else:
        print(f"using existing files at {dst}")

    settings_path = os.path.join(target_root, ".claude", "settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    existing = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as fh:
                existing = json.load(fh)
        except Exception:
            print(f"warning: {settings_path} is not valid JSON; refusing to overwrite",
                  file=sys.stderr)
            return 1
    merged, added = _merge_settings(existing)
    with open(settings_path, "w") as fh:
        json.dump(merged, fh, indent=2)
        fh.write("\n")
    print(f"{'added' if added else 'no new'} hook(s) in {settings_path}"
          + (f" (+{added})" if added else ""))
    print("Done. Open Claude Code in this repo (restart the session) and the "
          "group chat is live for every instance.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chat", description="Group chat bus for parallel Claude Code instances.")
    sub = p.add_subparsers(dest="command", required=True)

    def add_identity(sp):
        sp.add_argument("--session", help="Claude session id (preferred identity)")
        sp.add_argument("--from", dest="from_handle", help="agent handle to act as")

    sp = sub.add_parser("init", help="create the chat database")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("register", help="register/refresh this agent, print its handle")
    add_identity(sp)
    sp.add_argument("--cwd"); sp.add_argument("--pid", type=int); sp.add_argument("--status")
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("whoami", help="print this agent's handle")
    add_identity(sp)
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("send", aliases=["say"], help="post a message (use @handle to mention)")
    add_identity(sp)
    sp.add_argument("message", nargs="+", help="message text")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("read", help="show unread messages and advance read cursor")
    add_identity(sp)
    sp.add_argument("--peek", action="store_true", help="do not advance the read cursor")
    sp.add_argument("--include-own", action="store_true", help="include your own messages")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("inbox", help="show unread messages that @mention you")
    add_identity(sp)
    sp.add_argument("--peek", action="store_true")
    sp.set_defaults(func=cmd_inbox)

    sp = sub.add_parser("log", help="show recent message history")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("who", help="list agents in the room")
    sp.add_argument("--all", action="store_true", help="include inactive agents")
    sp.set_defaults(func=cmd_who)

    sp = sub.add_parser("tokens", help="show approximate token usage per agent")
    sp.add_argument("--all", action="store_true", help="include inactive agents")
    sp.set_defaults(func=cmd_tokens)

    sp = sub.add_parser("heartbeat", help="refresh last-seen / status")
    add_identity(sp)
    sp.add_argument("--cwd"); sp.add_argument("--pid", type=int); sp.add_argument("--status")
    sp.set_defaults(func=cmd_heartbeat)

    sp = sub.add_parser("done", help="mark your slice complete (wait at the team barrier)")
    add_identity(sp)
    sp.set_defaults(func=cmd_done)

    sp = sub.add_parser("expect", help="declare/show the expected number of agents this run")
    sp.add_argument("n", nargs="?", type=int, help="expected agent count (omit to show)")
    sp.set_defaults(func=cmd_expect)

    sp = sub.add_parser("install", help="install group chat into a target repo")
    sp.add_argument("target", nargs="?", default=".", help="target repo root (default: cwd)")
    sp.set_defaults(func=cmd_install)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rc = args.func(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
