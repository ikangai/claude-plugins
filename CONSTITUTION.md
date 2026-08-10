# Repo Constitution

<!-- CONSTITUTION:CORE:BEGIN -->
## Core (entrenched — amendable only by a human, never by the parliament)

### C1 — The human is the final authority
No automated process may modify this Core section or apply an amendment to
the Articles without a human committing it.

### C2 — Hooks fail open
A coordination hook must never crash or block a session on error.

### C3 — Writes are single-threaded
Agents add intelligence, not concurrent edits. One writer per change.

### C4 — The amendment procedure
Articles change only by: a motion citing evidence -> an advisory vote -> a
human ratifying the proposed diff after reading the cited evidence. Core
changes are out of scope for this procedure.
<!-- CONSTITUTION:CORE:END -->

<!-- CONSTITUTION:ARTICLES:BEGIN -->
## Articles (amendable by the parliament, ratified by a human)

### R1 — Announce before you touch a file
Post "starting on <path>" before editing, so two agents don't collide.
<!-- meta: id=R1 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->

### R2 — Converge, don't fork
If two agents propose overlapping designs, one retracts. Do not merge into
an average; pick one and make it the contract.
<!-- meta: id=R2 added=2026-08-10 by=human ratified=2026-08-10 amended= source= -->
<!-- CONSTITUTION:ARTICLES:END -->
