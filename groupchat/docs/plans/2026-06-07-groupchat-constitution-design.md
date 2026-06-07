# Design: a governance layer — a tracked constitution the team amends from evidence

**Date:** 2026-06-07
**Status:** design (rev. 2 — post adversarial review), ready to turn into a task-by-task plan
**Topic:** add a *coordination constitution* to the group-chat plugin — a tracked,
human-ratified rulebook that the agent team can propose amendments to from
evidence in the work, with a periodic repeal-first review. Reuses the existing
SQLite bus, hooks, and tunable conventions. Additive scaffolding around the
tested `.groupchat/` tree, exactly like the plugin packaging was.

> **For the implementing instance:** this is a *design* doc. Produce a
> task-by-task implementation plan from it (see
> `2026-06-02-groupchat-plugin.md` for the format: numbered tasks, each with a
> failing check → implementation → passing check, `GROUPCHAT_DIR=/tmp/...`
> isolation, and the commit trailer
> `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`).
> Build the phases in order; **Phase 1 must ship and earn its keep before any
> voting exists.** The product is **P1 + P2**; the parliament (P3) is
> **advisory-only**, and any *binding* auto-gate is deferred to P4.

> **What changed in rev. 2 (from the review):** the vote tally is now explicitly
> **advisory**, not a gate — the binding decision is a human reading verifiable
> evidence (§4, §6). The constitution file resolves at the **same git anchor as
> the bus** (§Approach, §Open-notes). `ratify` now **announces changes on the bus**
> so live agents learn (§4). The honest **relationship to the original goal** is
> stated up front (§Why). Plus concrete fixes to `RULE_RE`, cite harvesting,
> rule-id lifecycle, the electorate definition, motion lifecycle, CLAUDE.md
> migration, and several YAGNI trims — all flagged inline as **[rev2]**.

---

## Why

Today the team coordinates through the chat bus (the informal layer) and, in
practice, through a `CLAUDE.md` that a human wrote once (static law handed down).
What's missing is the loop in between: a place where a rule that *keeps earning
its keep in the chat* gets promoted to durable law, a rule that's gone dead gets
repealed, and the agents who live under the rules can propose changes — from
evidence, not rhetoric — without anyone hand-editing a file every time.

This is deliberately the *common-law* shape: the chat log is the proceedings, the
`.dev-diary/` is the case reporter, `CONSTITUTION.md` is the codified statute, and
the review is *stare decisis* plus repeal. The novelty (vs. AGENTS.md / CLAUDE.md
/ soul.md, all read-only-to-the-agent) is that the governed amend their own
governing document, and a human ratifies.

### Relationship to the original goal — read this first **[rev2]**

The founding pain behind this whole line of work is a *different* problem: a single
human running N parallel agents is exhausted by **live, mid-task clarification
interruptions** and by **juggling N contexts**, and wants to be a **single point of
contact**. **This governance layer does not solve that.** Governing the team's
*standing coordination conventions* is an offline/batch axis; intercepting an
agent's ad-hoc "which auth provider?" mid-task is a real-time *routing* axis. A
ratified constitution does not catch a single uncovered ambiguity, and it does not
give the human one inbox — in fact it *adds* a human chore (ratifying amendments,
running the repeal review).

So position this honestly:

- The on-target fix for the founding pain is a **human-contact funnel** (the
  hub-and-spoke "a lead aggregates clarifications" direction), hardened against its
  single-point-of-failure. That is separate work and is **not** delivered here.
- The constitution's payoff is **coordination quality over time** and **rulebook
  anti-rot**: well-cited Articles (e.g. "announce before you touch a file",
  "converge, don't fork") cut *collisions and duplicated/forked work*, which is a
  *minority but real* slice of what drives confusion-class interruptions. Treat
  that as a modest, indirect, delayed second-order benefit — not the headline.

Build this because durable, agent-amendable coordination law is valuable on its own
merits, not because it empties the human's inbox.

## Non-goals / what this does **not** do

- It does **not** reduce live clarification interruptions or provide a single point
  of contact. That is the funnel's job, not this. **[rev2]**
