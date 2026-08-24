# Payments RB-02 Scope-Pure Assembly Manifest

## Local non-release assembly

The local payload at `artifacts/payments-rb02-option-b-scope-pure/` was assembled under the authorized Option B boundary. It is an internal component artifact only; it is not a staging/release artifact.

| Field | Value |
|---|---|
| Canonical content SHA-256 | `3fb5a4327e3ce5b0b9c076f3d2426d4e663ae178f7091ed6c19515c8c5b4ddca` |
| Payload manifest SHA-256 | `15b56e3bc3caf858522ce5196c70b61cc32efa476411455df367560a8d653ff9` |
| Local Git blob identity of canonical payload | `3575e72838e2e2449bfe1f4d24e24f35743137bc` |
| Mixed anchor | `49e69fb8a3051de6c1cf7adff8e16c928cf84412` — `provenance_only` |
| Assembly status | Complete; independent review pending |

## Exact payload paths

`artifacts/payments-rb02-option-b-scope-pure/patch-path-inventory.txt` records the exact allowed source paths and is equal to `expected-patch-paths.txt`. The payload contains four RB-02 implementation patches, the selected two-file BatchLease repair patch, and the two-setting RB-02.3 policy patch. Full commit provenance is stored in `payload/source-commits.tsv`.

No wildcard, source tree, unlisted path, Scheduler content, File Management content, environment overlay, deployment material, or secret is part of the canonical payload.
