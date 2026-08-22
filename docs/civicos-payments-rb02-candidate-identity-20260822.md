# CivicOS Payments RB-02 — Immutable Component Candidate Identity

## 1. Record status

| Field | Recorded value |
|---|---|
| Candidate label | `Civicos-Payments-RB02-Component-Review-20260822` |
| Record class | Immutable **component manifest** for human-owner review only |
| Operational status | **Not a release artifact; not authorised for deployment, staging, official testing, or submission** |
| Candidate source anchor | `49e69fb8a3051de6c1cf7adff8e16c928cf84412` |
| Source-anchor tree | `61019af851ceca1327ac7500167f7969cea217e6` |
| Source-anchor timestamp | `2026-08-21T23:45:33-06:00` |
| Non-secret profile | Full Git object IDs, commit subjects/roles, tree ID, source paths, command checks, and non-secret toolchain/configuration references only |

## 2. Immutable manifest

| Scope role | Full immutable commit SHA |
|---|---|
| RB-02.1 implementation | `5ed08441dfd536738eed6bd8442b3cf76041f716` |
| RB-02.1 verification | `330f19f6a22591d06d45d6f41e4e9d08462549ec` |
| RB-02.2 implementation | `2fb03f830bd2dd2c50445420c4c9aa0c3aaf965e` |
| RB-02.2 verification | `d1a3cd275cad0969a91d601b6f8eadfb3c948315` |
| RB-02.3 implementation | `698716908f65d90d84aed607dacb8b8b3815ed5` |
| RB-02.3 verification | `54a64bc7df027960ccd2c76724afab5c968f77a2` |
| RB-02.4 implementation | `a7ee3345ccb351e5b38fd2526a151765dd56216` |
| RB-02.4 final verification | `74de74a29f189002ecbf49d80987e024b315eefb` |
| Required supporting dependency | `49e69fb8a3051de6c1cf7adff8e16c928cf84412` — Payments PostgreSQL `BatchLease` migration repair |

Repository checks already established that each listed RB-02 commit and the final verification commit is an ancestor of the source anchor. The final verification commit is therefore evidence within the source-anchor ancestry; it is **not** the source anchor itself.

## 3. Inclusion and exclusion boundary

The manifest includes only the **internally closed Payments RB-02.1–RB-02.4 component evidence** and the stated Payments PostgreSQL migration prerequisite. It excludes SCH-02.2; all other Scheduler recovery, recipient, history, projection, adapter, and harness work; File Management; other unlisted Payments work; deployment artifacts; environment-specific configuration; secrets; customer, production, or payment data; official validation; release; and submission.

### Material scope-isolation limitation

The source anchor contains Scheduler SCH-01 changes in addition to the Payments repair. It is therefore **not a scope-pure deployable Payments-only artifact**. This manifest is suitable to identify and review the Payments RB-02 component history only. It must not be used to claim a Payments-only build, release, staging candidate, security approval, production readiness, or submission readiness.

## 4. Reproduction and verification procedure

A human reviewer can verify the non-secret identity relationship from a clean repository checkout:

```bash
# Resolve every listed object as a commit.
git cat-file -e <full-sha>^{commit}

# Confirm each RB-02 implementation/verification commit is an ancestor of the source anchor.
git merge-base --is-ancestor <rb02-full-sha> 49e69fb8a3051de6c1cf7adff8e16c928cf84412

# Confirm final RB-02 verification is an ancestor of the supporting repair anchor.
git merge-base --is-ancestor 74de74a29f189002ecbf49d80987e024b315eefb 49e69fb8a3051de6c1cf7adff8e16c928cf84412

# Confirm source-anchor commit/tree identity and inspect mixed scope.
git show -s --format='commit=%H%ntree=%T%nparents=%P%nsubject=%s' 49e69fb8a3051de6c1cf7adff8e16c928cf84412
git diff --name-status 74de74a29f189002ecbf49d80987e024b315eefb 49e69fb8a3051de6c1cf7adff8e16c928cf84412
```

Record Git version, repository remote identity, clean-checkout state, dependency/toolchain versions, non-secret configuration-profile reference, executed commands, UTC timestamps, and outcomes in a future evidence index. Do not add passwords, tokens, keys, connection strings, secret-manager output, or sensitive data to this record.

## 5. Known limitations and non-authorisation

This manifest preserves recorded internal verification provenance only. It does not re-run or extend any verification, prove environment equivalence, establish functional completeness beyond the recorded scope, authorise data or database mutation, approve a release, or validate any external integration.

> **No deployment, staging access, secret access, official-suite execution, release, submission, or external communication is requested, granted, performed, or implied by this component manifest.**

An immutable, scope-pure build/artifact and separate human/governance decisions would be required before any future operational candidate could be considered.
