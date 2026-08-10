# Repo Constitution

<!-- CONSTITUTION:CORE:BEGIN -->
## Core (entrenched — amendable only by a human, never by the parliament)

### C1 — The parliament is sovereign over the Articles
The agents amend the Articles themselves: a motion citing evidence -> an
authenticated vote -> automatic enactment after the objection window.
No human approval is required. Git remains the audit trail: every
enactment is a commit, and reverting one is the human veto of record.

### C2 — Hooks fail open
A coordination hook must never crash or block a session on error.

### C3 — Writes are single-threaded
Agents add intelligence, not concurrent edits. One writer per change.

### C4 — The amendment procedure
<!-- CONSTITUTION:PROCEDURE:autonomous -->
An Article changes when a motion holds, for the whole objection window:
(a) a supermajority of the cast votes, (b) a quorum of registered
voters, and (c) voters from at least two distinct models. The
parliament then applies the diff and commits it. This Core section
stays outside the procedure: it changes only by a direct human edit
in git.
<!-- CONSTITUTION:CORE:END -->

<!-- CONSTITUTION:ARTICLES:BEGIN -->
## Articles (amendable by the parliament — autonomous enactment)

### R1 — Announce before you touch a file
Post "starting on <path>" before editing, so two agents don't collide.
<!-- meta: id=R1 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R2 — Converge, don't fork
If two agents propose overlapping designs, one retracts. Do not merge into
an average; pick one and make it the contract.
<!-- meta: id=R2 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R3 — Claim before you work
If the room runs a task ledger, `task claim` your slice before starting. The
claim is atomic — if you lose the race you are told who holds it; coordinate
with the holder instead of double-working.
<!-- meta: id=R3 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R4 — Report results into the fan-in
When your slice is done, post `result --from <you> "..." [--task N]`. The
orchestrator reads results and summaries, not the chat scrollback.
<!-- meta: id=R4 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R5 — Mentions are for replies
@mention an agent only when you need it to act or answer — only mentions
block a teammate's Stop. Everything else is a plain broadcast.
<!-- meta: id=R5 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R6 — Answer before you leave
Respond to any unanswered @mention of you before finishing your slice.
<!-- meta: id=R6 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R7 — One door to the human
Reach the operator only via `@human`; it funnels to the lead. A lead absorbs
what repo conventions can answer and escalates only the residual.
<!-- meta: id=R7 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R8 — Don't poll
New messages arrive automatically; never loop `read` or spin to "stay
available" — the barrier parks you dormant and wakes you when needed.
<!-- meta: id=R8 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R9 — Declare the team size
When you know how many instances share the goal, run `expect N` early so the
barrier is precise; without it everyone falls back to the 90s startup grace.
<!-- meta: id=R9 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->
<!-- CONSTITUTION:ARTICLES:END -->
