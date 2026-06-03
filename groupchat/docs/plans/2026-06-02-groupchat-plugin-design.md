# Design: package the group chat as a distributable Claude Code plugin

**Date:** 2026-06-02
**Status:** validated, ready for implementation
**Topic:** turn the `.groupchat/` system into a shareable plugin, add a usage
skill, slash commands, and per-session token tracking.

## Goal

Make the parallel-instance group chat installable into any repo as a real Claude
Code **plugin** (added via `/plugin` from a marketplace), so users get the hooks
wired automatically without copying files or hand-editing `settings.json`. Keep
the existing `chat.py install <repo>` copy-into-repo path working as a second
delivery mechanism (dual distribution).

Also: bundle the coordination etiquette as a **skill** so every repo gets it,
add convenience **slash commands**, and add a **token-tracking** feature that
records and reports per-session token consumption.

## Approach: wrap, don't rewrite

At hook runtime, plugin code lives in the plugin cache (`${CLAUDE_PLUGIN_ROOT}`)
but the working directory is the user's repo, and `chat.py:store_dir()` already
resolves `chat.db` by walking cwd → git common dir. So the split falls out
naturally:

- **Code** (chat.py, hooks) ships *in the plugin* — installed once, never
  committed to target repos.
- **The bus** (`chat.db`) is created at runtime in the *target repo's*
  `.groupchat/`, gitignored, shared across all its worktrees/instances — exactly
  as today.

The plugin is therefore almost entirely additive scaffolding around the existing,
tested `.groupchat/` tree. The only change to existing hook wiring: the command
path swaps `$CLAUDE_PROJECT_DIR/.groupchat/hooks/...` → `${CLAUDE_PLUGIN_ROOT}/.groupchat/hooks/...`.

**Dogfooding note:** this dev repo already wires the hooks through its own
`.claude/settings.json`. It must NOT also install the plugin (hooks would
double-fire). External repos use the plugin; this repo keeps `settings.json`.

## Verified platform facts (via claude-code-guide)

- `${CLAUDE_PLUGIN_ROOT}` **expands in hook JSON** (SessionStart, UserPromptSubmit,
  Stop) — correct for bundled scripts.
