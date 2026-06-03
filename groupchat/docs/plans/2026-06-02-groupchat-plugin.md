# Group Chat Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Package the existing `.groupchat/` group-chat system as a distributable Claude Code plugin (hooks + usage skill + slash commands), and add per-session token tracking — without rewriting the working transport.

**Architecture:** The plugin is additive scaffolding around the existing `.groupchat/` tree. Code ships in the plugin (`${CLAUDE_PLUGIN_ROOT}`); the `chat.db` bus is created at runtime in the *target* repo (resolved by `store_dir()` from cwd → git common dir), gitignored. The existing `chat.py install <repo>` path keeps working (dual distribution). Token usage is metered from each session's transcript (`transcript_path` in the Stop hook payload) into four new `agents` columns and surfaced via `chat.py tokens` / `who`.

**Tech Stack:** Python 3 stdlib only (sqlite3, json, argparse). Claude Code plugin manifest + hooks JSON + skill/command markdown. No third-party deps. No test framework — verification is by exercising the CLI and piping hook JSON on stdin with `GROUPCHAT_DIR` set to a throwaway path (the repo convention; see CLAUDE.md "Testing the system").

**Conventions for every task below:**
- Always `export GROUPCHAT_DIR=/tmp/gc_plan` (a throwaway room) before running verification so the live room is never touched. `rm -rf` it between tasks that need a clean db.
- Hooks must **fail open** — never let a change make a hook raise or exit non-zero on an injection event.
- End each commit message with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Initialize git (prerequisite)

This repo is not yet a git repo; per-task commits and the eventual marketplace publish both need it.

**Files:**
- Create: `.gitignore` (repo root)

**Step 1: Init and set a baseline ignore**

```bash
cd /Users/martintreiber/Documents/Development/claude_chat
git init
```

**Step 2: Write repo-root `.gitignore`**

Create `.gitignore`:

```gitignore
.DS_Store
__pycache__/
*.pyc
.groupchat/chat.db*
.dev-diary/.events.jsonl
```

**Step 3: Baseline commit**

```bash
git add -A
git commit -m "chore: initialize git repository

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: a clean working tree (`git status` shows nothing to commit).

---

### Task 2: Token-summing helper in `chat.py`

A pure function that reads a Claude Code transcript (JSONL) and sums `usage` across assistant messages. Best-effort; returns zeros on any error.

**Files:**
- Modify: `.groupchat/chat.py` (add helper near the other helpers, after `_env_int`, ~line 204)

**Step 1: Write the verification fixture + script**

```bash
export GROUPCHAT_DIR=/tmp/gc_plan
mkdir -p /tmp/gc_plan
cat > /tmp/gc_plan/transcript.jsonl <<'EOF'
{"type":"user","message":{"content":"hi"}}
{"type":"assistant","message":{"usage":{"input_tokens":100,"output_tokens":20,"cache_read_input_tokens":5,"cache_creation_input_tokens":2}}}
{"type":"assistant","message":{"usage":{"input_tokens":50,"output_tokens":10,"cache_read_input_tokens":1,"cache_creation_input_tokens":0}}}
{"type":"system","message":{}}
not-json-garbage-line
EOF
```

**Step 2: Run to verify it fails (function not defined yet)**

```bash
python3 -c "import sys; sys.path.insert(0,'.groupchat'); import chat; print(chat.sum_transcript_tokens('/tmp/gc_plan/transcript.jsonl'))"
```
Expected: `AttributeError: module 'chat' has no attribute 'sum_transcript_tokens'`.

**Step 3: Implement the helper**

Add to `.groupchat/chat.py` after `_env_int` (~line 204):

```python
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
```

**Step 4: Run to verify it passes**

```bash
python3 -c "import sys; sys.path.insert(0,'.groupchat'); import chat; print(chat.sum_transcript_tokens('/tmp/gc_plan/transcript.jsonl'))"
```
Expected exactly: `{'in_tokens': 150, 'out_tokens': 30, 'cache_read_tokens': 6, 'cache_create_tokens': 2}`

Also verify the error path returns zeros:
```bash
python3 -c "import sys; sys.path.insert(0,'.groupchat'); import chat; print(chat.sum_transcript_tokens('/no/such/file'))"
```
Expected: `{'in_tokens': 0, 'out_tokens': 0, 'cache_read_tokens': 0, 'cache_create_tokens': 0}`

**Step 5: Commit**

```bash
git add .groupchat/chat.py
git commit -m "feat: add transcript token-summing helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Token columns (guarded schema migration)

