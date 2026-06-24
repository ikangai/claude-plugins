---
description: Reconcile bootstrap --worktree branches — read-only ahead/behind + overlaps (never merges)
argument-hint: [--base <ref>]
---
When teammates were spawned with `bootstrap --worktree`, each lands on its own
`groupchat/<name>` branch. This reconciles them for a merge — **read-only and
diff-only: it never merges anything.** Use the agora CLI path from your
SessionStart briefing as `<cli>`.

Run `<cli> worktrees` (alias `harvest`; pass `--base <ref>` to diff against something
other than the current branch) and show me the report verbatim. It lists, per
`groupchat/<name>` branch: commits ahead/behind the base, the changed files, **file
overlaps** between branches (the merge-carefully signal), and a suggested merge order
(smallest blast radius first).

Then summarize for me: which branches are safe to merge in any order (disjoint files)
and which overlap. Propose the `git merge …` sequence — but **I run the merges**; do
not merge on my behalf.
