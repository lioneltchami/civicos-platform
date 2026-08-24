# Future Scope-Pure Payments RB-02 Artifact — Construction Plan

## Status and chosen method

**NOT_BUILT.** The preferred method for a later separately authorised cycle is a **deterministic manifest-first, allowlist-driven canonical assembly**. It starts from the approved path allowlist and exact full source identities, applies a frozen canonicalization contract, and produces a separate canonical manifest and content digest only after authorised assembly. The mixed anchor remains provenance metadata only and provides no content-bearing input.

This is a planning document. No worktree, branch, export, artifact bytes, digest, tag, publication, deployment, secret access, staging, official test, release, or submission is created by this plan.

## Future planning-level sequence

| Step | Later authorised action description | Required precondition | Fail-closed result |
|---|---|---|---|
| 1 | Confirm separate construction authorization and exact Option B scope. | Named authorization, owner, permitted actions, and time boundary. | Keep `NOT_BUILT`. |
| 2 | Freeze the exact path allowlist and source/repair identities. | No unlisted or ambiguous input. | Reject input. |
| 3 | Classify every prospective item as allowed content, provenance-only metadata, or prohibited. | Exactly one classification per item. | Stop on ambiguity. |
| 4 | Establish a clean isolated assembly context chosen by the later authorization. | No mixed anchor content; no secrets/environment/deployment material. | Stop; do not assemble. |
| 5 | Apply a fixed canonicalization contract: UTF-8, line endings, ordering, whitespace, escaping, null/date/number behavior, locale/timezone, and schema version. | Contract is complete and deterministic. | Block on unspecified or locale-dependent rule. |
| 6 | Produce a planned path-level manifest and exclusion report. | Every included path matches the allowlist exactly. | Reject contamination. |
| 7 | Compute future identity records only after successful assembly: content digest, manifest digest, tree/release identity, freeze UTC, lockfile/hash refs, and non-secret profile reference. | Final canonical bytes and independent review evidence exist. | No freeze/digest claim. |
| 8 | Conduct independent reconstruction and scope review. | Byte-identical canonical outputs and zero exclusion violations. | Invalidate prospective result. |

## Future identity fields

The later artifact record must include `artifact_name`, `status`, Option B scope label, authorization record, ordered full source IDs, repair rationale, allowlist/exclusion manifest digests, mixed-anchor `provenance_only` field, canonicalization/schema/toolchain identifiers, non-secret configuration profile reference, canonical content digest, manifest digest, tree/release identity, lockfile/hash references, freeze UTC, and independent reconstruction evidence. These fields are **unset now**; no artifact identity exists.

## Dry-run / preflight checklist

| Gate | Future pass condition | Failure disposition |
|---|---|---|
| Authorization | A separate explicit construction authorization exists. | Do not construct. |
| Allowlist | Exact `docs/civicos-payments-rb02-scope-pure-path-allowlist-20260822.md` membership is frozen. | Reject unlisted path. |
| Exclusions | No Scheduler SCH-01/SCH-02.x, File Management, secrets, environment, deployment, or release content. | Stop and preserve `NOT_BUILT`. |
| Provenance | `49e69fb…` is metadata only, never content/digest/candidate identity. | Stop on misuse. |
| Input identity | Every full SHA and repair item resolves and matches the approved manifest. | Stop on absence/substitution. |
| Canonicalization | Rules and toolchain are fully pinned and reproducible. | Block on nondeterminism. |
| Scope scan | Path/content/dependency review reports zero contamination. | Reject scope. |
| Two-run design | Later independent runs must be byte-identical. | Do not accept semantic-only similarity. |
| Status control | Dry-run creates no artifact bytes, digest, branch, worktree, export, tag, or external side effect. | Terminate planning run. |

> This plan is executable only in a later separately authorised cycle. It is not an authorization to create or operate an artifact now.