Add four `INTEGER NOT NULL DEFAULT 0` columns to `agents`, applied idempotently so existing dbs upgrade silently.

**Files:**
- Modify: `.groupchat/chat.py` — `_ensure_schema` (~line 121-154)

**Step 1: Verification — confirm columns are absent on a fresh db built by current code**

```bash
rm -rf /tmp/gc_plan; export GROUPCHAT_DIR=/tmp/gc_plan
python3 .groupchat/chat.py init
python3 -c "import sqlite3; c=sqlite3.connect('/tmp/gc_plan/chat.db'); print([r[1] for r in c.execute('PRAGMA table_info(agents)')])"
```
Expected: list WITHOUT `in_tokens` etc.

**Step 2: Implement the migration**

In `.groupchat/chat.py`, add a helper just above `_ensure_schema` (~line 120):

```python
def _add_column_if_missing(conn, table: str, col: str, decl: str) -> None:
    have = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
```

Then, inside `_ensure_schema`, AFTER the `conn.executescript(...)` block and BEFORE the `INSERT OR IGNORE INTO meta` line, add:

```python
    # Token-usage columns (added post-v1; guarded so old dbs upgrade in place).
    for _col in ("in_tokens", "out_tokens", "cache_read_tokens", "cache_create_tokens"):
        _add_column_if_missing(conn, "agents", _col, "INTEGER NOT NULL DEFAULT 0")
```

**Step 3: Verify the columns now exist (fresh AND upgraded db)**

```bash
rm -rf /tmp/gc_plan
python3 .groupchat/chat.py init
python3 -c "import sqlite3; c=sqlite3.connect('/tmp/gc_plan/chat.db'); print([r[1] for r in c.execute('PRAGMA table_info(agents)')])"
```
Expected: list now INCLUDES `in_tokens out_tokens cache_read_tokens cache_create_tokens`.

Upgrade path (build an old-shaped db, then let connect() migrate):
```bash
rm -rf /tmp/gc_plan; mkdir -p /tmp/gc_plan
python3 -c "
import sqlite3
c=sqlite3.connect('/tmp/gc_plan/chat.db')
c.executescript('CREATE TABLE agents(session_id TEXT PRIMARY KEY, handle TEXT);')
c.close()
import sys; sys.path.insert(0,'.groupchat'); import chat
conn=chat.connect()  # should ALTER in the missing columns, not crash
print('upgraded:', [r['name'] for r in conn.execute('PRAGMA table_info(agents)')])
"
```
Expected: prints `upgraded:` with the four token columns present, no exception.

**Step 4: Commit**

```bash
git add .groupchat/chat.py
git commit -m "feat: add token-usage columns to agents (guarded migration)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `record_tokens` writer

**Files:**
- Modify: `.groupchat/chat.py` — add after `set_status` (~line 326)

**Step 1: Implement**

```python
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
```

**Step 2: Verify**

```bash
rm -rf /tmp/gc_plan; export GROUPCHAT_DIR=/tmp/gc_plan
python3 .groupchat/chat.py init
python3 -c "
import sys; sys.path.insert(0,'.groupchat'); import chat
conn=chat.connect(); chat.register(conn,'s1',handle='ada')
chat.record_tokens(conn,'s1',{'in_tokens':150,'out_tokens':30,'cache_read_tokens':6,'cache_create_tokens':2})
r=chat.agent_by_session(conn,'s1')
print(r['out_tokens'], r['in_tokens'], r['cache_read_tokens'], r['cache_create_tokens'])
"
```
Expected: `30 150 6 2`

**Step 3: Commit**

```bash
git add .groupchat/chat.py
git commit -m "feat: add record_tokens writer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `tokens` CLI subcommand + `who` suffix

**Files:**
- Modify: `.groupchat/chat.py` — add `_fmt_count` + `cmd_tokens` (near `cmd_who`, ~line 553); edit `cmd_who` (line 551); register subparser (~line 714).

**Step 1: Implement formatter + command**

Add near `cmd_who`:

```python
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
```

**Step 2: Add the token suffix to `cmd_who`**

In `cmd_who`, replace the print line (line 551) with:

