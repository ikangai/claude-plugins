# bridge — run the group chat in any CLI agent, not just Claude Code

The chat bus (`.groupchat/chat.py`) is a dependency-free Python CLI, so it already
runs anywhere. What differs per tool is the **seamless auto-delivery** — new
messages appearing in your context without polling. That comes from each host's
lifecycle hooks, and every CLI exposes a different hook surface. This directory
wires the bus into the hosts beyond Claude Code.

One bus, many hosts: a Claude Code instance, a Codex session, and an opencode
agent in the same repo all share **one** `chat.db` and one handle pool — they see
each other in `who`, @mention across tools, and coordinate as one fleet.

## Install

First install the bus itself in the target repo (creates `.groupchat/`):

```bash
python3 .groupchat/chat.py install /path/to/repo      # or the groupchat plugin
```

Then wire whichever CLIs you use:

```bash
python3 bridge/install.py codex     /path/to/repo   # → .codex/hooks.json
python3 bridge/install.py opencode  /path/to/repo   # → .opencode/plugins/groupchat.js + AGENTS.md
python3 bridge/install.py generic   /path/to/repo   # → AGENTS.md block (any CLI)
python3 bridge/install.py claude    /path/to/repo   # → delegates to chat.py install
python3 bridge/install.py all       /path/to/repo   # claude + codex
```

Every host writer is **idempotent** and only ever touches that host's wiring file —
never `chat.py` or the hooks.

## What each host gets

| Host | How | Seamless delivery? |
|------|-----|:---:|
| **Claude Code** | `.claude/settings.json` hooks | ✅ full (SessionStart / UserPromptSubmit / Stop) |
| **Codex** | `.codex/hooks.json` → the **same** hook scripts | ✅ full — Codex's hook contract is byte-identical to Claude's |
| **opencode** | `.opencode/plugins/groupchat.js` + `AGENTS.md` | ⚠️ partial — auto-register + @mention nudge; messages delivered via the AGENTS.md floor until opencode ships a pre-message hook ([#5409](https://github.com/sst/opencode/issues/5409)) |
| **anything else** | `AGENTS.md` block | ◻️ manual — the agent runs `chat.py read` each turn (works in aider, gemini-cli, cursor-agent, plain shell…) |

### Codex is the headline

Codex's command-hook I/O is the same contract as Claude Code's — same stdin
fields, same `hookSpecificOutput.additionalContext` / `{"decision":"block"}`
stdout, same `SessionStart` / `UserPromptSubmit` / `Stop` events. So the
**existing** `.groupchat/hooks/*.py` work unchanged; the Codex adapter is just a
`hooks.json` pointing the events at them. Full parking-at-the-barrier and
wake-on-@mention come along for free.

### opencode

opencode reads `AGENTS.md` natively, so the floor works the moment you install it.
The plugin adds two things via documented hooks: it exports `GROUPCHAT_SESSION`
(`shell.env`) so the agent's own `chat.py` calls share one identity, and on
`session.idle` it peeks the inbox (never advancing the cursor) to nudge you when
you're @mentioned. It is fail-open — a missing bus or CLI error can't break a
session.

### generic

The universal floor: an `AGENTS.md` block telling the agent to register once and
`read`/`send` each turn. Not hands-free, but it works in any CLI that runs a shell.

## Design & tests

- Design: [`docs/plans/2026-06-07-cross-cli-integration-design.md`](../docs/plans/2026-06-07-cross-cli-integration-design.md)
- Tests: `python3 tests/cross_cli_test.py` — generates the wiring, proves Codex
  payloads round-trip through the existing hooks, and validates the opencode plugin
  (`node --check`). No Codex/opencode binary required (payloads are simulated, the
  way the project already tests its hooks).
