# Vendored distribution copy

This `employee/` directory is a **distribution copy** so teammates can install the
plugin one-click from this public marketplace (Claude Code *and* Claude Desktop)
without access to the private product repo.

- **Canonical source + development:** `ikangai/CompassAI` → `plugins/employee/`
  (v0.1.0). The structure, shared-skill parity and CLI-drift tests for all role
  plugins (pmo, pm, employee) live in `plugins/tests/` there — they import
  `cli/lib/manifest`, so they are deliberately NOT vendored here.
- **Re-vendor on each plugin release** (keep this copy in sync):
  ```
  # from a CompassAI checkout, main up to date:
  mkdir -p /tmp/rp && git archive origin/main plugins/employee | tar -x -C /tmp/rp
  rsync -a --delete --exclude tests --exclude VENDORED.md /tmp/rp/plugins/employee/ <this-repo>/employee/
  ```
  The `signals-operating` skill is shared: pmo holds the source, pm and
  employee carry byte-identical copies — re-vendor all three together.

At runtime the plugin needs **nothing** from CompassAI — `plugin.json` declares a
remote MCP connector (`https://signals.ikangai.com/mcp`) and the host runs the
OAuth flow on first use. The skills drive `signals_call`/`signals_describe`/`signals_api`.
