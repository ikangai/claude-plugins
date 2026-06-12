# ikangai — Claude Code plugins

A [plugin marketplace](https://docs.anthropic.com/en/docs/claude-code) for
[Claude Code](https://claude.com/claude-code), maintained by
[ikangai](https://github.com/ikangai). This repo hosts several plugins — each
lives in its own top-level directory and is indexed in
`.claude-plugin/marketplace.json`. New plugins will be added over time.

## Install

Add the marketplace once, then install any plugin from it:

```
/plugin marketplace add ikangai/claude-plugins
/plugin install groupchat
```

Installed plugins update when the marketplace is refreshed
(`/plugin marketplace update ikangai`).

## Plugins

### groupchat `v0.5.0`

A shared chat bus for **parallel AI coding-agent sessions working on one repo**
(Claude Code, Codex, opencode, …). Sessions coordinate through a SQLite-backed
message bus: announce what they're starting, flag files they're about to touch,
@mention each other, and escalate to the human through a single elected lead.
Checking is automatic — hooks inject new messages into each session's context,
so nobody polls.

Highlights:

- **Seamless via hooks** — auto-registration, per-turn message injection, and a
  Stop-hook **team barrier** that keeps finished agents parked (dormant, ~0
  tokens) until the whole team is done.
- **Team bootstrap & naming** — `/groupchat:team` spawns the rest of the team as
  new Claude windows mapped to free handles (it asks how many when the room is
  empty); any session can `/rename` itself to a role (`frontend`, `reviewer`).
- **Hub-and-spoke leadership** — workers' `@human` questions funnel to one
  lead, who batches and escalates; the operator answers with a single reply.
- **Governance (opt-in)** — a tracked, human-ratified `CONSTITUTION.md` with an
  advisory parliament (motions, votes, repeal-first review).
- **Cross-CLI** — bridge adapters wire Codex, opencode, and any shell CLI onto
  the same bus.
- **Observability** — a read-only HTML dashboard (roster, conversation, token
  stats, parliament, barrier) plus `tokens` / `who` CLI views.

Docs and release notes: [`groupchat/README.md`](groupchat/README.md) ·
[`groupchat/RELEASES.md`](groupchat/RELEASES.md). Developed in
[martintreiber/claude_chat](https://github.com/martintreiber/claude_chat).

## Repo layout

```
.claude-plugin/marketplace.json   # the marketplace index (one entry per plugin)
groupchat/                        # each plugin in its own directory
  .claude-plugin/plugin.json      #   plugin manifest (name, version)
  ...                             #   the plugin's code, skills, commands, docs
LICENSE
```

To add a plugin: create a new top-level directory with its own
`.claude-plugin/plugin.json` and register it in
`.claude-plugin/marketplace.json`.

## License

MIT — see [LICENSE](LICENSE).
