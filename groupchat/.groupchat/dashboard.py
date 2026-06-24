#!/usr/bin/env python3
"""Live room dashboard — a single pane of glass for a human supervising the fleet.

A *read-only* renderer over the group-chat bus. It turns the current state of
``chat.db`` into a self-contained HTML file (``.groupchat/room.html`` by default)
that a human can open in a browser to watch the whole room at a glance:

  * the **roster** — who's active, parked (done but waiting at the barrier), or
    gone, with each agent's output-token burn;
  * the **tokens** — the full ``chat.py tokens`` view: in / out / cache-read /
    cache-create per agent plus totals (approximate, transcript-derived);
  * the **conversation** — the recent message feed, with @mentions surfaced and
    system/parliament traffic styled apart;
  * the **parliament** — open motions with their *advisory* tallies;
  * the **team barrier** — how many agents are done vs. active.

Design notes — it deliberately mirrors ``chat.py``'s constraints:

  * **No server, no daemon, no third-party deps.** Just stdlib; the output is one
    self-contained ``.html`` with inlined CSS (open it over ``file://``).
  * **Strictly read-only on the bus.** It opens the db ``mode=ro`` and never
    writes ``chat.db`` — a dashboard can't corrupt the bus, block a Stop, or
    race the single-threaded writer (friendly to C2 *hooks-fail-open* and C3
    *writes-single-threaded*).
  * **Fails soft.** A missing/locked db yields an "empty room" page, never a
    stack trace in a human's face.

Usage::

    python3 .groupchat/dashboard.py                 # write .groupchat/room.html once
    python3 .groupchat/dashboard.py --open          # ...and open it in a browser
    python3 .groupchat/dashboard.py --watch 5       # regenerate every 5s (live view)
    python3 .groupchat/dashboard.py --out path.html # custom output path
    python3 .groupchat/dashboard.py --limit 60      # show the last 60 messages
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

# Import the transport module read-only. dashboard.py lives next to chat.py in
# .groupchat/, so the script dir is on sys.path when run directly; the test suite
# adds .groupchat/ explicitly. We reuse chat.py's resolution + query helpers so
# the dashboard always sees the same room the hooks and CLI do.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat  # noqa: E402

DEFAULT_MSG_LIMIT = 50
DEFAULT_REFRESH = 5  # seconds, used by --watch / live mode

# Status dot palette (kept in sync with the flat-vs-hierarchy / playground look).
_DOT = {"green": "#22c55e", "blue": "#3b82f6", "gray": "#a3a3a3", "amber": "#eab308"}


# --------------------------------------------------------------------------- #
# Small formatters
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_display() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_tokens(n) -> str:
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


def _fmt_age(secs: float) -> str:
    if secs == float("inf"):
        return "—"
    s = int(secs)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _status_of(status: str | None, active: bool) -> tuple[str, str]:
    """Map (status, active) -> (human label, dot colour). 'Parked' = a done agent
    still inside the active window, i.e. dormant at the team barrier."""
    done = (status or "") == chat.DONE_STATUS
    if done and active:
        return "done · parked", "blue"
    if done:
        return "done", "gray"
    if active:
        return "active", "green"
    return "idle", "gray"


# --------------------------------------------------------------------------- #
# Snapshot — read the bus into a plain dict (all the logic lives here so the
# renderer can stay a dumb, easily-tested pure function).
# --------------------------------------------------------------------------- #
def _amend_thresholds() -> tuple[float, int]:
    try:
        superq = float(os.environ.get("GROUPCHAT_AMEND_SUPERMAJORITY", "0.66"))
    except ValueError:
        superq = 0.66
    try:
        quorum = int(os.environ.get("GROUPCHAT_AMEND_QUORUM", "3"))
    except ValueError:
        quorum = 3
    return superq, quorum


def _collect_agents(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM agents ORDER BY handle").fetchall()
    out = []
    for r in rows:
        active = chat._is_active(r["last_seen"])
        label, dot = _status_of(r["status"], active)
        cols = r.keys()
        entry = {
            "handle": r["handle"],
            "label": label,
            "dot": dot,
            "active": active,
            "cwd": os.path.basename((r["cwd"] or "").rstrip("/")) or (r["cwd"] or ""),
            "age": _fmt_age(chat.iso_age_seconds(r["last_seen"])),
        }
        # All four transcript counters (a pre-upgrade db may lack the columns).
        for col in ("in_tokens", "out_tokens", "cache_read_tokens", "cache_create_tokens"):
            n = int((r[col] if col in cols else 0) or 0)
            entry[col] = n
            entry[col.replace("_tokens", "_display")] = _fmt_tokens(n)
        out.append(entry)
    # Active (incl. parked) first, then by handle — the people in the room float up.
    out.sort(key=lambda a: (not a["active"], a["handle"]))
    return out


def _token_totals(agents: list) -> dict:
    """Sum the four transcript counters across collected agents — the dashboard's
    mirror of ``chat.py tokens``' total line. Pure (works on collected dicts, no
    second db read)."""
    tot = {}
    for k in ("in", "out", "cache_read", "cache_create"):
        n = sum(int(a.get(f"{k}_tokens") or 0) for a in (agents or []))
        tot[k] = n
        tot[f"{k}_display"] = _fmt_tokens(n)
    return tot


def _collect_messages(conn, limit: int) -> list[dict]:
    rows = chat.recent_messages(conn, limit)
    out = []
    for m in rows:
        try:
            mentions = json.loads(m["mentions"] or "[]")
        except Exception:
            mentions = []
        out.append({
            "id": m["id"],
            "time": chat._hhmm(m["ts"]),
            "sender": m["sender"],
            "mentions": mentions,
            "kind": m["kind"],
            "body": m["body"],
        })
    return out


def _collect_motions(conn) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT * FROM motions WHERE status='open' ORDER BY id DESC").fetchall()
    except sqlite3.Error:
        return []
    superq, quorum = _amend_thresholds()
    out = []
    for m in rows:
        t = chat.motion_tally(conn, m["id"])
        cast = t["yea"] + t["nay"]
        frac = (t["yea"] / cast) if cast else 0.0
        flag = ("worth a human's ratify look"
                if (t["voters"] >= quorum and frac >= superq) else "below the advisory bar")
        out.append({
            "id": m["id"], "op": m["op"], "target": m["target"],
            "proposer": m["proposer"], "status": m["status"],
            "yea": t["yea"], "nay": t["nay"], "voters": t["voters"],
            "because": m["because"] or "", "flag": flag,
        })
    return out


def _collect_barrier(conn) -> dict:
    # Each read is guarded independently: a bug or lock in one barrier function
    # must not blank the panel — show whatever counts we can still compute.
    try:
        active = chat.active_agents(conn)
    except Exception:
        active = []
    done = [a for a in active if (a["status"] or "") == chat.DONE_STATUS]
    try:
        expected = chat.expected_team_size(conn)
    except Exception:
        expected = None
    try:
        td = chat.team_done(conn)
    except Exception:
        td = False
    return {
        "active": len(active),
        "done": len(done),
        "team_done": td,
        "expected": expected,
        "label": f"{len(done)}/{len(active)} done",
    }


def _collect_lead(conn) -> dict:
    """Who currently owns human contact (the hub-and-spoke lead), and how they got
    there. Reads the same ``resolve_lead`` the send-guard uses, so the dashboard
    shows exactly the routing that's live. ``None`` handle ⇒ flat mode."""
    handle = chat.resolve_lead(conn)
    if not handle:
        return {"handle": None, "source": "flat"}
    pointer = (chat.get_meta(conn, "lead") or "").strip().lower()
    env = (os.environ.get("GROUPCHAT_LEAD") or "").strip().lower()
    if pointer and pointer == handle:
        source = "designated"
    elif env and env == handle:
        source = "operator override"
    else:
        source = "floor (auto-elected)"
    return {"handle": handle, "source": source}


