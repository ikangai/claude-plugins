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
# Reserved mention: the human operator. Hub-and-spoke routing (the hierarchy
# substrate) funnels worker→human questions through the lead, so @human is special
# — it can never be an agent handle, and only the lead may address it directly.
HUMAN_TOKEN = "human"
RESERVED_HANDLES = frozenset({HUMAN_TOKEN})
# Markdown code spans (matched backtick runs, inline or fenced). The @human guard
# leaves a *quoted* token alone so writing `@human` in docs/help/chat is not an
# escalation — backreference \1 requires the closing run to match the opening run.
CODE_SPAN_RE = re.compile(r"(`+)(?:.*?)\1", re.DOTALL)
# Rule citations (governance): case-SENSITIVE `R` + a non-zero-leading number as a
# whole token, with MENTION_RE's boundary guard. parse_rules() also rejects the
# R-squared family (no R0, no leading zeros, no `R2-squared`).
RULE_RE = re.compile(r"(?<![\w/])R([1-9]\d*)\b")
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


def repo_root() -> str:
    """Repo root that holds CONSTITUTION.md — the parent of the room dir, so the
    durable law sits beside ``.groupchat`` and resolves at the SAME git anchor as
    the bus. Mirrors ``store_dir()``'s resolution chain minus the trailing
    ``.groupchat`` join (NOT ``git rev-parse --show-toplevel``, which is per-worktree)."""
    return os.path.dirname(store_dir())


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
        CREATE TABLE IF NOT EXISTS rule_cites (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT    NOT NULL,
            rule_id    TEXT    NOT NULL,
            sender     TEXT    NOT NULL,
            message_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rule_cites_rule ON rule_cites(rule_id);
        CREATE TABLE IF NOT EXISTS motions (
            id          INTEGER PRIMARY KEY,
            ts          TEXT    NOT NULL,
            proposer    TEXT    NOT NULL,
            target      TEXT    NOT NULL,
            op          TEXT    NOT NULL,
            change      TEXT,
            because     TEXT    NOT NULL,
            base_text   TEXT,
            new_id      TEXT,
            status      TEXT    NOT NULL DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS votes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            TEXT NOT NULL,
            motion_id     INTEGER NOT NULL,
            voter_session TEXT NOT NULL,
            voter_handle  TEXT NOT NULL,
            vote          TEXT NOT NULL
        );
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
    """Handles @mentioned in ``body``, EXCLUDING any inside a markdown code span:
    quoting `` `@ada` `` in docs/help/chat is *discussing* the handle, not pinging it.
    This is the single home for that rule — routing, the inbox, the Stop @mention
    block, and escalation detection all key off the stored mentions, so a quoted
    handle never spuriously pings, wakes, blocks, or escalates anyone (the dogfooding
    sharp-edge from chat #85/#90). (``_code_span_ranges``/``_in_spans`` are defined
    with the hierarchy helpers below; resolved at call time.)"""
    spans = _code_span_ranges(body)
    return sorted({m.group(1).lower() for m in MENTION_RE.finditer(body)
                   if not _in_spans(m.start(), spans)})


def parse_rules(body: str) -> list[str]:
    """Harvest rule-id citations (``R<n>``) from a message body. A tolerant signal,
    not a ledger: case-sensitive, boundary-guarded, no R0/leading-zero, and skips
    the R-squared family (trailing ``=``/``^``/``²`` or ``-squared``/`` squared``)
    so chatter like ``R2=0.99`` or ``R2-squared`` is not a cite."""
    out = set()
    for m in RULE_RE.finditer(body):
        tail = body[m.end():m.end() + 9]
        if tail[:1] in ("=", "^", "²") or re.match(r"[-\s]squared\b", tail):
            continue
        out.add("R" + m.group(1))
    return sorted(out)


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


def _env_int(name: str, default: int | None = None) -> int | None:
    """Parse an int from env ``name``; return ``default`` when unset/empty/invalid.

    ONE definition serves both call styles — the barrier callers
    (``expected_team_size`` / ``max_park_seconds``) omit ``default`` and treat the
    resulting ``None`` as "unset", while the constitution callers pass an explicit
    fallback. A second, two-arg redefinition of this function once shadowed the
    original and made every no-default caller raise ``TypeError`` — which
    ``stop.py`` swallowed (fail-open), silently killing the team barrier. Keep it
    single. (See .dev-diary/2026-06-07-test-harness-and-the-dead-barrier.md.)
    """
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


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
    # Only *active* handles are taken — a closed/idle session's name is free to
    # recycle, so the pool doesn't march forward (ada→turing→…→agent-N) and the
    # agents table doesn't grow unbounded. An active session never loses its handle.
    # Reserved names (e.g. "human") stay taken so the @human escalation token can
    # never collide with a real agent handle.
    taken = {a["handle"] for a in active_agents(conn)} | set(RESERVED_HANDLES)
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
        # Reclaim a recycled name: if this handle is held by an INACTIVE agent
        # (a closed/idle session), drop the dead row so the UNIQUE INSERT below
        # succeeds — that's how a restarted shell keeps its GROUPCHAT_HANDLE and how
        # pool names get reused. _assign_handle never returns an actively-held handle,
        # so we only ever delete a dead identity; the `_is_active` guard means a race
        # that revived the holder is left alone and the INSERT just retries.
        stale = conn.execute(
            "SELECT session_id, last_seen FROM agents WHERE handle = ?", (h,)
        ).fetchone()
        if stale and not _is_active(stale["last_seen"]):
            # Re-assert staleness IN the DELETE (not just the check above): if the
            # holder revived in between — a TOCTOU where its own re-register /
            # set_status / mark_read refreshed last_seen — the guarded DELETE matches
            # 0 rows, the INSERT below collides on the UNIQUE handle, and the retry
            # loop falls through to a different name. So an active session can never
            # lose its handle (or its read cursor) to a newcomer reusing the name.
            cutoff = datetime.fromtimestamp(
                _now_epoch() - ACTIVE_WINDOW_SECONDS, timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur = conn.execute(
                "DELETE FROM agents WHERE session_id = ? AND (last_seen IS NULL OR last_seen < ?)",
                (stale["session_id"], cutoff),
            )
            # Only if a row was actually retired, and it was the lead, drop the stale
            # pointer so a name-reuser doesn't inherit leadership — resolve_lead's
            # floor re-elects instead. (Routing reads meta['lead']; hierarchy layer.)
            if cur.rowcount and (get_meta(conn, "lead") or "").strip().lower() == h:
                del_meta(conn, "lead")
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
# Hierarchy substrate — lead resolution & @human routing (model-agnostic)
# --------------------------------------------------------------------------- #
# Human contact is hub-and-spoke: only the lead may address @human; a worker's
# @human is redirected to @<lead>, so clarifications funnel to one node that batches
# and escalates. This file owns the READ side only — resolve_lead() (who is the lead
# right now) and the send-guard that routes on it. The WRITE side (claiming /
# handing off / releasing the lead role, which set meta['lead']) is a separate,
# decoupled track; the two never co-edit a function. Resolution order is agreed with
# that track (chat #20): the shared pointer wins, then an operator env override, then
# a deterministic FLOOR so a lead ALWAYS exists and fails over the instant one ages
# out — that floor is what kills the single-point-of-failure the research flagged.
def resolve_lead(conn) -> str | None:
    """The handle of the agent who currently owns human contact, or None only when
    no agent is active (degenerate → flat, @human passes through). Order:

    1. ``meta['lead']``    — the canonical shared pointer (a claim/designation/
       election result), **if its holder is currently active**;
    2. ``$GROUPCHAT_LEAD`` — an operator env override, if its holder is active;
    3. **floor** — the earliest-joined active agent (tie broken by handle): a
       deterministic, zero-config default that guarantees a live lead and instant
       failover. The shared pointer is honoured only while alive, so a parked/dead
       lead silently hands off to the floor — no SPOF, no stale routing.

    Pure/read-only: a stale ``meta['lead']`` is *not* cleared here (the write track
    owns that); read just falls through to the floor. Keying on the active set means
    the lead is always a real agent who can actually receive the routed @mention."""
    acts = active_agents(conn)
    if not acts:
        return None
    active_handles = {a["handle"] for a in acts}
    pointer = (get_meta(conn, "lead") or "").strip().lower()
    if pointer and pointer in active_handles:
        return pointer
    env = (os.environ.get("GROUPCHAT_LEAD") or "").strip().lower()
    if env and env in active_handles:
        return env
    return min(acts, key=lambda a: (a["first_seen"] or "", a["handle"]))["handle"]


def _code_span_ranges(body: str) -> list[tuple[int, int]]:
    """Character ranges covered by markdown inline-code / fenced spans (matched
    backtick runs). Used to leave a *quoted* escalation token untouched — writing
    ``@human`` in docs/help/chat is documentation, not a request to the operator."""
    return [(m.start(), m.end()) for m in CODE_SPAN_RE.finditer(body)]


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def _redirect_mention(body: str, frm: str, to: str) -> str:
    """Rewrite the full mention ``@frm`` → ``@to`` wherever it appears OUTSIDE a code
    span, using the exact mention grammar (so ``@human`` is rewritten but
    ``@humanity`` / ``@human-x`` — distinct handles — are left alone, and a quoted
    `` `@human` `` stays literal). Other mentions pass through untouched."""
    frm = frm.lower()
    spans = _code_span_ranges(body)
    return MENTION_RE.sub(
        lambda m: "@" + to if (m.group(1).lower() == frm
                               and not _in_spans(m.start(), spans)) else m.group(0),
        body)


def _has_unquoted_human(body: str) -> bool:
    """True iff the body contains a real (non-code-span) ``@human`` escalation."""
    spans = _code_span_ranges(body)
    return any(m.group(1).lower() == HUMAN_TOKEN and not _in_spans(m.start(), spans)
               for m in MENTION_RE.finditer(body))


def human_redirect_target(conn, sender: str, body: str) -> str | None:
    """The lead handle a worker's @human should be redirected to, or None when no
    redirect applies (no active lead, the sender IS the lead, or no *unquoted*
    @human in the body — a `` `@human` `` mention in docs/help text never escalates).
    Pure/read-only — both the send guard and the CLI feedback note call this."""
    lead = resolve_lead(conn)
    if not lead:
        return None  # flat mode — unchanged
    if (sender or "").strip().lower() == lead:
        return None  # the lead owns the human channel
    if not _has_unquoted_human(body):
        return None
    return lead


def _apply_human_guard(conn, sender: str, body: str) -> str:
    """Hub-and-spoke send guard: rewrite a worker's @human → @<lead>. No-op unless
    a redirect applies (see human_redirect_target)."""
    target = human_redirect_target(conn, sender, body)
    return _redirect_mention(body, HUMAN_TOKEN, target) if target else body


def open_escalations(conn, lead: str) -> list[int]:
    """Message-ids of the lead's @human escalations the operator still owes a reply
    on — the read side of the P2 lead-done gate. Walking chat chronologically: each
    *unquoted* ``@human`` message *by the lead* opens an escalation; an operator
    message (``sender == HUMAN_TOKEN``) that @mentions the lead afterwards clears the
    whole queue (one batched reply answers every pending question, per chat #39).

    The "unquoted" check is load-bearing and mirrors the send-guard: a `` `@human` ``
    inside a code span is the lead *documenting* the token, not asking the operator.
    Counting quoted tokens here was a real barrier-wedge — a lead discussing the
    feature would gate itself on phantom escalations and the team could never reach
    done (gauss #85). parse_mentions/the stored ``mentions`` column ignore code
    spans, so we re-derive from the body via _has_unquoted_human.

    The Stop hook parks a lead while this is non-empty and wakes it via the existing
    @mention path when the operator replies — so the team never tears down with a
    question to the human still unanswered, yet no new state or second cursor is
    introduced. A worker can't appear here: its @human is rewritten to @<lead>
    before storage, so only the lead ever authors an @human escalation."""
    lead = (lead or "").strip().lower()
    if not lead:
        return []
    rows = conn.execute(
        "SELECT id, sender, body, mentions FROM messages WHERE kind='chat' ORDER BY id ASC"
    ).fetchall()
    open_ids: list[int] = []
    for r in rows:
        if r["sender"] == lead and _has_unquoted_human(r["body"]):
            open_ids.append(r["id"])              # a real (unquoted) escalation by the lead
        elif r["sender"] == HUMAN_TOKEN and lead in json.loads(r["mentions"] or "[]"):
            open_ids = []                         # operator answered → queue cleared
    return open_ids


# --------------------------------------------------------------------------- #
# Hierarchy substrate — WRITE side (lead claim / hand-off / release)
# --------------------------------------------------------------------------- #
# The decoupled twin of resolve_lead(): the read side honours meta['lead'] only
# while its holder is active, so the write side never has to unset a crashed lead
# — a dead pointer simply fails over to the floor on the next read. Setting the
# pointer is the ONLY write; there is no role column to keep in sync.
def set_lead(conn, handle: str) -> str:
    """Point meta['lead'] at ``handle`` (claim / designate / hand-off). Returns the
    normalized handle. Honoured by resolve_lead() only while that handle is active."""
    h = (handle or "").strip().lower()
    if not h:
        raise ValueError("lead handle must be non-empty")
    if h in RESERVED_HANDLES:
        raise ValueError(f"'{h}' is reserved and cannot be the lead")
    set_meta(conn, "lead", h)
    conn.commit()
    return h


def clear_lead(conn) -> None:
    """Release the designated lead → resolve_lead() falls back to the deterministic
    floor (the earliest-joined active agent)."""
    del_meta(conn, "lead")
    conn.commit()


# --------------------------------------------------------------------------- #
# Messaging
# --------------------------------------------------------------------------- #
def send(conn, sender: str, body: str, session_id: str | None = None,
         kind: str = "chat") -> int:
    # Hub-and-spoke routing: a worker's @human is funnelled to the lead before the
    # message is stored, so the mention that blocks/surfaces is @<lead>, not @human.
    # Flat mode (no lead) and non-chat kinds are untouched.
    if kind == "chat":
        body = _apply_human_guard(conn, sender, body)
    # Only chat messages carry @mentions: motions/votes/system must not block a
    # teammate's Stop or gate the barrier (they ride the bus without nagging).
    mentions = parse_mentions(body) if kind == "chat" else []
    cur = conn.execute(
        "INSERT INTO messages(ts, sender, session_id, kind, body, mentions) "
        "VALUES (?,?,?,?,?,?)",
        (now_iso(), sender, session_id, kind, body, json.dumps(mentions)),
    )
    msg_id = cur.lastrowid
    # Harvest rule citations — only from real chat messages, and never from one
    # that quotes the constitution itself (self-inflation). Motions, votes, and
    # system announcements naming a rule must NOT count as cites (a motion would
    # otherwise shield the very rule it aims to change; a ratify announcement would
    # self-cite). Cites are the advisory behavioral signal the repeal review runs on.
    if kind == "chat" and "<!-- CONSTITUTION:" not in body:
        for rid in parse_rules(body):
            conn.execute(
                "INSERT INTO rule_cites(ts, rule_id, sender, message_id) "
                "VALUES (?,?,?,?)",
                (now_iso(), rid, sender, msg_id),
            )
    conn.commit()
    return msg_id


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
    # Tell a worker when its @human was funnelled to the lead (the guard rewrites
    # the body inside send(); we recompute the target here purely for the notice).
    redirect = human_redirect_target(conn, sender, body)
    mid = send(conn, sender, body, session_id=(a["session_id"] if a else None))
    print(f"sent #{mid} as {sender}")
    if redirect:
        print(f"note: @human redirected to @{redirect} "
              f"(you are not the lead — questions funnel through the lead)")
    conn.close()
    return 0


def cmd_questions(args):
    """Operator view: the lead's open @human escalations awaiting your answer.
    Closes the discoverability half of the loop — the human sees, at a glance, what
    the fleet needs from them and how to reply."""
    conn = connect()
    lead = resolve_lead(conn)
    if not lead:
        print("(no active lead — no escalations)")
        conn.close()
        return 0
    ids = open_escalations(conn, lead)
    if not ids:
        print(f"(no open escalations — @{lead} owes you nothing)")
        conn.close()
        return 0
    print(f"open escalation(s) from @{lead}  —  answer with: "
          f'answer <id> "..."')
    for mid in ids:
        row = conn.execute(
            "SELECT id, ts, body FROM messages WHERE id=?", (mid,)).fetchone()
        if row:
            print(f"  #{row['id']} {_hhmm(row['ts'])}  {row['body']}")
    conn.close()
    return 0


def cmd_answer(args):
    """Operator convenience: answer a lead's @human escalation. Posts an @<lead>
    reply *as the operator* (sender 'human'), which clears the lead's escalation
    queue and wakes it via the existing @mention path. Sugar over
    ``send --from human "@<lead> ..."`` — but it targets the escalation's author and
    refuses non-escalations, keeping the hub-and-spoke discipline intact."""
    conn = connect()
    mid = args.msg_id
    row = conn.execute(
        "SELECT sender, mentions FROM messages WHERE id=?", (mid,)).fetchone()
    if not row:
        print(f"no message #{mid}", file=sys.stderr)
        conn.close()
        return 1
    ms = json.loads(row["mentions"] or "[]")
    if HUMAN_TOKEN not in ms:
        print(f"#{mid} is not an @human escalation (mentions: {ms or 'none'}). "
              f'Reply directly with: send --from human "@<handle> ..."',
              file=sys.stderr)
        conn.close()
        return 1
    lead = row["sender"]  # the escalation's author is the lead who asked
    text = args.message if isinstance(args.message, str) else " ".join(args.message)
    text = text.strip()
    if not text:
        print("nothing to answer (empty message)", file=sys.stderr)
        conn.close()
        return 1
    new_id = send(conn, HUMAN_TOKEN, f"@{lead} [re #{mid}] {text}", kind="chat")
    print(f"answered #{mid} → @{lead} (sent #{new_id})")
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
    # Mark a DELIBERATE lead (explicit claim/designation/env) in the roster — not the
    # implicit floor, so flat / floor-only rooms stay uncluttered (no surprise crown).
    lead = resolve_lead(conn)
    _ptr = (get_meta(conn, "lead") or "").strip().lower()
    _env = (os.environ.get("GROUPCHAT_LEAD") or "").strip().lower()
    explicit_lead = lead if (lead and lead in (_ptr, _env)) else None
    for r in rows:
        flag = "●" if _is_active(r["last_seen"]) else "○"
        crown = " ★lead" if explicit_lead and r["handle"] == explicit_lead else ""
        status = f" — {r['status']}" if r["status"] else ""
        cwd = f"  [{r['cwd']}]" if r["cwd"] else ""
        toks = f" · {_fmt_count(r['out_tokens'])} out" if r["out_tokens"] else ""
        print(f"{flag} {r['handle']}{crown}{status}{cwd}  (seen {_hhmm(r['last_seen'] or '')}){toks}")
    conn.close()


def cmd_lead(args):
    """Show / claim / hand off / release the lead — the WRITE side of hub-and-spoke
    @human routing. Forms:
        lead                       show who's lead and why (claim / env / floor)
        lead <handle>              designate / hand off to <handle>
        lead --claim --from <me>   claim the lead for yourself (emergent self-claim)
        lead --release             step down → the deterministic floor takes over
    A human can also designate out-of-band via env GROUPCHAT_LEAD, or by ratifying an
    election. resolve_lead() (read side) honours the pointer only while its holder is
    active, so a parked/crashed lead auto-fails-over to the floor — no manual cleanup."""
    conn = connect()
    if getattr(args, "release", False):
        prev = get_meta(conn, "lead")
        clear_lead(conn)
        now = resolve_lead(conn)
        send(conn, "system",
             f"Lead released{f' (was @{prev})' if prev else ''} — "
             + (f"the floor lead is now @{now} (earliest-joined active)."
                if now else "no agents active; flat mode."),
             kind="system")
        print(f"released the lead → floor is now @{now}" if now
              else "released the lead (no active agents)")
        conn.close()
        return 0
    target = None
    if getattr(args, "claim", False):
        a = _resolve_for_cli(conn, args)
        if not a:
            print("lead --claim needs your identity: pass --from <your handle> "
                  "(or --session <id>)", file=sys.stderr)
            conn.close()
            return 1
        target = a["handle"]
    elif getattr(args, "handle", None):
        target = args.handle
    if target:
        # The lead must be an *active* agent that can actually receive routed
        # @mentions. resolve_lead honors the pointer only while its holder is active,
        # so designating an inactive/unknown handle would silently fall through to the
        # floor — yet still broadcast "route to @<h>", and a worker addressing @<h>
        # directly would be lost (audit #70). Refuse it (reserved/empty still get
        # set_lead's specific message).
        _t = (target or "").strip().lower()
        if _t and _t not in RESERVED_HANDLES \
                and _t not in {x["handle"] for x in active_agents(conn)}:
            print(f"@{_t} is not an active agent — can't be the lead. A lead must be "
                  f"active so a worker's @human reaches it; an inactive pointer would "
                  f"silently fall through to the floor (see `lead` / `who`).",
                  file=sys.stderr)
            conn.close()
            return 1
        try:
            h = set_lead(conn, target)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            conn.close()
            return 1
        send(conn, "system",
             f"@{h} is now the lead. Workers: your @human messages route to @{h} — the "
             f"single point of contact who batches questions for the operator.",
             kind="system")
        print(f"lead is now @{h}")
        conn.close()
        return 0
    # show
    lead = resolve_lead(conn)
    if not lead:
        print("no active agents — flat (no lead)")
        conn.close()
        return 0
    pointer = (get_meta(conn, "lead") or "").strip().lower()
    env = (os.environ.get("GROUPCHAT_LEAD") or "").strip().lower()
    actives = {x["handle"] for x in active_agents(conn)}
    if pointer and pointer in actives:
        why = "claimed / designated"
    elif env and env in actives:
        why = "operator env GROUPCHAT_LEAD"
    else:
        why = "floor — earliest-joined active (emergent default)"
    print(f"lead: @{lead}  [{why}]")
    if pointer and pointer not in actives:
        print(f"  note: designated @{pointer} is inactive — failed over to the floor")
    conn.close()
    return 0


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


# --------------------------------------------------------------------------- #
# Constitution (governance layer) — Phase 1: the document
# --------------------------------------------------------------------------- #
CONST_FILENAME = "CONSTITUTION.md"
_CORE_BEGIN = "<!-- CONSTITUTION:CORE:BEGIN -->"
_CORE_END = "<!-- CONSTITUTION:CORE:END -->"
_ART_BEGIN = "<!-- CONSTITUTION:ARTICLES:BEGIN -->"
_ART_END = "<!-- CONSTITUTION:ARTICLES:END -->"
_CONST_ZONES = {"core": (_CORE_BEGIN, _CORE_END), "articles": (_ART_BEGIN, _ART_END)}
_CORE_ID_RE = re.compile(r"^###\s+(C\d+)\b[ \t]*[—:-]?[ \t]*(.*)$", re.MULTILINE)
_ART_ID_RE = re.compile(r"^###\s+(R\d+)\b[ \t]*[—:-]?[ \t]*(.*)$", re.MULTILINE)
_PROV_RE = re.compile(r"<!--\s*meta:\s*(.*?)\s*-->")


def constitution_path() -> str:
    return os.path.join(repo_root(), CONST_FILENAME)


def _starter_constitution(today: str) -> str:
    return (
        "# Repo Constitution\n\n"
        f"{_CORE_BEGIN}\n"
        "## Core (entrenched — amendable only by a human, never by the parliament)\n\n"
        "### C1 — The human is the final authority\n"
        "No automated process may modify this Core section or apply an amendment to\n"
        "the Articles without a human committing it.\n\n"
        "### C2 — Hooks fail open\n"
        "A coordination hook must never crash or block a session on error.\n\n"
        "### C3 — Writes are single-threaded\n"
        "Agents add intelligence, not concurrent edits. One writer per change.\n\n"
        "### C4 — The amendment procedure\n"
        "Articles change only by: a motion citing evidence -> an advisory vote -> a\n"
        "human ratifying the proposed diff after reading the cited evidence. Core\n"
        "changes are out of scope for this procedure.\n"
        f"{_CORE_END}\n\n"
        f"{_ART_BEGIN}\n"
        "## Articles (amendable by the parliament, ratified by a human)\n\n"
        "### R1 — Announce before you touch a file\n"
        "Post \"starting on <path>\" before editing, so two agents don't collide.\n"
        f"<!-- meta: id=R1 added={today} by=human ratified={today} amended= source= -->\n\n"
        "### R2 — Converge, don't fork\n"
        "If two agents propose overlapping designs, one retracts. Do not merge into\n"
        "an average; pick one and make it the contract.\n"
        f"<!-- meta: id=R2 added={today} by=human ratified={today} amended= source= -->\n"
        f"{_ART_END}\n"
    )


def _zone_span(text: str, which: str):
    """Return ``(content_start, content_end)`` offsets for a zone's content (between
    the marker LINES, exclusive). Markers must be their own (stripped) line, so a
    marker string quoted inside body text can't be mistaken for the boundary."""
    begin, end = _CONST_ZONES[which]
    starts, pos = [], 0
    lines = text.splitlines(keepends=True)
    for ln in lines:
        starts.append(pos)
        pos += len(ln)
    bi = ei = None
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if s == begin and bi is None:
            bi = idx
        elif s == end and bi is not None and ei is None:
            ei = idx
    if bi is None or ei is None or ei <= bi:
        return None
    return (starts[bi] + len(lines[bi]), starts[ei])


def _const_zone(text: str, which: str):
    span = _zone_span(text, which)
    return text[span[0]:span[1]] if span else None


def _parse_prov(segment: str) -> dict:
    """Parse the LAST ``<!-- meta: k=v … -->`` in a block into a dict. Provenance
    lives at the block's foot, so taking the last comment stops a body example from
    poisoning it. Whitespace-separated ``key=value`` tokens with possibly-EMPTY
    values (e.g. ``amended=``) — split on the first ``=`` per token so an empty
    value can't swallow the next key."""
    pms = list(_PROV_RE.finditer(segment))
    prov = {}
    if pms:
        for tok in pms[-1].group(1).split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                prov[k] = v
    return prov


def parse_constitution(text: str) -> dict:
    """Split the two zones and parse Core (``C<n>``) + Articles (``R<n>``) with
    provenance. Returns ``{ok, errors, core:[{id,title}], articles:[{id,title,prov}]}``.
    Loud (ok=False) on missing/malformed markers or a reused rule id — the CLI
    surfaces that; hooks call this best-effort and stay silent on any problem."""
    res = {"ok": True, "errors": [], "core": [], "articles": []}
    core = _const_zone(text, "core")
    arts = _const_zone(text, "articles")
    if core is None:
        res["errors"].append("CORE zone markers missing or malformed")
    if arts is None:
        res["errors"].append("ARTICLES zone markers missing or malformed")
    if res["errors"]:
        res["ok"] = False
        return res
    for m in _CORE_ID_RE.finditer(core):
        res["core"].append({"id": m.group(1), "title": m.group(2).strip()})
    counts = {}
    for m in _ART_ID_RE.finditer(arts):
        rid = m.group(1)
        rest = arts[m.end():]
        nxt = re.search(r"^###\s", rest, re.MULTILINE)
        seg = rest[:nxt.start()] if nxt else rest
        prov = _parse_prov(seg)
        res["articles"].append({"id": rid, "title": m.group(2).strip(), "prov": prov,
                                "repealed": bool(prov.get("repealed"))})
        counts[rid] = counts.get(rid, 0) + 1
    dups = sorted(r for r, c in counts.items() if c > 1)
    if dups:
        res["ok"] = False
        res["errors"].append("duplicate rule id(s): " + ", ".join(dups))
    for a in res["articles"]:
        pid = a["prov"].get("id")
        if pid and pid != a["id"]:
            res["ok"] = False
            res["errors"].append(
                f"{a['id']}: provenance id={pid} mismatches heading")
    res["live"] = [a for a in res["articles"] if not a["repealed"]]
    return res


def cmd_constitution(args):
    action = getattr(args, "action", None) or "show"
    path = constitution_path()
    if action == "init":
        if os.path.exists(path):
            print(f"refusing to overwrite existing {path}", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(_starter_constitution(now_iso()[:10]))
        print(f"wrote starter constitution -> {path}")
        print("Next: move the coordination conventions out of CLAUDE.md into the "
              "Articles so the law has a single source of truth, then run "
              "`constitution check`.")
        return 0
    if not os.path.exists(path):
        print(f"no constitution yet at {path} — a human runs `constitution init`.")
        return 0
    res = parse_constitution(open(path).read())
    if action == "check":
        if res["ok"]:
            print(f"constitution OK: {len(res['core'])} core item(s), "
                  f"{len(res['articles'])} article(s)")
            return 0
        for e in res["errors"]:
            print(f"constitution ERROR: {e}", file=sys.stderr)
        return 1
    # show (default)
    for e in res["errors"]:
        print(f"! {e}")
    print("CORE (entrenched — human-only):")
    for c in res["core"]:
        print(f"  {c['id']} — {c['title']}")
    print("ARTICLES (parliament-amendable, human-ratified):")
    for a in res["articles"]:
        meta = " ".join(f"{k}={v}" for k, v in a["prov"].items() if v)
        print(f"  {a['id']} — {a['title']}" + (f"   [{meta}]" if meta else ""))
    return 0


def _env_float(name: str, default: float) -> float:
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _rule_cite_sender_sets(conn, days: int = 0) -> dict:
    """``{rule_id: set(senders)}`` over the window (days=0 → all-time)."""
    if days and days > 0:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        rows = conn.execute(
            "SELECT rule_id, sender FROM rule_cites WHERE ts >= ?", (cutoff,)).fetchall()
    else:
        rows = conn.execute("SELECT rule_id, sender FROM rule_cites").fetchall()
    by = {}
    for r in rows:
        by.setdefault(r["rule_id"], set()).add(r["sender"])
    return by


def cmd_review(args):
    """Repeal-first, ADVISORY review: rank live Articles by distinct-sender cites,
    flag dead/rarely-cited rules for repeal, and surface cites for unknown/repealed
    ids. Changes nothing. (Drift-grep + diary-promotion are deferred to P2.5.)"""
    path = constitution_path()
    if not os.path.exists(path):
        print(f"no constitution yet at {path} — nothing to review.")
        return 0
    res = parse_constitution(open(path).read())
    if not res["ok"]:
        for e in res["errors"]:
            print(f"constitution ERROR: {e}", file=sys.stderr)
        return 1
    conn = connect()
    days = int(getattr(args, "days", 0) or 0)
    sets = _rule_cite_sender_sets(conn, days)
    live = {a["id"]: a for a in res["live"]}
    low = _env_int("GROUPCHAT_REVIEW_LOW", 1)
    window = f"last {days}d" if days else "all-time"
    print(f"Constitution review (window: {window}) — {len(live)} article(s), advisory")

    repeal, watch, kept = [], [], []
    for rid, a in live.items():
        senders = set(sets.get(rid, set()))
        senders.discard(a["prov"].get("by"))   # discount self-cites by the author
        n = len(senders)
        row = (rid, a["title"], n)
        (repeal if n == 0 else watch if n <= low else kept).append(row)

    print("\nRepeal candidates (never cited — dead letters):")
    for rid, t, n in repeal:
        print(f"  {rid} — {t}   ({n} cites)")
    if not repeal:
        print("  (none)")
    if watch:
        print(f"\nRarely cited (<= {low} distinct agent — watch):")
        for rid, t, n in watch:
            print(f"  {rid} — {t}   ({n} cites)")
    print("\nActive (kept):")
    for rid, t, n in kept:
        print(f"  {rid} — {t}   ({n} cites)")
    if not kept:
        print("  (none)")

    unknown = sorted(set(sets) - set(live))
    if unknown:
        print("\nUnknown / repealed cite ids (RULE_RE noise or dead rules):")
        for rid in unknown:
            print(f"  {rid}   ({len(sets[rid])} cites)")

    print("\nDrift-flag + diary-promotion checks: deferred to P2.5.")
    return 0


# --------------------------------------------------------------------------- #
# Constitution — Phase 3: the advisory parliament (motion / vote / amendments / ratify)
# --------------------------------------------------------------------------- #
def _parse_motion_id(token: str):
    t = (token or "").strip().lstrip("Mm")
    return int(t) if t.isdigit() else None


def _next_rule_id(conn, parsed) -> str:
    """Allocate a monotonic, never-reused R-id (high-water mark in meta)."""
    existing = [int(a["id"][1:]) for a in parsed["articles"] if a["id"][1:].isdigit()]
    floor = (max(existing) + 1) if existing else 1
    hw = int(get_meta(conn, "const_next_rule_id") or 0)
    nxt = max(floor, hw)
    set_meta(conn, "const_next_rule_id", str(nxt + 1))
    return f"R{nxt}"


def _article_block(text: str, rule_id: str):
    """Return ``(start, end, block_text)`` for the ``### R<id>`` Article block, or None.
    The block spans its heading through just before the next ``### `` or the zone end."""
    ab = text.find(_ART_BEGIN)
    ae = text.find(_ART_END)
    if ab == -1 or ae == -1 or ae < ab:
        return None
    zone_start = ab + len(_ART_BEGIN)
    m = re.search(rf"^###\s+{re.escape(rule_id)}\b.*$", text[zone_start:ae], re.MULTILINE)
    if not m:
        return None
    hstart = zone_start + m.start()
    nxt = re.search(r"^###\s", text[hstart + 1:ae], re.MULTILINE)
    hend = (hstart + 1 + nxt.start()) if nxt else ae
    return (hstart, hend, text[hstart:hend])


def _format_provenance(prov: dict) -> str:
    base = ["id", "added", "by", "ratified", "amended", "source"]
    parts = [f"{k}={prov.get(k, '')}" for k in base]
    if prov.get("repealed"):
        parts.append(f"repealed={prov['repealed']}")
    parts += [f"{k}={prov[k]}" for k in prov if k not in base and k != "repealed"]
    return "<!-- meta: " + " ".join(parts) + " -->"


def _apply_amendment(text: str, m, today: str) -> str:
    """Return the new file text for a ratified motion. Pure (no write)."""
    if m["op"] == "repeal":
        blk = _article_block(text, m["target"])
        if not blk:
            return text
        s, e, block = blk
        # Tombstone, don't delete: the id stays in the committed file so it is never
        # reused (durable no-reuse, independent of the runtime db) and prior cites
        # resolve to a known-repealed rule.
        prov = _parse_prov(block)
        prov.update(id=m["target"], by="parliament", repealed=today, source=f"M{m['id']}")
        tomb = f"### {m['target']} — (repealed {today})\n{_format_provenance(prov)}\n"
        return text[:s] + tomb + text[e:]
    if m["op"] == "amend":
        blk = _article_block(text, m["target"])
        if not blk:
            return text
        s, e, block = blk
        hm = re.match(r"(###[^\n]*)\n", block)
        heading = hm.group(1) if hm else f"### {m['target']}"
        prov = _parse_prov(block)
        prov.update(id=m["target"], by="parliament", amended=today,
                    ratified=today, source=f"M{m['id']}")
        new_block = f"{heading}\n{(m['change'] or '').strip()}\n{_format_provenance(prov)}\n"
        return text[:s] + new_block + text[e:]
    if m["op"] == "add":
        span = _zone_span(text, "articles")
        if not span:
            return text
        prov = {"id": m["new_id"], "added": today, "by": m["proposer"],
                "ratified": today, "amended": "", "source": f"M{m['id']}"}
        block = (f"### {m['new_id']} — (new rule)\n{(m['change'] or '').strip()}\n"
                 f"{_format_provenance(prov)}\n\n")
        i = span[1]  # offset of the ARTICLES:END marker line
        return text[:i] + block + text[i:]
    return text


def _unified_diff(old: str, new: str, path: str) -> str:
    import difflib
    rel = os.path.basename(path)
    return "".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}"))


def motion_tally(conn, motion_id: int) -> dict:
    """Advisory tally: distinct registered voters, last vote per session wins."""
    rows = conn.execute(
        "SELECT voter_session, voter_handle, vote FROM votes WHERE motion_id=? ORDER BY id",
        (motion_id,)).fetchall()
    last = {}
    for r in rows:
        last[r["voter_session"]] = (r["voter_handle"], r["vote"])
    yea = sum(1 for _h, v in last.values() if v == "yea")
    nay = sum(1 for _h, v in last.values() if v == "nay")
    return {"yea": yea, "nay": nay, "voters": len(last),
            "detail": [(s, h, v) for s, (h, v) in last.items()]}


def _motion_summary(op, target, new_id, change, because, proposer) -> str:
    head = {"amend": f"Motion: amend {target}",
            "repeal": f"Motion: repeal {target}",
            "add": f"Motion: add {new_id}"}[op]
    out = f"{head} — proposed by {proposer}. because: {because}"
    if change and op != "repeal":
        out += f"  | new text: {change}"
    return out


def cmd_motion(args):
    conn = connect()
    a = _resolve_for_cli(conn, args)
    proposer = a["handle"] if a else (args.from_handle or "anon")
    because = (args.because or "").strip()
    if not because:
        print("a motion needs evidence: pass --because '<message ids / tests / diary>'",
              file=sys.stderr)
        return 1
    path = constitution_path()
    if not os.path.exists(path):
        print(f"no constitution at {path} — run `constitution init` first", file=sys.stderr)
        return 1
    text = open(path).read()
    parsed = parse_constitution(text)
    if not parsed["ok"]:
        print("constitution is malformed; fix it before legislating", file=sys.stderr)
        return 1
    if args.repeal:
        target, op, change = args.repeal, "repeal", None
    elif args.rule == "new":
        target, op, change = "new", "add", args.change
    else:
        target, op, change = args.rule, "amend", args.change
    if target and re.fullmatch(r"C\d+", target):
        print(f"{target} is entrenched Core — not amendable by motion (C1/C4)",
              file=sys.stderr)
        return 1
    if change is not None:
        for ln in change.splitlines():
            if (re.match(r"^\s*###\s", ln) or "CONSTITUTION:CORE:" in ln
                    or "CONSTITUTION:ARTICLES:" in ln or "<!-- meta:" in ln):
                print("--change may not contain a '### ' heading, a zone marker, or a "
                      "'<!-- meta:' comment (it would corrupt the law)", file=sys.stderr)
                return 1
    live = {x["id"] for x in parsed["live"]}
    base_text, new_id = None, None
    if op in ("amend", "repeal"):
        if target not in live:
            print(f"{target} is not a live Article", file=sys.stderr)
            return 1
        if op == "amend" and not (change or "").strip():
            print("amend needs --change '<new rule text>'", file=sys.stderr)
            return 1
        blk = _article_block(text, target)
        base_text = blk[2] if blk else None
    else:  # add
        if not (change or "").strip():
            print("add needs --change '<rule text>'", file=sys.stderr)
            return 1
        new_id = _next_rule_id(conn, parsed)
    tgt_key = new_id if op == "add" else target
    summary = _motion_summary(op, target, new_id, change, because, proposer)
    mid = send(conn, proposer, summary,
               session_id=(a["session_id"] if a else None), kind="motion")
    conn.execute("UPDATE motions SET status='superseded' WHERE target=? AND status='open'",
                 (tgt_key,))
    conn.execute(
        "INSERT INTO motions(id, ts, proposer, target, op, change, because, "
        "base_text, new_id, status) VALUES (?,?,?,?,?,?,?,?,?, 'open')",
        (mid, now_iso(), proposer, tgt_key, op, change, because, base_text, new_id))
    conn.commit()
    print(f"motion M{mid} opened: {op} {tgt_key} (advisory). Teammates vote with "
          f"`vote --session <sid> M{mid} yea|nay`; a human ratifies.")
    return 0


def cmd_vote(args):
    conn = connect()
    sid = getattr(args, "session", None)
    a = agent_by_session(conn, sid) if sid else None
    if not a:
        # A bare --from is unauthenticated (anyone can spoof a handle), so votes
        # require a registered session. Agents only know their handle, not their
        # session id — so point them at the ready-to-run line in their group-chat
        # briefing (which embeds the real session id and works on ANY host), and
        # give the Claude Code shortcut as a convenience. Host-neutral: the
        # briefing path doesn't depend on a Claude-only env var.
        print(
            "vote requires a registered session (a bare --from handle is "
            "unauthenticated and is not counted).\n"
            "Use the ready-to-run vote line from your group-chat briefing (it "
            "embeds your session id and works on any host), or pass --session "
            "<your-session-id>.\n"
            "In Claude Code your session id is in $CLAUDE_CODE_SESSION_ID:\n"
            f'    python3 "{os.path.abspath(__file__)}" vote '
            f'--session "$CLAUDE_CODE_SESSION_ID" {args.motion} {args.vote}',
            file=sys.stderr)
        return 1
    mid = _parse_motion_id(args.motion)
    if mid is None:
        print(f"bad motion id {args.motion!r} (expected M<number>)", file=sys.stderr)
        return 1
    m = conn.execute("SELECT * FROM motions WHERE id=?", (mid,)).fetchone()
    if not m:
        print(f"no motion M{mid}", file=sys.stderr)
        return 1
    if m["status"] != "open":
        print(f"M{mid} is {m['status']}, not open — vote not counted", file=sys.stderr)
        return 1
    send(conn, a["handle"], f"M{mid} {args.vote}",
         session_id=a["session_id"], kind="vote")
    conn.execute("INSERT INTO votes(ts, motion_id, voter_session, voter_handle, vote) "
                 "VALUES (?,?,?,?,?)",
                 (now_iso(), mid, a["session_id"], a["handle"], args.vote))
    conn.commit()
    print(f"recorded {args.vote} on M{mid} as {a['handle']} (advisory)")
    return 0


def cmd_amendments(args):
    conn = connect()
    show_all = getattr(args, "all", False)
    rows = conn.execute("SELECT * FROM motions ORDER BY id DESC").fetchall()
    rows = [m for m in rows if show_all or m["status"] == "open"]
    if not rows:
        print("no motions yet." if show_all else "no open motions.")
        return 0
    superq = _env_float("GROUPCHAT_AMEND_SUPERMAJORITY", 0.66)
    quorum = _env_int("GROUPCHAT_AMEND_QUORUM", 3)
    print("Motions — ADVISORY tally; the vote never gates, a human ratifies from "
          "evidence (see `ratify`):")
    for m in rows:
        t = motion_tally(conn, m["id"])
        cast = t["yea"] + t["nay"]
        frac = (t["yea"] / cast) if cast else 0.0
        flag = ("worth a human's ratify look"
                if (t["voters"] >= quorum and frac >= superq) else "below the advisory bar")
        print(f"  M{m['id']} [{m['status']}] {m['op']} {m['target']} — by {m['proposer']}")
        print(f"      yea {t['yea']} / nay {t['nay']}  ({t['voters']} registered voters) — {flag}")
        print(f"      because: {(m['because'] or '')[:100]}")
    return 0


def cmd_ratify(args):
    """[human] Default: a READ-ONLY, repeatable evidence dossier + proposed diff
    (no status change, no announcement). With ``--confirm`` (run AFTER you commit
    the diff): mark the motion ratified and notify the team. Never writes the file
    itself — diff-only, C1. The vote is advisory; the human ratifies from evidence."""
    conn = connect()
    mid = _parse_motion_id(args.motion)
    if mid is None:
        print(f"bad motion id {args.motion!r}", file=sys.stderr)
        return 1
    m = conn.execute("SELECT * FROM motions WHERE id=?", (mid,)).fetchone()
    if not m:
        print(f"no motion M{mid}", file=sys.stderr)
        return 1
    if m["status"] in ("ratified", "superseded", "withdrawn"):
        print(f"M{mid} is {m['status']} — nothing to ratify", file=sys.stderr)
        return 1
    if re.fullmatch(r"C\d+", m["target"] or ""):
        print(f"{m['target']} is entrenched Core — cannot be ratified", file=sys.stderr)
        return 1
    path = constitution_path()
    if not os.path.exists(path):
        print(f"no constitution at {path}", file=sys.stderr)
        return 1
    text = open(path).read()
    parsed = parse_constitution(text)
    if not parsed["ok"]:
        print("constitution is malformed; fix it before ratifying", file=sys.stderr)
        return 1
    if m["op"] in ("amend", "repeal"):
        blk = _article_block(text, m["target"])
        if not blk:
            print(f"{m['target']} no longer exists — re-motion", file=sys.stderr)
            return 1
        if (m["base_text"] or "").strip() != blk[2].strip():
            print(f"{m['target']} changed since M{mid} opened (base-text mismatch). "
                  "Re-motion against the current text.", file=sys.stderr)
            return 1
    if m["op"] == "add" and m["new_id"] in {a["id"] for a in parsed["articles"]}:
        print(f"{m['new_id']} already exists — re-motion (id now taken)", file=sys.stderr)
        return 1
    new_text = _apply_amendment(text, m, now_iso()[:10])
    if new_text == text:
        print(f"M{mid} would make no change to the law — refusing (re-motion).",
              file=sys.stderr)
        return 1

    if getattr(args, "confirm", False):
        conn.execute("UPDATE motions SET status='ratified' WHERE id=?", (mid,))
        send(conn, "system",
             f"Constitution: M{mid} ratified ({m['op']} {m['target']}) — re-read the law.",
             kind="system")
        conn.commit()
        print(f"M{mid} marked ratified and the team notified. "
              "(Run this only after committing the diff.)")
        return 0

    t = motion_tally(conn, mid)
    cc = len(_rule_cite_sender_sets(conn, 0).get(m["target"], set()))
    voters = ", ".join(f"{h}:{v}" for _s, h, v in t["detail"]) or "(none)"
    print(f"=== Ratify dossier — M{mid}: {m['op']} {m['target']} ===")
    print(f"proposer (self-asserted handle — a lead, not proof): {m['proposer']}")
    print(f"evidence (--because): {m['because']}")
    print(f"advisory votes (registered sessions): yea {t['yea']} / nay {t['nay']}  [{voters}]")
    print(f"behavioral signal: {m['target']} cited by {cc} distinct agent(s)")
    print("Votes are ADVISORY — read the evidence above, then commit the diff yourself.")
    print("\n--- proposed diff (apply by hand, then `git commit`) ---")
    diff = _unified_diff(text, new_text, path)
    print(diff if diff.strip() else "(no textual change)")
    print(f"\nThis view is READ-ONLY and repeatable. After committing the diff, run "
          f"`ratify --confirm M{mid}` to mark it ratified and notify the team.")
    return 0


def cmd_doctor(args):
    """Run the health & staleness checker (.groupchat/doctor.py) as a first-class
    subcommand, so it's discoverable alongside the rest of the CLI rather than a
    hidden script. Loads doctor.py as a module and delegates to its main()."""
    import importlib.util
    dpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doctor.py")
    if not os.path.isfile(dpath):
        print("doctor.py not found alongside chat.py", file=sys.stderr)
        return 1
    spec = importlib.util.spec_from_file_location("_gc_doctor_cli", dpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(["-q"] if getattr(args, "quiet", False) else [])


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

    sp = sub.add_parser("lead",
                        help="show / claim / hand off / release the lead (@human routing)")
    add_identity(sp)
    sp.add_argument("handle", nargs="?",
                    help="handle to designate / hand off to (omit to show the current lead)")
    sp.add_argument("--claim", action="store_true",
                    help="claim the lead for yourself (needs --from / --session)")
    sp.add_argument("--release", action="store_true",
                    help="step down → the deterministic floor takes over")
    sp.set_defaults(func=cmd_lead)

    sp = sub.add_parser("questions", aliases=["escalations"],
                        help="[operator] the lead's open @human escalations awaiting your answer")
    sp.set_defaults(func=cmd_questions)

    sp = sub.add_parser("answer",
                        help="[operator] answer a lead's @human escalation by its message id")
    sp.add_argument("msg_id", type=int, help="the escalation's message id (see `questions`)")
    sp.add_argument("message", nargs="+", help="your answer to the team")
    sp.set_defaults(func=cmd_answer)

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

    sp = sub.add_parser("doctor", help="health & staleness check (code/schema/hooks/wiring)")
    sp.add_argument("-q", "--quiet", action="store_true",
                    help="only warnings/failures + summary")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("constitution", aliases=["const"],
                        help="show/init/check the coordination constitution")
    sp.add_argument("action", nargs="?", choices=["show", "init", "check"],
                    default="show", help="default: show")
    sp.set_defaults(func=cmd_constitution)

    sp = sub.add_parser("review", help="repeal-first constitution review (advisory)")
    sp.add_argument("--days", type=int, default=0, help="cite window in days (0 = all-time)")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("motion", help="propose a constitution amendment (advisory; evidence required)")
    add_identity(sp)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--rule", help="rule id to amend (R<n>), or 'new' to add an Article")
    g.add_argument("--repeal", help="rule id to repeal (R<n>)")
    sp.add_argument("--change", help="proposed new rule text (required for amend/add)")
    sp.add_argument("--because", required=True, help="evidence: message ids / tests / diary refs")
    sp.set_defaults(func=cmd_motion)

    sp = sub.add_parser("vote", help="cast an advisory vote on a motion (registered --session only)")
    add_identity(sp)
    sp.add_argument("motion", help="motion id, e.g. M12")
    sp.add_argument("vote", choices=["yea", "nay"])
    sp.set_defaults(func=cmd_vote)

    sp = sub.add_parser("amendments", help="list motions and their advisory tallies")
    sp.add_argument("--all", action="store_true", help="include closed/superseded/ratified motions")
    sp.set_defaults(func=cmd_amendments)

    sp = sub.add_parser("ratify", help="[human] show a motion's evidence + proposed diff (read-only); --confirm to enact")
    sp.add_argument("motion", help="motion id, e.g. M12")
    sp.add_argument("--confirm", action="store_true",
                    help="after committing the diff: mark ratified + notify the team")
    sp.set_defaults(func=cmd_ratify)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rc = args.func(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