```python
        toks = f" · {_fmt_count(r['out_tokens'])} out" if r["out_tokens"] else ""
        print(f"{flag} {r['handle']}{status}{cwd}  (seen {_hhmm(r['last_seen'] or '')}){toks}")
```

**Step 3: Register the subparser**

After the `who` parser block (~line 714), add:

```python
    sp = sub.add_parser("tokens", help="show approximate token usage per agent")
    sp.add_argument("--all", action="store_true", help="include inactive agents")
    sp.set_defaults(func=cmd_tokens)
```

**Step 4: Verify**

```bash
rm -rf /tmp/gc_plan; export GROUPCHAT_DIR=/tmp/gc_plan
python3 .groupchat/chat.py init
python3 -c "
import sys; sys.path.insert(0,'.groupchat'); import chat
conn=chat.connect(); chat.register(conn,'s1',cwd='/x',handle='ada')
chat.record_tokens(conn,'s1',{'in_tokens':480000,'out_tokens':12300,'cache_read_tokens':2100000,'cache_create_tokens':0})
"
python3 .groupchat/chat.py tokens
echo "--- who ---"
python3 .groupchat/chat.py who
```
Expected: `tokens` prints a header line, an `ada  out 12.3k  in 480.0k  cache-read 2.1M ...` row and a `TEAM` row. `who` shows `● ada ... · 12.3k out`.

**Step 5: Commit**

```bash
git add .groupchat/chat.py
git commit -m "feat: add tokens command and token suffix in who

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Runtime `.gitignore` bootstrap in `connect()`

So a freshly-created target-repo `.groupchat/` (which holds only the db under the plugin) never gets committed. Guarded by `if not exists`, so this dev repo's committed `.groupchat/.gitignore` is untouched.

**Files:**
- Modify: `.groupchat/chat.py` — `connect()` (~line 109-118)

**Step 1: Implement**

In `connect()`, right after `os.makedirs(d, exist_ok=True)` (line 111):

```python
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        try:
            with open(gi, "w") as fh:
                fh.write("# group chat runtime — do not commit\n*\n")
        except Exception:
            pass
```

**Step 2: Verify (fresh dir gets ignore; existing one is left alone)**

```bash
rm -rf /tmp/gc_plan; export GROUPCHAT_DIR=/tmp/gc_plan
python3 .groupchat/chat.py init
cat /tmp/gc_plan/.gitignore
echo "--- idempotent: pre-existing ignore is preserved ---"
printf 'custom\n' > /tmp/gc_plan/.gitignore
python3 .groupchat/chat.py who >/dev/null
cat /tmp/gc_plan/.gitignore   # should still say 'custom'
```
Expected: first `cat` shows `*`; second still shows `custom`.

Confirm this repo's own ignore is unchanged:
```bash
git diff --exit-code .groupchat/.gitignore && echo "dev repo .gitignore untouched"
```
Expected: prints "dev repo .gitignore untouched".

**Step 3: Commit**

```bash
git add .groupchat/chat.py
git commit -m "feat: bootstrap .gitignore in freshly created chat dirs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Meter tokens in the Stop hook

Read `transcript_path` from the Stop payload and update the agent's totals — fail-open, before the mention/barrier logic.

**Files:**
- Modify: `.groupchat/hooks/stop.py` — inside `main()`, after `chat.register(conn, sid)` (~line 78)

**Step 1: Implement**

In `stop.py`, immediately after `chat.register(conn, sid)  # refresh last-seen` and before `path = os.path.abspath(...)`:

```python
    # Meter token usage for this session (best-effort; never blocks a stop).
    try:
        tp = data.get("transcript_path")
        if tp:
            chat.record_tokens(conn, sid, chat.sum_transcript_tokens(tp))
    except Exception:
        pass
```

**Step 2: Verify the happy path updates the row**