def _collect_escalations(conn, lead_handle) -> list[dict]:
    """Open @human escalations the operator hasn't answered — the human-facing half
    of hub-and-spoke. Consumes the **room-wide, session-keyed** ``all_open_escalations``
    (Phase 5) so a question whose author renamed or handed off the lead is still shown —
    not just the current lead's (the old handle-keyed ``open_escalations`` re-orphaned on
    rename). No helper ⇒ empty list (degrades safe). ``lead_handle`` is unused now."""
    fn = getattr(chat, "all_open_escalations", None)
    if not fn:
        return []
    out = []
    for _sid, ids in (fn(conn) or {}).items():
        for mid in ids:
            try:
                row = conn.execute(
                    "SELECT id, ts, body FROM messages WHERE id=?", (int(mid),)).fetchone()
            except (TypeError, ValueError):
                continue
            if row:
                out.append({"id": row["id"], "time": chat._hhmm(row["ts"]),
                            "body": row["body"]})
    return sorted(out, key=lambda d: d["id"])


def _collect_constitution(conn) -> dict | None:
    try:
        path = chat.constitution_path()
        if not os.path.exists(path):
            return None
        parsed = chat.parse_constitution(open(path).read())
        if not parsed.get("ok"):
            return None
        return {"core": len(parsed.get("core", [])),
                "articles": len(parsed.get("articles", []))}
    except Exception:
        return None


