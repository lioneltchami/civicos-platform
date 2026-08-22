# Item 03 — SCH-02.2-A Scope Confirmation

Two shared-context analyses confirm a strict publisher-only boundary. SCH-02.2-A may add durable per-attempt evidence at the existing publisher enqueue boundary and deterministic crash-injection proof only. It must preserve the seven SCH-02.1 states and the existing meaning of `PUBLISHED` as a locally confirmed enqueue outcome, not broker receipt or recipient delivery.

| In scope | Required PostgreSQL evidence | Excluded |
|---|---|---|
| Claim, handoff phase, unknown handoff, finalization, expiry/reclaim, stale-finalization event evidence | Real committed claim/reclaim and stale-finalization proof | Recipient recovery and acknowledgement/history |
| Publisher-boundary test hooks | Pre-handoff and post-handoff/pre-finalization crash tests | Broker adapter redesign or confirmation protocol |
| Existing token/generation fence | One current owner under competing connections | Projections, fakes, harnesses, staging, and later increments |

A timeout or injected crash remains at-least-once ambiguity: it must never infer publication. Existing SCH-01 lifecycle and dispatch-lock behavior remain unchanged.
