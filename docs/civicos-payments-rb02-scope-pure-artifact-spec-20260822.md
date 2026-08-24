# Future Scope-Pure Payments RB-02 Immutable Artifact Specification

## Status and purpose

**Option B specification only — NOT BUILT.** This document defines a future scope-pure Payments RB-02 artifact for a later separately authorised construction cycle. It creates no artifact bytes, branch, worktree, export, tag, digest, release, staging object, deployment, or submission.

| Field | Required future value |
|---|---|
| Proposed artifact label | `Civicos-Payments-RB02-OptionB-ScopePure` |
| Current status | `NOT_BUILT` |
| Scope ID | `Payments-RB-02` |
| Scope option | `B` |
| Future freeze time | Populate only after a separately authorised construction and successful verification |
| Future identity | Populate only after construction: canonical content digest, manifest digest, exact tree/release identity, lockfile/hash references, and non-secret configuration-profile reference |

## Exact inclusion boundary

A future artifact may include **only** the full RB-02.1–RB-02.4 implementation and verification provenance set, together with the minimal content required for the Payments `BatchLease` PostgreSQL repair. Every included path/content item must appear in a future allowlist manifest with a direct scope or repair-necessity rationale.

| Scope role | Required full immutable ID |
|---|---|
| RB-02.1 implementation | `5ed08441dfd536738eed6bd8442b3cf76041f716` |
| RB-02.1 verification | `330f19f6a22591d06d45d6f41e4e9d08462549ec` |
| RB-02.2 implementation | `2fb03f830bd2dd2c50445420c4c9aa0c3aaf965e` |
| RB-02.2 verification | `d1a3cd275cad0969a91d601b6f8eadfb3c948315` |
| RB-02.3 implementation | `698716908f65d90d84aed607dacb8b8b3815ed51` |
| RB-02.3 verification | `54a64bc7df027960ccd2c76724afab5c968f77a2` |
| RB-02.4 implementation | `a7ee3345ccb351e5b38fd2526a151765dd562160` |
| RB-02.4 verification | `74de74a29f189002ecbf49d80987e024b315eefb` |
| Supporting repair provenance | `49e69fb8a3051de6c1cf7adff8e16c928cf84412`, limited to necessary Payments `BatchLease` repair content only |

## Mandatory exclusions

The future artifact must exclude Scheduler SCH-01 source and documentation; all SCH-02.x content; File Management; unrelated Payments work; secrets and credentials; environment-specific configuration; deployment/infrastructure configuration; build outputs; release artifacts; and all content whose inclusion cannot be justified by the allowlist.

`49e69fb8a3051de6c1cf7adff8e16c928cf84412` is **provenance only**. It is not the future artifact, staging digest, release digest, build input shortcut, or scope authority. Its mixed Scheduler content must not enter the future scope-pure artifact.

## Later authorised construction methods — description only

A later authorised cycle may use a clean isolated worktree/branch, a content-filtered reconstruction, or a documented export assembled from the approved commit and repair content. The method must be reproducible, must generate a canonical path-level manifest, and must not use cherry-picking, tagging, publishing, secret handling, deployment, staging, official tests, release, or submission unless separately authorised for that later cycle.

## Future verification checklist

| Check | Future pass condition | Failure disposition |
|---|---|---|
| Separate authorization | Explicit written permission identifies scope, operator, and permitted construction action. | Stop; this specification is not authorization. |
| Source identity | All listed SHAs resolve exactly and RB-02.1–.4 roles match the manifest. | Stop; no substitution or abbreviation. |
| Repair minimality | Each included `BatchLease` repair path has a direct Payments RB-02 necessity rationale. | Remove or reject unneeded material. |
| Scope allowlist | Every artifact path is listed and justified. | Reject out-of-scope content. |
| Scheduler exclusion | No SCH-01 or SCH-02.x source, docs, tests, configuration, or output remains. | Stop and reject. |
| File Management exclusion | No File Management content remains. | Stop and reject. |
| Sensitive/config exclusion | No secret, credential, environment, deployment, or release material is included. | Quarantine and stop. |
| Anchor disposition | Mixed anchor is marked provenance-only and never used as final digest. | Correct metadata or reject. |
| Reproducibility | An independent clean reconstruction produces identical canonical content and manifest digests. | Stop and investigate. |
| Freeze integrity | Final digests are computed only after canonical assembly and recorded with UTC freeze time and toolchain/lockfile references. | Reject mutable or ambiguous result. |
| Independent review | Two independent reviewers approve scope, identity, exclusion evidence, and digest semantics. | Do not accept artifact. |

## Risks and failure modes

Mixed-anchor contamination, abbreviated or substituted commits, over-inclusion of Payments dependencies, hidden Scheduler/File Management paths, secret/config leakage, source-digest conflation, non-reproducible assembly, and post-freeze mutation are all blocking risks. The future builder must fail closed: uncertain scope, missing evidence, or any out-of-scope content invalidates the proposed artifact.

> **Explicit non-existence and non-authorisation:** The scope-pure artifact is **not built** in this cycle. No build, cherry-pick, tag, publication, deployment, secret access, staging, official-suite execution, release, or submission is requested, granted, performed, or implied.