def _safe(fn, default):
    """Run a section collector; on any failure degrade to ``default`` so a single
    buggy/locked read can never blank the whole dashboard."""
    try:
        return fn()
    except Exception:
        return default


def collect(conn, msg_limit: int = DEFAULT_MSG_LIMIT, live: bool = False,
            refresh: int = DEFAULT_REFRESH) -> dict:
    """Read the whole room into a snapshot dict. Pure reads — never mutates. Each
    section degrades independently (see ``_safe``)."""
    # Lead is resolved first because the escalation queue is scoped to it;
    # agents likewise, because the token totals are summed from them.
    lead = _safe(lambda: _collect_lead(conn), {"handle": None, "source": "flat"})
    agents = _safe(lambda: _collect_agents(conn), [])
    return {
        "title": "groupchat room",
        "generated_display": _now_display(),
        "room_dir": _safe(chat.store_dir, ""),
        "live": live,
        "refresh": refresh,
        "agents": agents,
        "token_totals": _safe(lambda: _token_totals(agents), {}),
        "messages": _safe(lambda: _collect_messages(conn, msg_limit), []),
        "motions": _safe(lambda: _collect_motions(conn), []),
        "barrier": _safe(lambda: _collect_barrier(conn),
                         {"active": 0, "done": 0, "team_done": False,
                          "expected": None, "label": "unavailable"}),
        "lead": lead,
        "escalations": _safe(lambda: _collect_escalations(conn, (lead or {}).get("handle")), []),
        "constitution": _safe(lambda: _collect_constitution(conn), None),
    }


def _empty_snapshot(reason: str, live: bool = False,
                    refresh: int = DEFAULT_REFRESH) -> dict:
    return {
        "title": "groupchat room", "generated_display": _now_display(),
        "room_dir": chat.store_dir(), "live": live, "refresh": refresh,
        "agents": [], "token_totals": {}, "messages": [], "motions": [],
        "barrier": {"active": 0, "done": 0, "team_done": False,
                    "expected": None, "label": "no agents"},
        "lead": {"handle": None, "source": "flat"}, "escalations": [],
        "constitution": None, "note": reason,
    }


