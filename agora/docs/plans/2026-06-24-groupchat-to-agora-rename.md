# Rename: groupchat → Agora

*2026-06-24. Vision item #2. The plugin identity changes; behavior does not.*

## Why "Agora"

The system is no longer a chat room — it's a self-organizing polity working a repo:
per-squad barriers, fan-in, a hub-and-spoke lead, an advisory constitution + parliament
(sessions/agendas/decisions). **Agora** is the ancient assembly *and* the marketplace —
it holds both halves (the work gets built *and* the body deliberates) without implying
the assembly *decides* (which "Senate"/"Polis" would, inviting the capture fiction the
constitution explicitly forbids). See the vision doc for the alternatives weighed.

## The seam (why this is safe, not a rewrite)

The rename is two small chokepoints plus a mechanical text sweep — no logic changed:

- **`_env(suffix)`** — every env read funnels here. It prefers `AGORA_<suffix>`, falls
  back to the legacy `GROUPCHAT_<suffix>`, new spelling wins. `_env_int` routes through
  it; `stop.py` (which reads its tunables at module-load, before `chat` is imported) has
  a tiny inline twin. So all 18 env vars are dual-read in one place.
- **`_room_dirname(anchor)`** — picks the runtime room dir: the new `.agora`, but an
  existing `.groupchat/chat.db` room keeps being used (an existing `.agora` wins). So an
  in-flight room is never stranded, and `store_dir()` resolves `$AGORA_DIR` then the
  legacy `$GROUPCHAT_DIR`.
- **`_spawn_command`** emits `AGORA_*` for spawned children (the child runs the new code,
  which reads `AGORA_*` first).
- **Identity sweep** — `plugin.json` name `agora` (so commands namespace to `/agora:*`),
  the skill renamed `groupchat`→`agora`, and `/groupchat:*` → `/agora:*` across the
  command files and docs.

## Backward compatibility (what still works)

`$GROUPCHAT_DIR`, every `GROUPCHAT_*` tunable, and an existing `.groupchat` room are all
still honored — verified by `tests/rename_compat_test.py` (legacy env resolves; AGORA
wins on a tie; a legacy `.groupchat` room still works end-to-end). The full 32-module
suite stays green (two spawn-command tests updated to expect the new `AGORA_*` emission;
two tests deliberately keep setting the *legacy* input env to prove it's still read).

## Deferred — gated, not forgotten

- **The package/marketplace slug.** The *public* rename (publishing as `agora`) needs the
  name reserved externally — a human step. Until then the plugin works under the new
  identity locally; nothing is published.
- **The internal `.groupchat/` code-directory `git mv`** (chat.py/hooks) and the matching
  **bridge** rename (`bridge/opencode/groupchat.js`, the `GROUPCHAT_SESSION` session-env,
  and `cross_cli_test`'s assertions). Both are coupled to the `.groupchat/` *path* and are
  pure mechanical churn with their own test churn — they ride the publish, so this PR
  stays a clean, reviewable identity change rather than a 200-reference path move.

## Adversarial review (fresh eyes) — outcome

A 4-lens / 21-agent review (backcompat, store-dir·fail-open, spawn·shadow,
sweep·dormancy), each finding independently verified: **17 findings, 11 confirmed** (3 =
one should-fix reported by multiple lenses; 8 nits). The refuted set is the reassuring
part — the load-bearing claims were *verified correct*: spawn lineage under `AGORA_*`, the
`_env`→`_envlead` rename with **no shadow leftover**, bootstrap's size declaration, and
the hooks failing open while honoring `AGORA_*`. Fixed:

1. **`_env_float` bypassed the seam** (the should-fix) — it read `os.environ.get(name)`
   directly, so `AGORA_AMEND_SUPERMAJORITY` was silently ignored (only legacy worked). I
   missed it because its arg is a *parameter*, not a `"GROUPCHAT_"` literal my grep
   caught. Routed through `_env`; regression-tested both spellings.
2. **`stop.py` module-level parse could crash on a junk env** (fail-open concern,
   pre-existing) — `int(AGORA_PARK_WINDOW=abc)` would raise at import and kill the Stop
   hook. Wrapped in a fail-open `_envnum` (verified: junk env → exit 0).
3. **`doctor.py` isolated its probe room via `GROUPCHAT_DIR` only** — an ambient
   `AGORA_DIR` (new-wins) would shadow it and aim the probe at the operator's real room.
   Both probe sites now set both spellings.
4. **Docstring + 23 command-file descriptions** still said "group chat" — swept to agora.

Nit accepted as a run-note (no code change): the test suite isolates via the legacy
`GROUPCHAT_DIR`, so it must **not** be run with an ambient `AGORA_DIR` exported (which
would new-wins-shadow every per-test room). With no ambient room env, the suite is
**32/32**.
