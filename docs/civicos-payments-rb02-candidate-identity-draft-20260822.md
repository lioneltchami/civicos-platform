# Payments RB-02 Candidate Identity — Controlling Commit Draft

## Verified ancestry

All eight RB-02 implementation/verification commits and the required Payments PostgreSQL repair are reachable from `49e69fb8a3051de6c1cf7adff8e16c928cf84412`:

| Increment | Implementation | Verification |
|---|---|---|
| RB-02.1 | `5ed08441dfd536738eed6bd8442b3cf76041f716` | `330f19f6a22591d06d45d6f41e4e9d08462549ec` |
| RB-02.2 | `2fb03f830bd2dd2c50445420c4c9aa0c3aaf965e` | `d1a3cd275cad0969a91d601b6f8eadfb3c948315` |
| RB-02.3 | `698716908f65d90d84aed607dacb8b8b3815ed5` | `54a64bc7df027960ccd2c76724afab5c968f77a2` |
| RB-02.4 | `a7ee3345ccb351e5b38fd2526a151765dd56216` | `74de74a29f189002ecbf49d80987e024b315eefb` |
| Supporting dependency | `49e69fb8a3051de6c1cf7adff8e16c928cf84412` — Payments PostgreSQL `BatchLease` migration repair |

The final RB-02 verification commit is an ancestor of the repair commit. The repair commit tree is `61019af851ceca1327ac7500167f7969cea217e6`.

## Scope-isolation finding

`49e69fb…` is a valid immutable source anchor for the repair and all RB-02 history, **but it is not a Payments-only release artifact**: its delta from the final RB-02 verification includes Scheduler SCH-01 source and documentation changes as well as the Payments repair. Therefore it must not be represented as a scope-pure Payments staging artifact.

The formal candidate record must identify a **Payments RB-02 component manifest**—the exact RB-02 chain plus repair—not a deployable full-repository release artifact. Creation of a scope-pure immutable build/artifact is a future human-controlled release activity and remains outside this documentation cycle.

## Exclusions

SCH-02.2, all recipient/history/projection/adapter Scheduler work, File Management, unrelated Payments changes, environment-specific configuration, secrets, production/customer/payment data, deployment artifacts, staging activity, official tests, release activity, and submission are excluded.