- It does **not** let agents change how they *write* code in parallel. Writes stay
  single-threaded by convention; the constitution governs *coordination*, not a
  license to fan out edits. (Single-threaded-writes is in the **entrenched core**
  below, where the parliament can't touch it.)
- It does **not** make amendment frictionless. Stability is the value. Amending is
  a deliberate act (motion → vote → **human ratify**), not an autosave.
- It does **not** let the vote count *decide* anything. The tally is **advisory**;
  the human ratifying from evidence is the only thing that changes the law. **[rev2]**
- It does **not** treat the diary as proof (see *The diary* below). The diary
  generates hypotheses; amendments are grounded in verifiable signals.
- It does **not** auto-apply anything to the constitution file in P1–P3. A human
  commits every change to the law. (Bounded auto-apply is a flagged P4 frontier.)

---

## Approach: wrap, don't rewrite

Same mantra as the plugin work. Everything here is additive:

- **The law is a tracked file**, `CONSTITUTION.md`, committed in the *target*
  repo — version-controlled and diffable, like AGENTS.md. (Contrast: `chat.db` is
  runtime and gitignored. Durable law is source; legislative *process state* —
  cites, motions, votes — is runtime, in `chat.db`.)
- **The constitution file resolves at the SAME anchor as the bus. [rev2]** Add a
  `repo_root()` helper that reuses `store_dir()`'s git logic — the **git
  common-dir parent** (the main-worktree root) — *minus* the `.groupchat` join, and
  put `CONSTITUTION.md` there. Do **not** use `git rev-parse --show-toplevel`
  (that's the *current* worktree's root, which differs per worktree). Because the
  file is committed, same-branch worktrees would read identical content anyway, but
  the **write path** (`ratify`) and out-of-sync branches make a single shared anchor
  the correct, unambiguous choice — and it keeps cites (in the one shared `chat.db`)
  tallying against one law no matter which worktree runs `review`. `repo_root()` must
  mirror `store_dir()`'s *full* fallback chain (chat.py:70-102) minus the trailing
  `.groupchat` join — git-common-dir parent, then `$CLAUDE_PROJECT_DIR`, then cwd — so
  `CONSTITUTION.md` always lands as a sibling of `.groupchat`; assert
  `repo_root() == os.path.dirname(store_dir())` in the git case as a test.
- **The legislative process rides the existing bus.** Motions and votes are just
  `messages` rows with a new `kind`. No new transport, no second cursor, no MCP.
- **Rule citations are harvested from normal chat** the way `@mentions` already
  are — a regex over message bodies. Agents get **no new ritual** to remember;
  "stopping with an empty inbox == done" was the taste, and this matches it.
- **A human authors the core and ratifies every amendment.** The tooling
  *proposes* a diff and *surfaces the evidence*; the human reads the evidence and
  commits. The vote is one weak input to that judgment, never the trigger. **[rev2]**

`CONSTITUTION.md` is **deliberately separate** from `AGENTS.md`/`CLAUDE.md`. Those
stay static, human-authored instructions. Keeping the constitution its own file
gives the amendment machinery a **bounded target** (it can only ever touch the
amendable zone of one file) and keeps the static instructions static. **[rev2]**
But coordination conventions must live in *exactly one place* — see
*CLAUDE.md migration* below, so the two files don't drift.

---

## Verified facts from the existing code (do not break these)

*(All confirmed against `.groupchat/chat.py` during review — line numbers are current.)*

- **Hooks fail open.** Every hook body is wrapped `try/except … sys.exit(0)`.
  `user_prompt_submit.py` exiting non-zero would *block the user's prompt*. Any
  governance touch to a hook keeps this. **The loud-failure constitution parser is
  CLI-only; hooks use a best-effort read path that swallows exceptions and emits
  nothing on error.** **[rev2]**
- **One monotonic cursor.** `agents.last_read_id` is the entire delivery model;
  there is no read-receipt table. Motions/votes/cites are `messages` and are
  delivered by this same cursor. **Do not add a second cursor.** A change to the law
  must therefore *also* be a `messages` row or live agents never see it (see
  `ratify`, §4). **[rev2]**
- **`messages.kind`** is an open `TEXT` column, `DEFAULT 'chat'` (chat.py:145), with
  no enumeration/CHECK — values are free-form (e.g. `stop.py` already posts
  `kind='system'`). New kinds (`'motion'`, `'vote'`) slot in with **no schema
  change**. `send(... kind=...)` already takes the parameter (chat.py:462).
- **`parse_mentions()` + `MENTION_RE`** (chat.py:47, 192) show the pattern for
  harvesting tokens from a body at send time. `MENTION_RE` uses a negative
  lookbehind `(?<![\w/])` to avoid mid-word/path matches — `RULE_RE` mirrors it.
- **`meta` k/v** (`get_meta`/`set_meta`/`del_meta`, chat.py:271/276/285) is the home
  for small room-wide state (e.g. the rule-id high-water mark); **guarded
  `ALTER TABLE`** (`_add_column_if_missing`, chat.py:131) and
  `CREATE TABLE IF NOT EXISTS` in `_ensure_schema` (chat.py:137) are how schema grows
  without breaking old dbs. Follow both.
- **`send --from` needs no registration and does NOT authenticate the handle** (it
  just stamps the `sender` string). `read`/`inbox` accept *either* `--session` *or* an
  already-registered `--from` handle (chat.py:369-376) — but a bare/unregistered
  `--from` is unauthenticated. **This is the crux of the vote-integrity story (§6): a
  self-asserted `--from` handle means a tally over `--from` votes is forgeable.** Votes
  must therefore be **stricter than `read`/`inbox`: resolve identity ONLY from
  `--session`** (the hook-known, registered identity) and ignore any `--from`-only
  vote, so the tally is attributable to real registered sessions. **[rev2]**
- **`store_dir()` resolution** (chat.py:68) — `$GROUPCHAT_DIR` → **git common-dir
  parent** → `$CLAUDE_PROJECT_DIR` → cwd. `CONSTITUTION.md` resolves at the *same*
  common-dir parent (via the new `repo_root()` helper), **not** `--show-toplevel`. **[rev2]**
- **Tunables are env vars**, seconds where relevant, with sane defaults (see the
  barrier knobs). New thresholds follow the `GROUPCHAT_*` naming.
- **Distribution:** code ships in the plugin (`${CLAUDE_PLUGIN_ROOT}`); the
  `chat.py install` copy-in path stays valid (dual distribution). New CLI lives in
  `chat.py`; new `/groupchat:*` commands are thin wrappers that reuse the absolute
  `chat.py` path from the SessionStart briefing (the `${CLAUDE_PLUGIN_ROOT}`
  no-expand-in-command-markdown bug #9354 still applies).
- **Dogfooding:** the dev repo wires hooks via its own `.claude/settings.json` and
  must **not** also install the plugin (double-fire).
- **Tests:** no framework — exercise the CLI and pipe hook JSON on stdin with
  `GROUPCHAT_DIR` set to a throwaway path.

---

## The model

### 1. `CONSTITUTION.md` — the document

A tracked Markdown file at the repo root (resolved via `repo_root()`, §Approach),
with two zones delimited by HTML-comment markers so a parser can split them
deterministically:

```markdown
# Repo Constitution

<!-- CONSTITUTION:CORE:BEGIN -->
## Core (entrenched — amendable only by a human, never by the parliament)

### C1 — The human is the final authority
No automated process may modify this Core section or apply an amendment to the
Articles without a human committing it.

### C2 — Hooks fail open
A coordination hook must never crash or block a session on error.

### C3 — Writes are single-threaded
Agents add intelligence, not concurrent edits. One writer per change.

### C4 — The amendment procedure
Articles change only by: a motion citing evidence → an advisory vote → a human
ratifying the proposed diff after reading the cited evidence. Core changes are out
of scope for this procedure.
<!-- meta: zone=core -->
<!-- CONSTITUTION:CORE:END -->

<!-- CONSTITUTION:ARTICLES:BEGIN -->
## Articles (amendable by the parliament, ratified by a human)

### R1 — Announce before you touch a file
Post "starting on <path>" before editing, so two agents don't collide.
<!-- meta: id=R1 added=2026-05-30 by=human ratified=2026-05-30 amended= source= -->

### R2 — Converge, don't fork
If two agents propose overlapping designs, one retracts. Do not merge into an
average; pick one and make it the contract.
<!-- meta: id=R2 added=2026-05-30 by=human ratified=2026-05-30 amended= source= -->
<!-- CONSTITUTION:ARTICLES:END -->
```

- **Core** items are `C<n>`; **Articles** are `R<n>`. The `R<n>` id is the stable
  citation token and the unit the review measures and the parliament amends. CORE is
  defined by one property: **only a human may change it**; that is *why* C3 (a pure
  convention) belongs there alongside C1/C2/C4 — entrenchment, not enforceability,
  is the membership test.
- **Rule ids are monotonic and never reused. [rev2]** Allocate the next `R<n>` from a
  high-water mark stored in `meta` (e.g. `const_next_rule_id`); a repealed id is
  retired forever (optionally left as a `repealed=<date>` tombstone Article).
  **Repeal never renumbers survivors** — renumbering would break every prior cite and
  every `source=`. `constitution check` enforces no-reuse.
- Each Article carries a machine-readable **provenance** comment: `id`, `added`,
  `by` (who proposed), `ratified`, `amended` (date if ever changed), `source`
  (the motion id that produced it). **Provenance is human-attested metadata, not a
  verified link to a passing motion — the git commit is the real provenance.** `check`
  validates field *presence/format*, never asserts `source=` corresponds to a real
  tally. **[rev2]** `cites` is **not** stored here — it's recomputed from the bus at
  review time (a cache, not a source of truth, so it can't be gamed by editing the
  file).
- **Parsing rule:** if either zone's markers are missing or malformed, the
  `constitution` **CLI** refuses to operate (loud failure on the *durable* file). The
  **hook** read path is the exception — it best-effort parses and stays silent on
  error (C2 fail-open). A corrupt constitution is a human-fix situation for the CLI,
  never a session-breaker for the hook. **[rev2]**

**`chat.py constitution` subcommands (Phase 1):**

- `constitution init` — **human-run**; writes a starter `CONSTITUTION.md` with the
  Core above, at the repo root. **Seeds the Articles zone from the existing CLAUDE.md
  coordination conventions** (so there is one source of truth, not two — see
  *CLAUDE.md migration*). Refuses to overwrite an existing file. **[rev2]**
- `constitution` / `constitution show` — parse and print: Core items, Articles
  with provenance, and any structural problems.
- `constitution check` — validate markers + per-Article provenance + **rule-id
  monotonicity/no-reuse**; non-zero exit on a malformed file (so it can gate CI
  later, whenever CI exists).

### 2. Citation harvesting — the measurement layer (Phase 2)

- Add `RULE_RE` next to `MENTION_RE`. **[rev2]** Mirror the boundary handling exactly:
  `(?<![\w/])R(\d+)\b`, **case-sensitive `R`** (real cites are `R2`, not `r2`), and
  reject the R-squared family (a trailing `=`, `^`, or `²`/`-squared`). It stays a
  tolerant *signal*, not a ledger — but these guards keep the most common noise
  (`R2=0.99`, `r2`, code-span `grep R2`, an agent quoting the constitution) from
  driving the repeal decision. Skip cite-harvest on any message that quotes the
  constitution (e.g. body contains the `CONSTITUTION:` markers).
- Extend `send()` to harvest rule-ids the way it harvests mentions — **but only for
  `kind in ('chat','system')`.** **[rev2]** A rule named inside a `motion`/`vote`
  body (e.g. "motion to repeal R2") must **not** count as a cite, or the act of
  debating a rule's repeal would inflate its importance and shield it.
- New runtime table (guarded `CREATE TABLE IF NOT EXISTS` in `_ensure_schema`):

  ```sql
  CREATE TABLE IF NOT EXISTS rule_cites (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      ts         TEXT    NOT NULL,
      rule_id    TEXT    NOT NULL,
      sender     TEXT    NOT NULL,
      message_id INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_rule_cites_rule ON rule_cites(rule_id);
  ```

  Append-only, additive, touches none of the existing invariants. The index mirrors
  `idx_messages_id` (chat.py:163) so the windowed group-by stays cheap. **[rev2]**
- A cite is recorded automatically whenever a normal (chat) message references a
  rule. No `chat.py cite` ritual; an agent writing "per R2 I'll retract my design"
  *is* the cite. This is the empirical fact the review runs on: in the
  converge-not-fork run, the rules that mattered were the ones that got *cited*
  repeatedly.
- **The importance score counts distinct *senders*, not raw cite rows, and discounts
  self-cites** (sender == the Article's `by`). **[rev2]** One agent spamming "per R5"
  must not manufacture importance. (Note: cites are still self-asserted via `sender`;
  this is acceptable because the review is *advisory* and the strong check is human —
  see §6.)

### 3. The review — repeal-first (Phase 2)

`chat.py review` produces an advisory **report** (it changes nothing). It reads
`CONSTITUTION.md`, recomputes distinct-sender cite-counts from `rule_cites` over a
window, **reconciles cite rule-ids against the live Articles set [rev2]**, and emits,
in priority order:

1. **Repeal candidates** — Articles with zero/low distinct-sender cites over the
   window. *This is the primary output.* Rulebooks rot by accretion; the review's
   main job is pruning. A never-cited rule is a dead letter; a rarely-cited one is a
   candidate. (Cite-count is a cheap **Rule-Importance-Score**; the stronger, manual
   check is to A/B a rule against whatever conformance/tests the repo has and see if
   removing it changes anything.)
2. **Drift flags** — Articles that reference paths/symbols no longer present in the
   repo (cheap `grep` heuristic). Rules that contradict the current code.
3. **Unknown / repealed cite-ids** — cites whose `rule_id` is not a live Article
   (RULE_RE noise, or a repealed rule's lingering cites) — reported *separately*, never
   as live-rule importance. **[rev2]**
4. **Promotion candidates** — recurring pains in the diary/log not yet covered by an
   Article → suggested motions. Each is tagged **`HYPOTHESIS (diary, unverified)`**
   and requires at least one corroborating bus cite or test before it can become a
   motion. (Hypotheses only; see *The diary*.) **[rev2]**

Cadence: on demand first. Scheduling (e.g. a dedicated `/goal` instance that runs
the review) is a later concern, not a P2 dependency.

> **P2 is the product's "does-it-help?" engine, but it measures *rule usage*, not the
> founding goal's *interruption* metric.** Don't claim otherwise. The repeal report
> (output 1) is the fully-specified, genuinely valuable core; drift-grep (2) and
> diary-promotion (4) are heuristic and may be split into a P2.5 with their own
> acceptance criteria. **[rev2]**

### 4. The parliament — motion → vote → ratify (Phase 3, **advisory**)

Rides the bus via new `messages.kind` values; the tally is computed from the log and
is **advisory input to a human, never a gate**. **[rev2]**

- `chat.py motion --from <h> --rule R2 --change "<new text>" --because "<evidence>"`
  posts a `kind='motion'` message; the motion id is `M<message_id>` (collision-free —
  `messages.id` is AUTOINCREMENT). To repeal: `--repeal R2`. To add:
  `--rule new --change "..."` (the new id is allocated from the `meta` high-water mark).
  - **The motion records the base text/version of the rule it targets.** **[rev2]**
  - **Motion stays on `--from`** (no registration needed to post one — see *Verified
    facts*); the proposer never enters the tally, so authenticating it buys nothing the
    human-ratify gate doesn't already cover. Only the *vote* tally needs `--session`. **[rev2]**
  - **Evidence is required:** `--because` must be non-empty and should reference
    message ids / diary entries / a test case. The code can only enforce *presence*;
    the *quality* check is the human ratifier reading the cited evidence. The principle
    is "convict on the verifiable record," so the motion must point at the log/tests,
    not just assert.
  - **Entrenched-core protection:** a motion targeting a `C<n>` id (or the Core zone)
    is **rejected** by `chat.py motion` and again at ratify time.
- `chat.py vote --session <sid> M12 yea|nay` posts `kind='vote'` referencing the
  motion. **Votes resolve identity from `--session` (a registered agent), not a bare
  `--from` handle** — an unauthenticated `--from` vote does not count toward the tally.
  One registered session = one vote; **last vote per session wins.** **[rev2]**
- `chat.py amendments` lists motions and their live, **advisory** tally: distinct
  *registered* voters (yea/nay), the rule's distinct-sender cite-count, any drift
  flags, and the motion's status. It does **not** render a green "passes" verdict —
  it renders the raw provenance a human needs to judge. **[rev2]**
- **Motion lifecycle [rev2]:** `open → closed → (ratified | failed | withdrawn | expired)`.
  A motion is `open` until `ratify` (or an explicit `close`) freezes it; votes after
  close don't count. State this so a stale motion can't be ratified and two motions
  can't apply conflicting diffs to the same rule. Concurrent motions on the same
  `R<id>` are allowed, but opening a newer motion on an `R<id>` marks prior `open`
  motions on that id as **`superseded`** (a terminal state, so stale siblings aren't
  votable or surfaced), and `ratify` additionally refuses any motion whose recorded
  **base text no longer matches** the live Article (TOCTOU backstop — require a
  re-motion against current text).
  Because motions don't `@mention`, they don't block Stop or gate the barrier (good —
  no nagging); the flip side is that an `open` motion the cohort never votes on simply
  **expires** when the team tears down (or persists for the next cohort, surfaced by
  the P3 SessionStart line). Pick *expire-on-teardown* as the default and document it.
- **Thresholds — advisory framing (tunable, `GROUPCHAT_*`):** these set *when the
  report flags a motion as "has enough support to be worth a human's ratify look,"*
  not when it passes.

  | Knob | Default | Note |
  |------|---------|------|
  | `GROUPCHAT_AMEND_SUPERMAJORITY` | `0.66` | advisory: `yea / (yea + nay)` over distinct **registered** voters after last-vote-per-session collapse |
  | `GROUPCHAT_AMEND_QUORUM` | `3` | advisory: min distinct **registered, currently-active** voters; clamp to the active cohort — **never** derive from the self-declared `GROUPCHAT_TEAM_SIZE` |
  | `GROUPCHAT_AMEND_COOLDOWN` | `0` | a **stability/UX** knob (min seconds open before the report flags it), *not* a capture defense |

- **`chat.py ratify M12` is the human's tool** (document it as human-run; agents are
  told in the skill not to call it). It **freezes** the motion (`closed`), re-checks
  only the **entrenched-core protection** and the **base-text TOCTOU guard**, then
  **surfaces the evidence dossier and emits a unified diff** to the Articles zone
  (updated rule text + provenance bump: `amended=<date> by=parliament source=M12`,
  `ratified=<date>`, plus the frozen vote message-ids). **[rev2]**
  - **It does NOT print a green "quorum/supermajority met."** It surfaces the raw
    provenance — the distinct voter session_ids and whether each is a live registered
    agent, the rule's cite-count, drift flags, and the cited `--because` evidence —
    and asks the human to read *that*. The vote is one weak input; the binding act is
    the human reading evidence and committing. **[rev2]**
  - **`ratify` prints a diff to stdout; the human commits it. There is no
    `PROPOSED_AMENDMENTS.md` and no `--apply`** in P3 — an in-place file edit would be
    an automated process modifying the law, which C1 forbids; diff-only gets the same
    result with zero risk. (In-place apply is a P4 frontier, behind an explicit opt-in.) **[rev2]**
  - **`ratify` also posts a `kind='system'` message** ("R2 amended (M12): <new text>")
    so every live agent learns of the change on its next turn via the existing cursor —
    the same mechanism the barrier uses for its "left the barrier" notice. The file
    edit alone is invisible to a mid-session agent. **[rev2]**

### 5. The diary — evidence base, held at arm's length

`.dev-diary/` already exists (`.dev-diary/.events.jsonl` is gitignored; prose
entries tracked). Promote it from "interesting artifact" to the review's
**legislative record**: the place near-misses and lessons accumulate between
reviews.

**Who writes the signal, and how `review` reads it [rev2]:** the diary is produced by
the separate `diary` skill, whose entries are first-person *prose*, not structured
lines — so do **not** invent a new `LESSON:` ritual for agents to remember (that
violates the "no new ritual" taste). Instead, pick one owner:

- **Preferred:** extend the `diary` skill to emit a machine-scannable
  `LESSON: … [evidence: #142,#147]` line at synthesis time (the skill already writes
  the entry; it adds the line), **or**
- **Fallback:** have `review` grep prose entries for the `[evidence: #id]` tokens it
  already understands, with no convention change.

**The caveat, stated so the implementer wires it correctly:** the diary is
*self-reported* and therefore biased, and the moment it feeds the constitution it
becomes a **lobbying instrument** — an agent can write self-justifying entries to
steer future amendments. So:

- The diary is a **hypothesis generator** for the review (`review` surfaces diary
  lessons as *promotion candidates*), **never** proof.
- Every amendment is grounded in **verifiable** signals — git history,
  test/conformance results, the actual motions and cites in the bus. *Read the
  memoir for leads; convict on the evidence.*
- The review must not auto-promote from the diary. A diary lesson becomes only a
  *suggested motion* tagged `HYPOTHESIS (diary, unverified)`, which still needs ≥1
  corroborating bus cite/test, a vote, and a human ratify.

### 6. Capture defenses (the security view)

A homogeneous Opus fleet is *more* capturable, not less — and this is not a
hunch. The project's own verified research (3-0 in adversarial verification)
establishes the threat directly:

- **LLM agents are not epistemically independent** — they share priors/biases
  (arXiv 2602.09341), so a supermajority is the *expected* output of correlated
  sampling, not evidence of considered agreement.
- **Herd / conformity bias** (arXiv 2505.21588): once a few agents vote yea, the
  rest conform — a supermajority is *easier* to reach on a bad-but-plausible motion
  than a genuinely contested one.
- **Votes/nominations are sometimes hallucinated** (arXiv 2403.12482).

**The consequence, baked into this design [rev2]:** a vote tally — even a perfectly
honest one — is **weak signal and cannot legitimately gate anything.** Layered on top
is an integrity hole: `send --from` is unauthenticated, so a `--from` tally is also
**forgeable** (one process can stamp many handles, manufacturing quorum,
supermajority, *and* the "attributable" audit log). The defenses, in order of how
much they actually carry:

1. **The binding gate is a human reading verifiable evidence (C1 + C4).** This is the
   load-bearing defense. The vote never decides; the human ratifies from the
   `--because` evidence, the cite-count, and the diff. A forged or herd-driven tally
   reaches a human who is looking at *evidence*, not a checkmark.
2. **Votes are authenticated to registered sessions** (`--session`, one-session-one-vote)
   so the *advisory* tally and the audit log are at least attributable to real agents,
   not arbitrary invented handles. **[rev2]**
3. **Behavioral signal beats declarative.** Weight a rule's distinct-sender
   **cite-count** (what agents *did*) above yea/nay (what they *say* under herd
   pressure) when judging a motion. **[rev2]**
4. **Entrenched core + human ratifier** are the backstop against the fleet voting
   itself somewhere bad — which is why C1–C4 sit outside the amendment procedure.
5. **The audit log is free** — every motion and vote is an append-only `messages`
   row; `amendments` surfaces who proposed and who voted. The *vote* side is
   attributable to registered sessions (`--session`); the *proposer* (`motion --from`)
   is a self-asserted handle, so treat the proposer field as a *lead, not proof* —
   which is fine, since the proposer never enters the tally and the binding check is
   the human reading evidence (defense #1).
6. **Amend rarely.** `GROUPCHAT_AMEND_COOLDOWN` and "friction is a feature" are a
   **stability/UX** choice (don't flag a motion the instant it's posted), **not** a
   capture defense — a sockpuppet loop is just as effective spread over the cooldown.
   Don't bill it as security. **[rev2]**

---

## File layout

New `+`, edited `~`:

```
groupchat/
├── .groupchat/
│   └── chat.py        ~ repo_root() helper (shared bus anchor); RULE_RE + kind-gated
│                        cite harvest in send(); rule_cites table + index (guarded);
│                        constitution parse/validate (CLI loud / hook fail-open);
│                        review; motion/vote(--session)/amendments/ratify CLIs
│   └── hooks/
│       └── session_start.py  ~ (P1) one fail-open line: point agents at CONSTITUTION.md
│                                 + (P3) mention any open motions awaiting their vote
├── skills/groupchat/SKILL.md  ~ add a "Governance" section (cite rules; propose via motion;
│                                 vote with --session; never run `ratify` — that's the human)
├── commands/
│   ├── constitution.md  + /groupchat:constitution → show the constitution
│   ├── motion.md        + /groupchat:motion        → propose an amendment (evidence required)
│   ├── vote.md          + /groupchat:vote          → vote on an open motion
│   └── review.md        + /groupchat:review        → run the repeal-first review
├── CLAUDE.md            ~ document the governance layer + tunables + dogfooding note;
│                          POINT the coordination-conventions section at CONSTITUTION.md
│                          (single source of truth) rather than restating the rules
└── README.md           ~ short "Governance" subsection

target repo (created by the human, tracked):
└── CONSTITUTION.md      + via `chat.py constitution init` (seeded from CLAUDE.md conventions)
```

`chat.db` gains the `rule_cites` table + index at runtime (gitignored, as today).
The constitution file is the only *committed* new artifact, lives at the shared repo
root (same anchor as the bus), and is authored by the human.

### CLAUDE.md migration **[rev2]**

The repo's `CLAUDE.md` already states the exact coordination conventions the Articles
will encode (e.g. "Announce before you act … prevents two agents editing the same
file" is verbatim the seed `R1`). Coordination conventions must live in **exactly one
place**:

- `constitution init` **seeds** the starter Articles from the CLAUDE.md "How you …
  should use the chat" / coordination section.
- The CLAUDE.md edit **replaces** those restated rules with a pointer to
  `CONSTITUTION.md` (keep the *meta*-instructions — how the bus works — static in
  CLAUDE.md; move the *coordination rules* to Articles).
- Result: no day-one drift, and amending an Article doesn't leave a stale copy in
  CLAUDE.md.

---

## Phases (ship in order; each independently verifiable)

**Phase 1 — the document.** `CONSTITUTION.md` format (zones + provenance +
monotonic ids); `repo_root()` shared anchor; `constitution init|show|check`
(init seeds Articles from CLAUDE.md); a **fail-open** SessionStart line pointing
agents at the file. No bus changes. *Useful on day one:* a structured, tracked,
human-authored coordination law that agents are told to follow, with one source of
truth. Verify via CLI + a piped SessionStart payload. (Scope `check` to
parse-but-don't-over-require in P1; only `id` is load-bearing — it's the citation
token.) **[rev2]**

**Phase 2 — measurement (the product's core).** `RULE_RE` + kind-gated cite harvest
in `send()`; `rule_cites` table + index; `chat.py review` emitting the repeal-first
report (dead rules by distinct-sender cite-count; drift flags; unknown/repealed-id
cites; diary-derived `HYPOTHESIS` promotion candidates). *This is the "does it help?"
engine — it measures rule usage, not the founding interruption goal.* The cite-count
repeal report is fully specified; drift-grep and diary-promotion are heuristic and
may be split to P2.5. Verify by posting messages that cite rules, then running
`review`. **[rev2]**

**Phase 3 — the parliament (advisory).** `kind='motion'`/`'vote'`;
`motion|vote(--session)|amendments|ratify`; motion lifecycle (open→closed→…);
**advisory** tally (registered sessions, distinct-voter denominator, no TEAM_SIZE
derivation); entrenched-core rejection; evidence-required; base-text TOCTOU guard;
`ratify` (human-run) **prints a diff + evidence dossier and posts a `system`
announcement** (no `--apply`, no `PROPOSED_AMENDMENTS.md`). The vote never gates; the
human ratifies from evidence. Verify by driving a motion → votes → ratify in an
isolated room and diffing the file + confirming the `system` announcement lands on the
cursor. **[rev2]**

**Phase 4 — (deferred, flagged) bounded autonomy.** Optional in-place auto-apply of a
*ratified* amendment, within strict guards: Articles-zone only, authenticated-session
quorum + supermajority + a non-zero cooldown, full audit, and a human veto window.
**Do not build this now.** It is the frontier where the capture risk is real; it ships
only on top of P1–P3's machinery and an explicit decision to relax the human-ratify
invariant (C1). Documented here so the boundary is named, not crossed by accident.

---

## Open notes / caveats

- **Cite regex precision.** `RULE_RE` is a tolerant *signal*, not an accountant's
  ledger. The `[rev2]` guards (case-sensitive `R`, MENTION_RE lookbehind, R-squared
  rejection, skip constitution-quotes) cover the common noise; don't gold-plate
  further. Decide and document the `[[R2]]` vs `R2` policy (accept both; both reduce
  to id `R2`).
- **The evidence gate is the human's.** Code enforces a motion *has* a `--because`;
  it cannot verify the evidence is real or that `source=` corresponds to a passing
  tally. That is the human ratifier's job, and the spec says so plainly — no tooling
  downstream may treat file provenance as authoritative.
- **One law, one anchor.** `CONSTITUTION.md` resolves at the **git common-dir parent**
  (via `repo_root()`), the same anchor as the bus, so cites in the one shared
  `chat.db` always tally against one law regardless of which worktree runs `review`.
  Do **not** use `--show-toplevel`. **[rev2]**
- **Governance never touches the write path.** Motions, votes, cites, and the review
  are reads and chat; none edits code or gates the Stop barrier. The barrier and the
  single-threaded-write convention are unchanged.
- **Mid-session delivery.** Because the cursor is the only delivery model, any change
  to the law (`ratify`) **must** also post a `messages` row — otherwise a long-running
  agent never learns the law changed. **[rev2]**
- **Bootstrapping order.** `rule_cites` and the new CLIs are harmless on a repo with
  no `CONSTITUTION.md` (review/constitution just report "no constitution yet"). The
  feature is opt-in: it does nothing until a human runs `constitution init`.