```bash
rm -rf /tmp/gc_plan; export GROUPCHAT_DIR=/tmp/gc_plan
export GROUPCHAT_TEAM_SIZE=2   # keep barrier unsatisfied so we exercise the early path
cat > /tmp/gc_plan/t.jsonl <<'EOF'
{"type":"assistant","message":{"usage":{"input_tokens":100,"output_tokens":20,"cache_read_input_tokens":5,"cache_creation_input_tokens":2}}}
EOF
echo '{"session_id":"s1","hook_event_name":"SessionStart"}' | python3 .groupchat/hooks/session_start.py >/dev/null
echo '{"session_id":"s2","hook_event_name":"SessionStart"}' | python3 .groupchat/hooks/session_start.py >/dev/null
echo '{"session_id":"s1","hook_event_name":"Stop","stop_hook_active":false,"transcript_path":"/tmp/gc_plan/t.jsonl"}' \
  | GROUPCHAT_PARK_WINDOW=1 GROUPCHAT_POLL_TICK=1 python3 .groupchat/hooks/stop.py >/dev/null
python3 .groupchat/chat.py tokens --all | grep -i "out 20"
```
Expected: a row showing `out 20` (s1's agent recorded the transcript usage).

**Step 3: Verify fail-open with a bad transcript path (must still exit 0, not crash)**

```bash
echo '{"session_id":"s1","hook_event_name":"Stop","stop_hook_active":false,"transcript_path":"/no/such.jsonl"}' \
  | GROUPCHAT_PARK_WINDOW=1 GROUPCHAT_POLL_TICK=1 python3 .groupchat/hooks/stop.py >/dev/null; echo "exit=$?"
```
Expected: `exit=0`.

**Step 4: Commit**

```bash
git add .groupchat/hooks/stop.py
git commit -m "feat: meter session tokens in the Stop hook

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Plugin manifest + marketplace listing

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`

**Step 1: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "groupchat",
  "description": "Shared chat bus for parallel Claude Code instances on one repo — coordinate via hooks, @mentions, a team barrier, and token tracking.",
  "version": "0.1.0",
  "author": { "name": "Martin Treiber", "email": "martin.treiber@gmail.com" }
}
```

**Step 2: Write `.claude-plugin/marketplace.json`**

```json
{
  "name": "groupchat-marketplace",
  "owner": { "name": "Martin Treiber" },
  "plugins": [
    {
      "name": "groupchat",
      "source": "./",
      "description": "Group chat for parallel Claude Code instances on one repo."
    }
  ]
}
```

**Step 3: Verify JSON is valid**

```bash
python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); json.load(open('.claude-plugin/marketplace.json')); print('valid')"
```
Expected: `valid`.

**Step 4: Commit**

```bash
git add .claude-plugin/
git commit -m "feat: add plugin manifest and marketplace listing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Plugin hooks wiring

**Files:**
- Create: `hooks/hooks.json`

**Step 1: Write `hooks/hooks.json`** (same commands/options as `HOOK_ENTRIES`/`HOOK_OPTIONS`, but via `${CLAUDE_PLUGIN_ROOT}`)

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/.groupchat/hooks/session_start.py\"" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/.groupchat/hooks/user_prompt_submit.py\"", "timeout": 15 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/.groupchat/hooks/stop.py\"", "timeout": 600, "statusMessage": "⏳ waiting for teammates at the group-chat barrier…" } ] }
    ]
  }
}
```

**Step 2: Verify valid JSON**

```bash
python3 -c "import json; json.load(open('hooks/hooks.json')); print('valid')"
```
Expected: `valid`.

**Step 3: Commit**

```bash
git add hooks/hooks.json
git commit -m "feat: wire plugin hooks via CLAUDE_PLUGIN_ROOT

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Usage skill

Lift the "How you should use the chat" guidance from CLAUDE.md into an agent-facing skill so every plugin-installed repo gets it.

**Files:**
- Create: `skills/groupchat/SKILL.md`

**Step 1: Write `skills/groupchat/SKILL.md`**

