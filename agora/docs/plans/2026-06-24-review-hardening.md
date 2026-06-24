# Review hardening (v0.15.1)

*2026-06-24. A thorough whole-codebase implementation review and the fixes it drove.*

After the layered build (squad sharding → council), a whole-codebase review (8 lenses —
cross-layer barrier, invariant composition, the Stop hook holistically, security,
concurrency, governance safety, code quality, cross-CLI — each finding adversarially
verified) targeted what the per-layer reviews could not see: the **seams between layers**
and whether the **system-wide invariants compose**. 33 findings, **24 confirmed** (3
blockers, 10 should-fix, 11 nits); 9 refuted (the verifiers correctly rejected the
deliberately-deferred items and non-reproducible claims). All confirmed fixed, TDD'd,
suite 34/34.

## Blockers

- **Legacy-DB migration broken (I6).** `messages.session_id` shipped in `CREATE TABLE`
  but had **no guarded `ALTER`**, so a pre-existing `.groupchat` room — exactly the rooms
  `_room_dirname` *prefers* — raised `OperationalError` on every `send()`. The one
  migration test never sent a message, so the suite stayed green over a broken upgrade.
  Fix: `_add_column_if_missing(messages, session_id)` + the test now sends + reads
  escalations on a migrated legacy db.
- **Council escalation false-clear (I5).** My v0.15.0 captain relay-clear fired on *any*
  chair `@mention` of the captain — so ordinary "@cap please rebase" cleared an open
  `@human` and the squad tore down with the operator's question unanswered. Fix: a
  captain's escalation clears only on an explicit **`[re #id]` relay marker** (the one
  `answer` stamps) from the chair/addressee — bare chatter no longer clears. `answer` is
  now usable by a relaying chair (`answer <id> … --from <chair>`, posts as the chair),
  keeping the relay ergonomic.

## Should-fix

- `ratify --confirm` is **caller-gated** (operator/lead, like the control plane); the
  read-only dossier stays open (I4/I7).
- The **park-ceiling** marks a force-released awaiting lead `done`, so it stops pinning
  teammates at the barrier (I5).
- The **chair stays parked while it owes a captain a relay** — an escalation in flight to
  it that it read but hasn't relayed (`pending_relays_for`, fail-safe) (I5).
- A **stale `standdown`** is cleared when a fresh solo cohort registers (mirrors the
  team-size reclaim), so a reused room within the 15-min window keeps its barrier (I5).
- **`@team` is squad-scoped** (a captain's rally doesn't wake other squads); `@all` stays
  fleet-wide (I2).
- **`inbox` is peek-only** — the single monotonic cursor can't "read this @mention but not
  an earlier broadcast", so advancing past the last mention silently dropped lower-id
  chatter (I3).
- A **non-hook (`parks=0`) agent no longer becomes the emergent chair** — `resolve_lead`'s
  floor prefers a hook-capable agent (a non-hook host can't be woken/parked) (I5).
- The **`dismissed` set is a dedicated table** (`INSERT OR IGNORE` / `DELETE`) — atomic,
  no read-modify-write lost-update, inherently fail-safe (no JSON to corrupt).

## Nits

Fail-safe `mentions` parse in `format_message`; a rename-hygiene sweep of runtime "group
chat" → agora strings (briefing header, UserPromptSubmit/Stop notices, doctor, bridge); a
misleading `bootstrap --squad` env-override note; the dead handle-keyed `open_escalations`
annotated (retained only for its tests); clearer `ratify`/`--from human` wording; the
`--from human` forgeability documented as by-design.

## Accepted / deferred (cosmetic, review-marked optional)

The dashboard barrier panel and the `is_quiet ◐` solo-suppression remain fleet-scoped
(not per-squad) — cosmetic; the unread/`schema_version` stamp stays unread (harmless).
The pre-existing asker-rename escalation edge and the deferred internal code-dir / bridge
rename / networked transport are unchanged (out of scope, by design).