# --------------------------------------------------------------------------- #
# Render — a pure function: snapshot dict -> self-contained HTML string.
# --------------------------------------------------------------------------- #
_CSS = """
:root{
  --bg:#fafafa;--surf:#ffffff;--b1:#f5f5f5;--b2:#e5e5e5;--b3:#d4d4d4;
  --text:#171717;--muted:#737373;--faint:#a3a3a3;
  --radius:16px;--radius-sm:10px;
  --font-ui:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  --font-mono:'SF Mono',Menlo,Monaco,ui-monospace,'Cascadia Mono',monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{font-family:var(--font-ui);background:var(--bg);color:var(--text);
  line-height:1.5;-webkit-font-smoothing:antialiased;padding:20px;}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;
  margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid var(--b2);}
header h1{font-size:18px;font-weight:650;letter-spacing:-.2px;}
header .meta{font-family:var(--font-mono);font-size:11px;color:var(--muted);}
.badge{font-family:var(--font-mono);font-size:10px;letter-spacing:.5px;
  text-transform:uppercase;padding:3px 8px;border-radius:20px;border:1px solid var(--b2);
  color:var(--muted);}
.badge.live{color:#15803d;border-color:#bbf7d0;background:#f0fdf4;}
.grid{display:grid;grid-template-columns:300px 1fr;gap:18px;align-items:start;}
@media(max-width:720px){.grid{grid-template-columns:1fr;}}
.card{background:var(--surf);border:1px solid var(--b2);border-radius:var(--radius);
  padding:16px;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.03);}
.card h2{font-size:11px;letter-spacing:1.2px;text-transform:uppercase;
  color:var(--muted);font-family:var(--font-mono);margin-bottom:12px;font-weight:600;}
.row{display:flex;align-items:center;gap:9px;padding:7px 0;border-top:1px solid var(--b1);}
.row:first-of-type{border-top:none;}
.dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto;}
.handle{font-weight:600;font-size:14px;}
.who-label{font-family:var(--font-mono);font-size:10px;color:var(--muted);}
.who-meta{margin-left:auto;font-family:var(--font-mono);font-size:10px;color:var(--faint);
  text-align:right;white-space:nowrap;}
.barline{font-family:var(--font-mono);font-size:13px;color:var(--text);}
.barsub{font-family:var(--font-mono);font-size:10px;color:var(--muted);margin-top:4px;}
.feed{display:flex;flex-direction:column;gap:2px;}
.msg{padding:9px 11px;border-radius:var(--radius-sm);border:1px solid transparent;}
.msg:hover{background:var(--b1);}
.msg .head{font-family:var(--font-mono);font-size:11px;color:var(--muted);margin-bottom:2px;}
.msg .id{color:var(--faint);}
.msg .sender{color:var(--text);font-weight:600;}
.msg .body{white-space:pre-wrap;word-break:break-word;font-size:14px;}
.msg.sys{background:var(--b1);border-color:var(--b2);}
.msg.sys .body{color:var(--muted);font-size:13px;font-family:var(--font-mono);}
.mention{color:#3b82f6;font-weight:600;}
.kind-tag{font-family:var(--font-mono);font-size:9px;text-transform:uppercase;
  letter-spacing:.5px;padding:1px 6px;border-radius:10px;background:var(--b2);
  color:var(--muted);margin-left:6px;}
.motion{padding:8px 0;border-top:1px solid var(--b1);font-size:13px;}
.motion:first-of-type{border-top:none;}
.motion .m-head{font-family:var(--font-mono);font-size:12px;}
.motion .m-tally{font-family:var(--font-mono);font-size:11px;color:var(--muted);margin-top:2px;}
.motion .m-because{font-size:12px;color:var(--muted);margin-top:3px;}
.advisory{font-size:11px;color:var(--faint);margin-top:10px;font-style:italic;}
.card.alert{border-color:#fcd34d;background:#fffbeb;}
.card.alert h2{color:#b45309;}
.escal{padding:8px 0;border-top:1px solid #fde68a;}
.escal:first-of-type{border-top:none;}
.escal .m-head{font-family:var(--font-mono);font-size:11px;color:#b45309;}
.escal .body{white-space:pre-wrap;word-break:break-word;font-size:13px;margin-top:2px;}
.empty{color:var(--faint);font-size:13px;font-style:italic;}
table.tok{width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:11px;}
table.tok th{text-align:right;color:var(--faint);font-weight:500;font-size:9px;
  letter-spacing:.5px;text-transform:uppercase;padding:2px 0 5px 8px;}
table.tok td{text-align:right;color:var(--muted);padding:4px 0 4px 8px;
  border-top:1px solid var(--b1);white-space:nowrap;}
table.tok th:first-child,table.tok td:first-child{text-align:left;padding-left:0;}
table.tok td.h{color:var(--text);font-weight:600;font-family:var(--font-ui);font-size:12px;}
table.tok tr.total td{border-top:1px solid var(--b2);color:var(--text);font-weight:600;}
footer{margin-top:18px;font-family:var(--font-mono);font-size:10px;color:var(--faint);}
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _render_body(body: str, mentions: list) -> str:
    """Escape a message body and re-link @mentions as styled chips."""
    safe = _esc(body)
    for m in sorted(set(mentions or []), key=len, reverse=True):
        token = "@" + _esc(m)
        safe = safe.replace(token, f'<span class="mention">{token}</span>')
    return safe


def _render_agents(agents: list) -> str:
    if not agents:
        return '<div class="empty">no agents have joined yet</div>'
    out = []
    for a in agents:
        colour = _DOT.get(a.get("dot", "gray"), _DOT["gray"])
        out.append(
            f'<div class="row">'
            f'<span class="dot" style="background:{colour}"></span>'
            f'<span class="handle">{_esc(a["handle"])}</span>'
            f'<span class="who-label">{_esc(a["label"])}</span>'
            f'<span class="who-meta">{_esc(a.get("out_display", ""))} out · '
            f'{_esc(a.get("age", ""))}</span>'
            f'</div>'
        )
    return "".join(out)


def _render_tokens(agents: list, totals: dict) -> str:
    """The full ``chat.py tokens`` view: the four transcript counters per agent
    plus a totals row."""
    if not agents:
        return '<div class="empty">no token data yet</div>'
    totals = totals or {}
    cells = ("in", "out", "cache_read", "cache_create")
    head = ('<tr><th>agent</th><th>in</th><th>out</th>'
            '<th>cache-read</th><th>cache-create</th></tr>')
    rows = []
    for a in agents:
        tds = "".join(f'<td>{_esc(a.get(f"{k}_display", "0"))}</td>' for k in cells)
        rows.append(f'<tr><td class="h">{_esc(a["handle"])}</td>{tds}</tr>')
    tds = "".join(f'<td>{_esc(totals.get(f"{k}_display", "0"))}</td>' for k in cells)
    rows.append(f'<tr class="total"><td>total</td>{tds}</tr>')
    return (f'<table class="tok">{head}{"".join(rows)}</table>'
            '<div class="advisory">approximate — summed from each agent\'s local '
            'transcript; good for relative burn, not billing.</div>')


def _render_messages(messages: list) -> str:
    if not messages:
        return '<div class="empty">no messages yet</div>'
    out = []
    for m in messages:
        is_sys = m.get("kind", "chat") != "chat"
        cls = "msg sys" if is_sys else "msg"
        arrow = ""
        if m.get("mentions"):
            arrow = " → " + " ".join(
                f'<span class="mention">@{_esc(x)}</span>' for x in m["mentions"])
        tag = ""
        if is_sys:
            tag = f'<span class="kind-tag">{_esc(m["kind"])}</span>'
        out.append(
            f'<div class="{cls}">'
            f'<div class="head"><span class="id">#{_esc(m["id"])}</span> '
            f'{_esc(m["time"])} <span class="sender">{_esc(m["sender"])}</span>'
            f'{arrow}{tag}</div>'
            f'<div class="body">{_render_body(m["body"], m.get("mentions"))}</div>'
            f'</div>'
        )
    return "".join(out)


def _render_motions(motions: list) -> str:
    if not motions:
        return '<div class="empty">no open motions</div>'
    out = []
    for m in motions:
        out.append(
            f'<div class="motion">'
            f'<div class="m-head">M{_esc(m["id"])} · {_esc(m["op"])} '
            f'{_esc(m["target"])} <span class="who-label">by {_esc(m["proposer"])}</span></div>'
            f'<div class="m-tally">yea {_esc(m["yea"])} / nay {_esc(m["nay"])} '
            f'· {_esc(m["voters"])} voters · {_esc(m["flag"])}</div>'
            f'<div class="m-because">{_esc(m["because"][:140])}</div>'
            f'</div>'
        )
    out.append('<div class="advisory">Votes are advisory — a human ratifies from '
               'the evidence; the tally is one weak input.</div>')
    return "".join(out)


def _render_escalations(items: list) -> str:
    if not items:
        return '<div class="empty">none — you\'re all caught up</div>'
    out = []
    for e in items:
        out.append(
            f'<div class="escal">'
            f'<div class="m-head"><span class="id">#{_esc(e.get("id"))}</span> '
            f'{_esc(e.get("time", ""))}</div>'
            f'<div class="body">{_esc((e.get("body") or "")[:200])}</div>'
            f'</div>'
        )
    return "".join(out)


def _render_lead(lead: dict) -> str:
    lead = lead or {}
    h = lead.get("handle")
    if not h:
        return ('<div class="barline">flat — no lead</div>'
                '<div class="barsub">@human passes through; no single point of contact</div>')
    return (f'<div class="barline">@{_esc(h)} '
            f'<span class="who-label">{_esc(lead.get("source", ""))}</span></div>'
            f'<div class="barsub">single point of human contact · '
            f'a worker\'s @human routes to @{_esc(h)}</div>')


def _render_barrier(b: dict) -> str:
    expected = b.get("expected")
    exp = f" · team size {expected}" if expected else ""
    state = "team done — all parked agents may exit" if b.get("team_done") \
        else "waiting — finished agents are parked, ready for an @mention"
    return (f'<div class="barline">{_esc(b.get("label", ""))}{exp}</div>'
            f'<div class="barsub">{_esc(state)}</div>')


def render_html(snapshot: dict) -> str:
    """Pure: snapshot dict -> a self-contained HTML page (no external assets)."""
    s = snapshot
    live = bool(s.get("live"))
    refresh = int(s.get("refresh", DEFAULT_REFRESH) or DEFAULT_REFRESH)
    refresh_meta = (f'<meta http-equiv="refresh" content="{refresh}">' if live else "")
    live_badge = (f'<span class="badge live">live · {refresh}s</span>' if live
                  else '<span class="badge">snapshot</span>')

    escalations = s.get("escalations", []) or []
    escal_alert = " alert" if escalations else ""
    escal_count = f' ({len(escalations)})' if escalations else ""

    const = s.get("constitution")
    const_line = (f'<span class="meta">constitution: {const["core"]} core · '
                  f'{const["articles"]} articles</span>' if const else "")
    note = (f'<div class="empty">{_esc(s["note"])}</div>' if s.get("note") else "")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_meta}
<title>{_esc(s.get("title", "groupchat room"))}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>{_esc(s.get("title", "groupchat room"))}</h1>
  {live_badge}
  <span class="meta">generated {_esc(s.get("generated_display", ""))}</span>
  {const_line}
</header>
{note}
<div class="grid">
  <div class="col-left">
    <div class="card{escal_alert}">
      <h2>Open escalations{escal_count} · awaiting you</h2>
      {_render_escalations(s.get("escalations", []))}
    </div>
    <div class="card">
      <h2>Lead · human contact</h2>
      {_render_lead(s.get("lead"))}
    </div>
    <div class="card">
      <h2>Roster</h2>
      {_render_agents(s.get("agents", []))}
    </div>
    <div class="card">
      <h2>Tokens</h2>
      {_render_tokens(s.get("agents", []), s.get("token_totals", {}))}
    </div>
    <div class="card">
      <h2>Team barrier</h2>
      {_render_barrier(s.get("barrier", {}))}
    </div>
    <div class="card">
      <h2>Parliament</h2>
      {_render_motions(s.get("motions", []))}
    </div>
  </div>
  <div class="col-right">
    <div class="card">
      <h2>Conversation</h2>
      <div class="feed">
        {_render_messages(s.get("messages", []))}
      </div>
    </div>
  </div>
</div>
<footer>read-only view of {_esc(s.get("room_dir", ""))} · groupchat dashboard</footer>
</body>
</html>"""