```markdown
---
name: groupchat
description: Use when this repo has the group-chat plugin installed and other Claude Code instances may be working the same repo in parallel — how to coordinate via the shared chat (announce work, flag files, @mention teammates, answer mentions, wait at the team barrier).
---

# Group chat for parallel instances

Several Claude Code instances may be working this repo at once. A shared chat bus
(SQLite, managed by the plugin's hooks) is your team channel. New messages arrive
in your context automatically before each turn — never poll.

Your handle is in your SessionStart briefing. Use it to post.

## Do
- **Announce before you act.** "Starting on `src/auth/handler.py`" prevents two
  agents editing the same file.
- **@mention** the specific agent when you need a reply; a plain message is a
  broadcast. Only @mentions block a teammate's Stop, so reserve them for things
  needing a response.
- **Answer mentions.** If your Stop surfaces an unanswered @mention, reply in chat
  before finishing.
- **Stop normally when your slice is done.** You won't exit when *you* finish —
  the Stop hook parks you (dormant, ~0 tokens) at the team barrier and wakes you
  if a teammate @mentions you. Don't poll or spin to stay available.
- **Declare team size early** if you know it: `chat.py expect N` (or launch with
  `GROUPCHAT_TEAM_SIZE=N`). Otherwise a 90s startup grace applies.

## CLI (the absolute path is in your SessionStart briefing)
- `send --from <you> "msg, @mention to ping"` — post
- `who` — roster (active ● / idle ○), with each agent's approx output tokens
- `tokens` — approximate per-agent token usage (from the local transcript)
- `inbox --from <you>` — your unread @mentions
- `done --from <you>` — mark your slice done (wait at the barrier)

Slash commands `/groupchat:who`, `/groupchat:chat`, `/groupchat:inbox`,
`/groupchat:tokens` wrap these.
```

**Step 2: Verify frontmatter parses**

```bash
python3 -c "
import re,sys
t=open('skills/groupchat/SKILL.md').read()
assert t.startswith('---'), 'missing frontmatter'
fm=t.split('---',2)[1]
assert 'name:' in fm and 'description:' in fm, 'missing keys'
print('skill frontmatter ok')
"
```
Expected: `skill frontmatter ok`.

**Step 3: Commit**

```bash
git add skills/groupchat/SKILL.md
git commit -m "feat: add groupchat usage skill

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Slash commands

Thin wrappers that reuse the `chat.py` path from the SessionStart briefing (NOT `${CLAUDE_PLUGIN_ROOT}` — it does not expand in command markdown, bug #9354).

**Files:**
- Create: `commands/who.md`
- Create: `commands/chat.md`
- Create: `commands/inbox.md`
- Create: `commands/tokens.md`

**Step 1: Write the four command files**

`commands/who.md`:
```markdown
---
description: Show the group-chat roster (active agents)
---
Run the group-chat CLI — its absolute `chat.py` path is in your SessionStart
group-chat briefing (the line showing `send --from <handle>`). Invoke it with the
`who` subcommand and show me the roster output verbatim.
```

`commands/chat.md`:
```markdown
---
description: Post a message to the group chat (use @handle to mention)
argument-hint: <message>
---
Using the group-chat CLI path from your SessionStart briefing, post this message
as your own handle, then confirm it sent:

    <cli> send --from <your-handle> "$ARGUMENTS"
```

`commands/inbox.md`:
```markdown
---
description: Show your unread @mentions in the group chat
---
Using the group-chat CLI path from your SessionStart briefing, run the `inbox`
subcommand as your own handle and show me any unread @mentions.
```

`commands/tokens.md`:
```markdown
---
description: Show approximate per-agent token usage
---
Using the group-chat CLI path from your SessionStart briefing, run the `tokens`
subcommand and show me the per-agent token report verbatim.
```

**Step 2: Verify all four exist with frontmatter**

```bash
for f in who chat inbox tokens; do
  head -1 "commands/$f.md" | grep -q '^---' && echo "$f ok" || echo "$f BAD"
done
```
Expected: `who ok`, `chat ok`, `inbox ok`, `tokens ok`.

**Step 3: Commit**

```bash
git add commands/
git commit -m "feat: add groupchat slash commands

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Documentation updates

**Files:**
- Modify: `CLAUDE.md` — add an "Installing as a plugin" subsection under Commands, and note token tracking + the dogfooding caveat.
- Modify: `README.md` — add plugin install instructions.

**Step 1: Add to `CLAUDE.md`** (after the existing "Setup / portability" block in the Commands section)

```markdown
### Installing as a plugin

Besides `chat.py install`, the system ships as a Claude Code plugin (this repo is
its own marketplace):

    /plugin marketplace add <owner>/<repo>
    /plugin install groupchat

The plugin carries the code (hooks + chat.py) under `${CLAUDE_PLUGIN_ROOT}`; the
runtime `chat.db` is still created in the *target* repo's `.groupchat/`
(gitignored). It also bundles the `groupchat` usage skill and the
`/groupchat:{who,chat,inbox,tokens}` commands.

**Do not install the plugin in *this* dev repo** — it already wires the hooks via
`.claude/settings.json`, and both at once would double-fire the hooks.

### Token tracking

The Stop hook meters each session's transcript (`transcript_path`) into four
`agents` columns (`in/out/cache_read/cache_create`). See them with `chat.py
tokens` (or `/groupchat:tokens`); `who` shows each agent's output tokens. Counts
are approximate (from the local transcript) — useful for *relative* burn and for
confirming a parked agent is idle, not for billing.
```

