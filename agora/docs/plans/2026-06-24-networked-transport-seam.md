# The networked-transport seam — a map, deliberately not a refactor

*2026-06-24. Vision item #3b. The honest answer to "how does Agora scale to agents on
OTHER machines." This is a design map and threat model, **not** an implementation — and
the reasoning for why building it now would be wrong.*

## Why a map and not code

The tempting move is to refactor the SQLite layer behind a `Transport` interface "so a
networked backend can drop in later." That is **premature abstraction**, and it's against
this project's grain:

- **No second implementation exists.** An interface with one impl is just indirection —
  it adds surface area and risk (the cursor, the barrier, the dormancy/byte-identical
  guarantees all run through these primitives) for zero capability today.
- **It violates "dormant until used."** Every layer here renders byte-identically until
  exercised; a speculative seam that nothing calls is the opposite.
- **The hard part isn't the interface — it's the distributed semantics.** Swapping
  `sqlite3` for a network client is the easy 10%. The 90% is below.

So: name the seam precisely, write the threat model, and build it **when a real second
backend is on the table** — not before.

## Where the seam actually is

The whole transport surface is ~8 functions in `chat.py`, already narrow:
`connect` / `store_dir`, `send`, `recent_messages` / `messages_since`, `unread_for`,
`mark_read` (the cursor), `register` / `active_agents`, `get_meta` / `set_meta`. A
networked transport would reimplement *these* against a shared service. The hooks and CLI
never touch SQLite directly — they already go through these — so the seam is honest. That
narrowness is the asset; it doesn't need an ABC today to be swappable later.

## The walls a networked transport hits (the real work)

1. **The barrier is CAP-bound.** `team_done` is a global predicate over the active set.
   Across machines with a partition, you cannot have a consistent barrier *and*
   availability — a partition either wedges finished agents or releases them early. A
   distributed barrier needs an explicit consistency choice (a lease/quorum service), not
   a SQLite `SELECT`.
2. **Clock skew breaks the active window.** Liveness, the startup grace, standdown
   expiry, and stale-size reclaim all compare `iso_age_seconds` against one wall clock.
   Across hosts that requires a logical clock or a server-authoritative timestamp, not
   each agent's `now()`.
3. **Identity becomes forgeable.** `--from <handle>` is already unauthenticated (a local
   guardrail, fine when everyone shares a trusted disk). Across the network it's a
   genuine spoof vector — `@human` routing, the lead, the control plane (`standdown` /
   `dismiss`), and *votes* all key off identity. A networked transport needs real auth
   (per-agent tokens), or the governance signals become unreliable — which compounds the
   capture wall (#3a): forged cross-machine votes are worse than homogeneous ones.
4. **Squad sharding helps, but doesn't cross the wall.** Per-squad barriers (#1) bound
   *coordination* fan-out on one bus; they don't make the bus itself distributed.

## The honest scaling story

- **Coordinate many agents on one machine:** solved — the shared file + squad sharding.
- **Run hundreds of *live* agents per host:** an OS/cost wall (processes, tokens), not a
  transport one — that's what the native Agent/Task fan-out is for.
- **Agents on other machines (LAN/internet):** a **separate product** with its own threat
  model (the four walls above). Reachable, but it is auth + consensus + clocks, not a
  drop-in backend. Defer until there's a concrete need and a chosen consistency model.

## What would make it real (the future PR, when warranted)

A `Transport` protocol over the ~8 functions, a `LocalTransport` (today's SQLite, byte-
identical), and a networked impl backed by a service that provides: authenticated agent
identity, a server-authoritative clock, and a lease/quorum primitive for the barrier.
Built behind tests proving `LocalTransport` is unchanged — the same discipline every layer
here followed.
