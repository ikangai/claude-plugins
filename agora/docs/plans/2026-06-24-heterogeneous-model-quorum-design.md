# Heterogeneous-model quorum — making the capture wall visible

*2026-06-24. Vision item #3a — the safe frontier of "stronger governance". Builds with
code; the transport half (#3b) is a design map, not a refactor.*

## The problem it addresses (the capture wall)

The founding governance threat: a homogeneous LLM fleet's vote is **"one opinion counted
N times."** Shared training priors mean a 7–0 sweep among identical models carries little
more independent evidence than a single vote — and the fleet can be nudged in lockstep by
one prompt. This is *why* a vote can never bind (a human ratifies from verifiable
evidence). But the flat tally (`yea 7 / nay 0`) *hides* the risk: it looks like a strong
mandate.

## What it does

Record each agent's **model** and annotate the **advisory** tally with model
**diversity**, so the capture risk is *visible* to the human ratifier:

- **`agents.model`** (NULL = unknown). Set via `$AGORA_MODEL` at launch, the `model`
  verb at runtime, or `bootstrap --model` (a bridge adapter may set it in future).
- **`motion_tally`** now also returns `models` (distinct known models among the casting
  voters) and **`single_model`** — True when 2+ voters all share one known model (a
  homogeneous-fleet sweep).
- **`_diversity_note`** annotates the surfaces: `agenda` / `amendments` show
  `· N models (cross-model support)` or `· ⚠ single-model vote — low epistemic
  independence (homogeneous fleet)`. The **`ratify` dossier** — the human's decision tool
  — spells it out: *"a homogeneous fleet shares priors; treat unanimity as one opinion,
  not a quorum."*

## Why this is the *safe* frontier (it can't be the captured one)

It **never changes whether anything binds.** A perfectly diverse unanimous vote is still
only advisory; a single-model sweep still can't enact anything. The only thing that
changes is *information*: the human sees the diversity. So it strictly *strengthens* the
human-ratifies-from-evidence model (C1) instead of eroding it — the opposite of binding
auto-apply, which the design study deferred *on principle*. Unknown (NULL) models are
excluded from the diversity count, so the signal never over-claims.

Dormant until used: a room that never votes, or never sets a model, renders identically
(the note is empty below 2 cast votes).

## Method

TDD: `tests/model_quorum_test.py` (12 checks). The discriminating pair: a 3–0 sweep from
one model is **flagged** `single_model`; support across 3 distinct models is **counted**
and *not* flagged. Plus the safety check: the surface stays framed **ADVISORY** regardless
of diversity. Full suite 33 modules (`doctor.py` EXPECTED updated for `model`).

## A stated semantic: model is read at *tally* time, not frozen at vote time

`motion_tally` joins each casting voter's session to its **current** `agents.model`. So an
agent that votes as `opus` then `model`-switches to `codex` shifts the diversity note
(verified in the smoke). This is a deliberate, simple choice for the common case (a fleet
votes, the human checks diversity shortly after). The stricter alternative — freezing
`voter_model` in the `votes` row at cast time — is the honest upgrade if the signal is
ever used for after-the-fact audit; left as a follow-on since it adds a column for an
edge case (an agent rarely changes model mid-deliberation), and the annotation is advisory
either way.

## Adversarial review (fresh eyes) — outcome

A 4-lens / 17-agent review (safety·binding, tally-correctness, dormancy·schema,
integration·bridge), each finding independently verified: **13 findings, 12 confirmed**
(1 should-fix class, mostly nits). The headline is a **verified-safe confirmation**: the
safety invariant holds — model diversity is *purely advisory annotation*. `ratify
--confirm` (the only status-changing path) computes the binding action from the motion row
+ constitution text and **doesn't call `motion_tally` at all**; `cmd_amendments`'s bar is
`yea/nay/voters` only; no hook/barrier/`who` reads `model`. Diversity can't flip anything.
Fixed:

1. **Inert by default** (the should-fix) — nothing auto-populated `model`, yet the headline
   capture case is a same-host `bootstrap`. Now `bootstrap --model <id>` (defaulting to the
   bootstrapper's `$AGORA_MODEL`) stamps the spawned fleet, so a homogeneous bootstrapped
   team self-declares and actually trips the flag. Regression-tested.
2. **Ratify dossier noise** — printed `0 distinct model(s)` when all voters were
   unknown-model; now stays quiet (matches `_diversity_note` dormancy).
3. **`model <junk>` silently cleared** — now refused (matches `cmd_squad`), prior model
   untouched. Regression-tested.
4. **Stale "bridge adapters set it" doc claims** — softened to "may set it in future"
   (no bridge wires it yet; `bootstrap --model` is the real auto-path).

Accepted as conservative-by-design (no code change): the single-model flag is **silenced
if any voter is unknown-model** — the safe direction (never a false flag); and the reading
is current-at-tally-time (documented above). Full suite 33/33.

## Deferred

Auto-detecting a Claude session's model from the hook payload (best-effort; today it's
`$AGORA_MODEL` / the `model` verb / the bridge). Weighting the *advisory* bar by diversity
(currently diversity is shown, not folded into the `flag`) — deliberately left as raw
information for the human, not a derived score.