- `${CLAUDE_PLUGIN_ROOT}` does **NOT** expand in slash-command markdown (open bug
  #9354). Commands must not rely on it.
- Hooks run with **cwd = the user's repo**; `CLAUDE_PROJECT_DIR` = repo root,
  `CLAUDE_PLUGIN_ROOT` = plugin install dir. Writing `chat.db` into the repo from
  a hook is the supported pattern.
- A single plugin can bundle hooks + skill + commands together.
- Hook payloads include a stable, readable `transcript_path` (present from
  SessionStart); assistant messages in that JSONL carry a `usage` object
  (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`). No direct token field in the hook payload itself.
  Caveat: third-party analyses report the JSONL input/output counts can
  undercount (output excludes thinking); cache counts are reliable. Treat totals
  as best-available, labeled approximate.

## File layout

This repo becomes both the plugin and its own one-plugin marketplace. New files
marked `+`, edits marked `~`:

```
claude_chat/
├── .claude-plugin/
│   ├── plugin.json          + manifest: name, version, description
│   └── marketplace.json     + lists this repo as a one-plugin marketplace
├── hooks/
│   └── hooks.json           + wires the 3 hooks via ${CLAUDE_PLUGIN_ROOT}
├── skills/
│   └── groupchat/
│       └── SKILL.md         + the "how to use the chat" etiquette
├── commands/
│   ├── who.md               + /groupchat:who    → roster
│   ├── chat.md              + /groupchat:chat   → post a message
│   ├── inbox.md             + /groupchat:inbox  → your unread @mentions
│   └── tokens.md            + /groupchat:tokens → token report
├── .groupchat/              ~ unchanged transport; chat.py install path still valid
│   ├── chat.py              ~ + token columns, `tokens` cmd, db-dir/gitignore bootstrap
│   └── hooks/{session_start,user_prompt_submit,stop,_hooklib}.py  ~ stop.py meters tokens
├── CLAUDE.md                ~ add "Installing as a plugin" + token-tracking notes
└── README.md                ~ add plugin install instructions
```

`hooks/hooks.json` carries the same per-hook options as the existing
`HOOK_OPTIONS`: Stop `timeout: 600` + the barrier `statusMessage`,
UserPromptSubmit `timeout: 15`. Parking behaves identically under the plugin.

The skill is a near-verbatim lift of the "How you (a Claude instance here) should
use the chat" section of CLAUDE.md, rephrased as agent-facing instructions. This
is the real win: today that etiquette only reaches instances because it sits in
*this* repo's CLAUDE.md — a target repo wouldn't get it. As a bundled skill,
every repo with the plugin gets it automatically.

## Runtime mechanics

**Hooks (core).** `hooks/hooks.json` references
`${CLAUDE_PLUGIN_ROOT}/.groupchat/hooks/*.py`. Hooks run with cwd = target repo,
so `import chat` + `store_dir()` resolve the db into the repo's `.groupchat/`.
Resolution logic is unchanged.

**Slash commands sidestep the #9354 bug.** Because commands can't use
`${CLAUDE_PLUGIN_ROOT}`, they hardcode no path. `session_start.py` already injects
the absolute `chat.py` path into every instance's briefing (the
`send --from <handle>` line). Each command is a thin instruction that reuses that
context, e.g. `who.md`: "Run the group-chat CLI from your SessionStart briefing
with the `who` subcommand and show me the roster." Same for `chat`
(→ `send --from <handle> "$ARGUMENTS"`), `inbox`, and `tokens`.

**Db creation + gitignore.** On first contact in a fresh repo, `session_start.py`
(via `register()`/`connect()`) ensures `.groupchat/` exists and drops a one-line
`.gitignore` (`*`) into it, so the runtime db is never accidentally committed.
Today that gitignore is a committed file; under the plugin the repo has no
committed `.groupchat/`, so the hook must create it. Small, contained change.

## Token tracking feature

**Meter tick.** `stop.py` fires at the end of every turn. Early in the hook
(before the parking logic, inside the fail-open wrapper) it reads
`transcript_path`, sums `usage` across assistant messages, and writes the totals
to the agent's row. Idempotent — recomputed cumulative-from-transcript each time,
so re-parks and resumes can't double-count. A token-read error must never block a
Stop.

**Schema.** Four columns added to the existing `agents` table via guarded
`ALTER TABLE` (existing dbs upgrade silently): `in_tokens`, `out_tokens`,
`cache_read_tokens`, `cache_create_tokens`. No new table — usage is per-session
and `agents` is keyed by session.

**Surfacing.**
- `chat.py tokens` — per-agent breakdown + a `TEAM` total row, e.g.
  `ada    out 12.3k  in 480k  cache-read 2.1M`.
- `who` gains a compact suffix: `● ada — active [/x] (seen 12:03) · 12k out`.
- `/groupchat:tokens` wraps the command.
- Output labeled `~approx (from local transcript)` so absolute numbers aren't
  over-trusted.

**Why it fits.** It measures the recurring claim that a *parked* agent costs ~0
tokens: `tokens` on a parked agent visibly flatlines between re-parks, turning
the barrier's "dormant, free" property into something observable rather than
asserted.

## Install / distribution flow

`.claude-plugin/marketplace.json` lists this repo (github source). End-user flow:

```
/plugin marketplace add <owner>/<repo>
/plugin install groupchat
# restart Claude in the target repo → chat is live for every instance
```

`plugin.json` sets `version`; bumping it is how users receive updates.

## Existing-file change summary

- `.groupchat/chat.py` — add 4 token columns (guarded ALTER), a `tokens`
  subcommand, a token suffix in `who`, and `.groupchat/` dir + `.gitignore`
  bootstrap in the connect/register path. The existing `install` command and CLI
  are otherwise untouched.
- `.groupchat/hooks/stop.py` — add a fail-open token-metering step that reads the
  transcript and updates the agent's token totals, before the mention/barrier
  logic.
- `CLAUDE.md`, `README.md` — document the plugin install path and token tracking.

## Open notes / caveats

- Token absolute accuracy is best-effort (see verified facts). The feature's
  value is relative/observational, not billing-grade.
- This repo is not currently a git repo (`git init` needed before a real
  marketplace publish; the plugin can still be tested locally via a path source).
- Transcript size: summing the whole transcript each Stop is fine for bounded
  sessions; if it ever becomes a latency concern, switch to an incremental
  byte-offset sum.