_TEXT_DOT = {"green": "●", "blue": "◍", "gray": "○", "amber": "●"}


def render_text(snapshot: dict) -> str:
    """Pure: snapshot dict -> a compact plain-text room summary. The same state the
    HTML shows, in one terminal-friendly block — so an agent can read roster, lead,
    barrier, open escalations, and motions in a single call instead of four."""
    s = snapshot
    out = []
    const = s.get("constitution")
    cwords = f" · {const['core']} core / {const['articles']} articles" if const else ""
    out.append(f"groupchat room · {s.get('generated_display', '')}{cwords}")
    if s.get("note"):
        out.append(f"  ({s['note']})")

    lead = s.get("lead") or {}
    if lead.get("handle"):
        out.append(f"lead: @{lead['handle']} ({lead.get('source', '')}) "
                   f"— a worker's @human routes here")
    else:
        out.append("lead: — flat mode (no single point of contact)")

    b = s.get("barrier") or {}
    out.append(f"barrier: {b.get('label', '')}"
               + (" — team done" if b.get("team_done") else " — waiting"))

    esc = s.get("escalations") or []
    if esc:
        out.append(f"open escalations ({len(esc)}) AWAITING YOU:")
        for e in esc:
            out.append(f"  #{e.get('id')}  {e.get('time', '')}  "
                       f"{(e.get('body') or '')[:80]}")
    else:
        out.append("open escalations: none")

    agents = s.get("agents") or []
    out.append("roster:")
    if agents:
        for a in agents:
            dot = _TEXT_DOT.get(a.get("dot"), "·")
            out.append(f"  {dot} {a.get('handle', ''):<10} {a.get('label', ''):<14} "
                       f"{a.get('out_display', '')} out · {a.get('age', '')}")
    else:
        out.append("  (no agents)")

    tot = s.get("token_totals") or {}
    if any(tot.get(k) for k in ("in", "out", "cache_read", "cache_create")):
        out.append(f"tokens (total): in {tot.get('in_display', '0')} · "
                   f"out {tot.get('out_display', '0')} · "
                   f"cache-read {tot.get('cache_read_display', '0')} · "
                   f"cache-create {tot.get('cache_create_display', '0')} (approx)")

    motions = s.get("motions") or []
    if motions:
        out.append("open motions (advisory — a human ratifies):")
        for m in motions:
            out.append(f"  M{m.get('id')} {m.get('op')} {m.get('target')} "
                       f"by {m.get('proposer')} — yea {m.get('yea')}/nay {m.get('nay')} "
                       f"({m.get('voters')} voters) — {m.get('flag')}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Generate — connect read-only, collect, render, write.
# --------------------------------------------------------------------------- #
def _connect_ro() -> sqlite3.Connection | None:
    """Open the room db strictly read-only so the dashboard can never write the
    bus. Returns None (caller renders an empty room) if it can't be opened."""
    path = chat.db_path()
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def snapshot(msg_limit: int = DEFAULT_MSG_LIMIT, live: bool = False,
             refresh: int = DEFAULT_REFRESH) -> dict:
    """Connect read-only and collect a room snapshot; an empty-room snapshot if the
    db isn't there yet. The single read path shared by HTML and text rendering."""
    conn = _connect_ro()
    if conn is None:
        return _empty_snapshot("room not initialised yet", live=live, refresh=refresh)
    try:
        return collect(conn, msg_limit=msg_limit, live=live, refresh=refresh)
    finally:
        conn.close()


def generate(out_path: str | None = None, msg_limit: int = DEFAULT_MSG_LIMIT,
             live: bool = False, refresh: int = DEFAULT_REFRESH) -> str:
    """Render the current room to ``out_path`` (default ``<store>/room.html``).
    Returns the written path. Read-only on the bus; fails soft to an empty page."""
    if out_path is None:
        out_path = os.path.join(chat.store_dir(), "room.html")
    snap = snapshot(msg_limit=msg_limit, live=live, refresh=refresh)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(render_html(snap))
    os.replace(tmp, out_path)  # atomic — an open browser never sees a half-written file
    return out_path


def _open_in_browser(path: str) -> None:
    import subprocess
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the group-chat room to HTML.")
    ap.add_argument("--out", help="output path (default <store>/room.html)")
    ap.add_argument("--limit", type=int, default=DEFAULT_MSG_LIMIT,
                    help="number of recent messages to show")
    ap.add_argument("--watch", type=int, metavar="SECONDS", nargs="?", const=DEFAULT_REFRESH,
                    help="regenerate every N seconds (live view; default 5)")
    ap.add_argument("--open", action="store_true", help="open the file in a browser")
    ap.add_argument("--text", action="store_true",
                    help="print a compact text summary to stdout instead of writing HTML")
    args = ap.parse_args(argv)

    if args.text:
        print(render_text(snapshot(msg_limit=args.limit)))
        return 0

    live = args.watch is not None
    refresh = args.watch if live else DEFAULT_REFRESH
    path = generate(out_path=args.out, msg_limit=args.limit, live=live, refresh=refresh)
    print(f"wrote {path}")
    if args.open:
        _open_in_browser(path)
    if not live:
        return 0
    print(f"watching — regenerating every {refresh}s (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(refresh)
            generate(out_path=args.out, msg_limit=args.limit, live=True, refresh=refresh)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
