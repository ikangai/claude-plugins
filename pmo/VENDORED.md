# Vendored distribution copy

This `pmo/` directory is a **distribution copy** so teammates can install the
plugin one-click from this public marketplace (Claude Code *and* Claude Desktop)
without access to the private product repo.

- **Canonical source + development:** `ikangai/CompassAI` → `plugins/pmo/`
  (v0.3.0). That's where the plugin is developed, with its
  `node --test plugins/pmo/tests/*.test.mjs` structure + CLI-drift guards — those
  import `cli/lib/manifest`, so the `tests/` dir is deliberately NOT vendored here.
- **Re-vendor on each plugin release** (keep this copy in sync):
  ```
  # from a CompassAI checkout, main up to date:
  git archive origin/main plugins/pmo | tar -x -C /tmp/pmo
  rsync -a --delete --exclude tests --exclude VENDORED.md /tmp/pmo/plugins/pmo/ <this-repo>/pmo/
  # then bump the plugin version in pmo/.claude-plugin/plugin.json if it changed
  ```

At runtime the plugin needs **nothing** from CompassAI — `plugin.json` declares a
remote MCP connector (`https://signals.ikangai.com/mcp`) and the host runs the
OAuth flow on first use. The skills drive `signals_call`/`signals_describe`/`signals_api`
(renamed from `compass_*` in 0.3.0 — re-grant tool permissions after upgrading).
