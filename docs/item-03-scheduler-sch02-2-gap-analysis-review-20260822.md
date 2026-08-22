# Item 03 — SCH-02.2 Crash-Window Gap Analysis Review

**Conclusion: confirmed with one boundary correction.** Two fresh reviewers confirm that SCH-02.1 proves durable database ownership, bounded recovery, and stale finalization fencing, but not the external handoff crash window. A returned enqueue call is not broker receipt, and stale database fencing cannot revoke an already emitted message or request.

The priority order is correct: first create a minimal durable per-attempt phase/evidence contract with deterministic crash injection; then prove unknown-handoff and lease-expiry reclaim races; only then evaluate broker reconciliation. Recipient recovery, acknowledgement history, projections, and transport redesign remain excluded.

The reviewers require the first increment to avoid changing what `PUBLISHED` means globally. It must record publisher attempt phases around the existing handoff and prove recovery before/after the call boundary with real PostgreSQL committed state, while explicitly preserving at-least-once semantics.
