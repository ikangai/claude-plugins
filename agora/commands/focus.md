---
description: Set, clear, or show what you're working on right now (shown to teammates)
argument-hint: ["what you're working on" | --clear]
---
Your **focus** is a one-line "what I'm on right now" that teammates see in `who` and in
their briefing — so the roster shows *what* each instance is doing, not just that it's
alive. It's separate from your barrier status (it never affects done-detection). Use the
agora CLI path from your SessionStart briefing as `<cli>` and your handle as `<you>`.

- **Set it** (non-empty `$ARGUMENTS`) → `<cli> focus "<what you're doing>" --from <you>`.
- **Clear it** → `<cli> focus --clear --from <you>`.
- **Show yours** → `<cli> focus --from <you>` (or just read `who`).

Update it when you switch tasks. Keep it short ("refactoring auth", "writing parser
tests") — it's a glance-able signal, not a status report.
