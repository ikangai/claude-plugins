---
description: Report a structured result back to the orchestrator (and collect results)
argument-hint: ["what you produced" | --task N | (empty to collect)]
---
A **result** is how a worker hands its outcome back to whoever is coordinating — a
`kind='result'` message that rides the bus but carries no @mention, so it never blocks
a teammate or wedges the barrier. Use the group-chat CLI path from your SessionStart
briefing as `<cli>` and your handle as `<you>`.

- **Report a result** (non-empty `$ARGUMENTS`) → `<cli> result --from <you> "<what you
  finished / produced / found>"`. Add `--task N` if this completes a task you were
  working — it closes task N and tags the result with its id.
- **Collect results** (empty `$ARGUMENTS`, i.e. you are the orchestrator) → `<cli>
  results` and show them verbatim; `--from <handle>` narrows to one agent.

Report a result when you finish your slice — concise but concrete (what changed, where,
any follow-ups). The orchestrator reads `results` (or `summary`) instead of
re-scrolling the whole chat.
