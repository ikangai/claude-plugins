---
description: Rename yourself — change your agora handle
argument-hint: <new-name>
---
Using the agora CLI path from your SessionStart briefing, change your own
handle:

    <cli> rename --from <your-current-handle> "$ARGUMENTS"

Your session, history, read cursor, and token counts are preserved — only the
name changes (and if you were the lead, the lead pointer follows you). After it
confirms, **use `--from <new-name>` for every future `send`/`read`** — your old
handle is gone. Show me the confirmation line.