**Step 2: Add a short plugin section to `README.md`** (place near existing install instructions; match surrounding tone):

```markdown
## Install as a plugin

    /plugin marketplace add <owner>/<repo>
    /plugin install groupchat

Restart Claude in the target repo and the chat is live for every instance. The
runtime database lives in that repo's `.groupchat/` (gitignored); the code ships
with the plugin. The classic `python3 .groupchat/chat.py install /path/to/repo`
copy-in method still works too.
```

**Step 3: Verify the docs mention the new pieces**

```bash
grep -q "Installing as a plugin" CLAUDE.md && grep -q "Token tracking" CLAUDE.md && grep -q "plugin" README.md && echo "docs updated"
```
Expected: `docs updated`.

**Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document plugin install and token tracking

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: End-to-end plugin smoke test (local path source)

Prove the plugin installs and the hooks resolve `chat.db` into a *target* repo, without publishing anywhere.

**Files:** none (manual verification + a throwaway target repo)

**Step 1: Simulate a plugin-style hook invocation against a fresh target repo**

The plugin path-resolution boils down to: hooks run with cwd = target repo, code referenced via an absolute plugin path. Emulate that:

```bash
unset GROUPCHAT_DIR GROUPCHAT_TEAM_SIZE
PLUGIN_ROOT=/Users/martintreiber/Documents/Development/claude_chat
TARGET=$(mktemp -d)/myrepo; mkdir -p "$TARGET"; ( cd "$TARGET" && git init -q )

# SessionStart, run from the target repo, code from the "plugin"
( cd "$TARGET" && echo '{"session_id":"s1","cwd":"'"$TARGET"'","hook_event_name":"SessionStart"}' \
    | python3 "$PLUGIN_ROOT/.groupchat/hooks/session_start.py" >/dev/null )

echo "--- db should be created INSIDE the target repo, not the plugin ---"
ls "$TARGET/.groupchat/" 2>/dev/null
echo "--- and gitignored ---"
cat "$TARGET/.groupchat/.gitignore"
echo "--- plugin's own .groupchat must NOT have a target db ---"
ls "$PLUGIN_ROOT/.groupchat/chat.db" 2>/dev/null && echo "(this is the dev repo's own db — expected)"
```
Expected: `$TARGET/.groupchat/chat.db` exists; its `.gitignore` is `*`; the agent was registered in the target repo's db.

**Step 2: Confirm roster reads from the target db**

```bash
( cd "$TARGET" && python3 "$PLUGIN_ROOT/.groupchat/chat.py" who )
```
Expected: shows one active agent (e.g. `● ada ...`).

**Step 3: Clean up**

```bash
rm -rf "$(dirname "$TARGET")"
```

**Step 4: Final verification of the whole suite**

Re-run the earlier task verifications in one isolated room to confirm nothing regressed (init → register two → record tokens via Stop → `tokens` → `who`). If all expected outputs hold, the feature is complete.

**Step 5: Commit (if any doc/cleanup changes were needed); otherwise nothing to commit.**

---

## Done criteria

- `chat.py tokens` and `/groupchat:tokens` report per-agent + team totals; `who`
  shows an output-token suffix.
- Stop hook records transcript tokens, fail-open on a bad/missing transcript.
- Existing dbs upgrade silently (guarded ALTER); fresh target `.groupchat/` is
  auto-gitignored; this dev repo's committed `.groupchat/.gitignore` is untouched.
- `.claude-plugin/{plugin,marketplace}.json`, `hooks/hooks.json`,
  `skills/groupchat/SKILL.md`, and `commands/*.md` exist and are valid.
- A path-source smoke test shows hooks create `chat.db` in the *target* repo.
- CLAUDE.md / README document the plugin path, token tracking, and the
  no-self-install dogfooding caveat.
- The existing `chat.py install` path and all current hook behavior are unchanged.
```
