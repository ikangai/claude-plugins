---
description: Work-division ledger — add / list / claim / done, or assign a teammate a task
argument-hint: [list | add "title" | claim <id> | done <id> | assign <handle> "title"]
---
The shared task ledger turns the chat into a coordinator: open vs claimed vs done
work that any agent can see, with an atomic claim so two agents can't grab the same
slice. Use the group-chat CLI path from your SessionStart briefing as `<cli>`, and
your own handle (also in the briefing) as `<you>`.

Pick the action from `$ARGUMENTS` (default to `list` when empty):

- **list** → `<cli> task list` (add `--all` to include done tasks). Show the output
  verbatim.
- **add** → `<cli> task add "<title>" [--paths "<glob>"] --from <you>` — append an
  open task others can claim. `--paths` is an optional hint at the files it touches.
- **claim** → `<cli> task claim <id> --from <you>` — take an open task. The claim is
  atomic: if a teammate beat you to it, you're told who holds it — coordinate in chat
  rather than double-working it.
- **done** → `<cli> task done <id> --from <you>` — mark a task complete.
- **assign** → `<cli> assign <handle> "<title>" [--paths "<glob>"] --from <you>` —
  hand a *specific* teammate a task. It creates the task already owned by them **and**
  @mentions them, so it's both durable (survives the chat scroll) and delivered (rides
  their cursor). Works even before they've joined.

After acting, briefly confirm what changed (e.g. "claimed #2; @bob still owns #3").
