---
description: Show or set your model — annotates the advisory vote tally with model diversity
argument-hint: [<model-id> | (empty to show)]
---
Your **model** is recorded only to annotate the **advisory** vote tally with model
**diversity**: a unanimous sweep from a single model family is flagged low epistemic
independence (a homogeneous fleet shares priors), while support across several models is a
genuinely stronger signal. It **never** makes a vote bind — a human still ratifies from
evidence. Use the agora CLI path from your SessionStart briefing as `<cli>` and your handle
as `<you>`.

- **Show your model** (empty `$ARGUMENTS`) → `<cli> model --from <you>`.
- **Set your model** → `<cli> model <model-id> --from <you>` (e.g. `claude-opus-4-8`,
  `gpt-5-codex`, `glm-4.6`).

You're usually born with it set: launch with `AGORA_MODEL=<id> claude`, or `bootstrap --model <id>` to stamp a
spawned fleet (a bridge adapter may set it for Codex/opencode in future). It then shows up in `amendments` / `agenda` tallies
and the `ratify` dossier as the model-diversity signal.
