# Cross-CLI integration — make the bus host-agnostic

*Author: gauss · 2026-06-07 · status: iterations 1–3 shipped (Codex, generic, opencode) — `bridge/`, `tests/cross_cli_test.py` (65 green)*

## The hard boundary

The one non-negotiable goal constraint: the group chat must **integrate seamlessly
into Claude Code, Codex, opencode, and other CLI dev tools** — not just Claude Code.
Today the system is Claude-only: the three hooks are wired through
`.claude/settings.json`, and nothing else knows the bus exists.

## The key realisation: the core is already portable

`chat.py` is a dependency-free stdlib CLI. Its primitives —
`register`, `send`, `read`, `inbox`, `who`, `heartbeat`, `done` — work in **any**
shell, under any agent, on any OS with Python 3. The store auto-resolves to the
git common dir, so it works outside Claude with no env vars.

So the bus *itself* is host-neutral. What is **not** portable is the **seamless
auto-injection** — the thing that makes you never have to poll. That is delivered
by host lifecycle hooks, and every CLI exposes a *different* hook surface.

Cross-CLI integration is therefore **not** a rewrite. It is: for each host, wire
its lifecycle events to the three universal jobs the bus needs.

## The three universal jobs

Every integration, on every host, reduces to these three:

1. **On session start** → `register` the agent (assign a handle) + inject a briefing
   (active teammates + recent chat + how to post).
2. **Before each turn** → inject messages newer than this agent's read cursor,
   then advance the cursor. Silent when nothing is new.
3. **On stop / turn-end** → if an unread message @mentions me, block and hand it
   back; else mark `done` and park at the team barrier.

The Claude hooks (`session_start.py`, `user_prompt_submit.py`, `stop.py`) already
implement exactly these three, reading a generic JSON payload on stdin and emitting
a generic JSON result on stdout (`_hooklib.read_input` / `emit_context`). They are
**host-agnostic Python that happens to be wired for Claude.**

## Host capability matrix

| Host        | session-start hook | pre-turn inject hook | stop/turn-end hook | wiring file | injection contract |
|-------------|:---:|:---:|:---:|---|---|
| Claude Code | ✅ SessionStart | ✅ UserPromptSubmit | ✅ Stop | `.claude/settings.json` | `hookSpecificOutput.additionalContext` / `decision:block` |
| **Codex**   | ✅ SessionStart | ✅ UserPromptSubmit | ✅ Stop | `.codex/hooks.json` *or* `config.toml` | **identical** to Claude |
| **opencode**| ⚠️ `session.created` | ❌ (none stable) | ⚠️ `session.idle` | `.opencode/plugins/*.js` | Bun `$` + `tui.*` actions |
| **generic** | ❌ | ❌ | ❌ | `AGENTS.md` snippet | agent calls `chat.py` by hand |

### Codex — near-free (iteration 1)

Codex's command-hook contract is **byte-identical** to Claude Code's:

- **stdin**: `session_id`, `cwd`, `hook_event_name`, `transcript_path`,
  `prompt` (UserPromptSubmit), `stop_hook_active` + `last_assistant_message` (Stop),
  `source` (SessionStart). Plus extras Codex adds and our hooks ignore: `model`,
  `permission_mode`, `turn_id`.
- **stdout**: `{"hookSpecificOutput":{"hookEventName":..,"additionalContext":..}}`
  for injection; `{"decision":"block","reason":..}` to block.
- **events**: same names — `SessionStart`, `UserPromptSubmit`, `Stop`.

So the **existing hook scripts work unchanged**. The Codex adapter is purely a
generated `.codex/hooks.json` that points the three events at the same
`.groupchat/hooks/*.py`. We depend only on the *I/O contract*, never the internals,
so this stays decoupled from the hierarchy team's in-flight hook refactor.

**The team barrier works on Codex too — confirmed by the spec.** Codex command
hooks "run synchronously and block": Codex waits for the hook process to exit or
hit its `timeout` (default **600s**) before completing the turn. So `stop.py`'s
sleep-park (a ~570s blocking window) genuinely holds a Codex session dormant at the
barrier, exactly as on Claude — and Codex's Stop `decision:block` "creates a new
continuation prompt … using your reason as that prompt text", which is the same
re-park / wake-on-@mention loop. Our generated wiring sets `timeout: 600` so the
570s window fits with headroom. (Still worth one smoke test against a real Codex
binary, but the contract — block-on-Stop, 600s timeout, block→continuation — is
documented, not assumed.)

### opencode — partial (iteration 2)

opencode's plugin API (`.opencode/plugins/*.js`, ESM, Bun `$` shell) has **no
stable pre-message inject hook** — only `session.created`, `session.idle`,
`experimental.session.compacting`, the `tool.execute.*` pair, and `tui.*` actions.
So full pre-turn injection isn't possible yet (tracked upstream: opencode #5409).
The plugin delivers what *is* possible:

- `session.created` → `register` + briefing via `tui.toast.show`.
- `session.idle` (turn end) → surface unread; queue into the next prompt via
  `tui.prompt.append` where available.
- The agent always has the `chat.py` CLI for explicit `send`/`read`.

Designed to light up fully the moment opencode ships a pre-message hook.

### generic — universal floor (iteration 3)

For any CLI with no hooks (aider, gemini-cli, plain shell): an `AGENTS.md` /
instruction snippet that tells the agent to `register` once and `read`/`send`
each turn. Pull-based, not seamless, but works **everywhere** Python runs.

## The installer

`bridge/install.py --host <claude|codex|opencode|generic|all> [target]`:

- **codex** → idempotently merge `<target>/.codex/hooks.json` with the three hook
  entries (absolute `python3 <hook>.py` commands, `Stop` gets `timeout:600`).
- **claude** → delegate to `chat.py install` (the existing path).
- **opencode** → copy the plugin to `<target>/.opencode/plugins/groupchat.js`.
- **generic** → write/print the `AGENTS.md` snippet.

It never touches `chat.py` or the hooks — only host wiring files. Idempotent like
`chat.py install`.

## Testing (no Codex/opencode binary required)

Same method the project already uses for hooks — **pipe host-native JSON payloads
on stdin and assert effects** (`GROUPCHAT_DIR` isolates the room):

- `tests/codex_compat_test.py` — feed Codex-shaped `SessionStart` /
  `UserPromptSubmit` / `Stop` payloads (including the extra `model`,
  `permission_mode`, `turn_id`, `source` fields) into the **existing** hooks and
  assert they register, inject `additionalContext`, and block on @mention. Proves
  the contract claim end-to-end.
- Installer tests — generated `.codex/hooks.json` is valid, wires all three events,
  and re-running adds nothing (idempotent).
- opencode plugin — `node --check` parse + exercising its shell-outs directly.

## Non-goals / deferred

- Codex/opencode **token metering** (their transcript formats differ; degrades to 0).
- A networked transport (still a shared file; out of scope per CLAUDE.md).
- Editing `chat.py` or the hooks — owned by other lanes this run (C3, one tree).
- Host-neutral briefing wording ("Claude Code instances" → "agent sessions"):
  a tiny follow-up for whoever owns `session_start.py`, coordinated in chat.
