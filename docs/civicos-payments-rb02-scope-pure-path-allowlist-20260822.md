# Future Scope-Pure Payments RB-02 Artifact — Path Allowlist

## Status

**Planning only; artifact NOT_BUILT.** This allowlist defines a future Option B construction boundary. It does not copy, build, export, hash, tag, or otherwise materialize any artifact.

## Exact allowed paths

| Project-relative path | Necessity rationale | Inclusion rule |
|---|---|---|
| `apps/payments/govstack_batch_lease.py` | RB-02.1 atomic batch lease and fencing implementation. | Required. |
| `apps/payments/govstack_batch_decision.py` | RB-02.3 provider-finality-aware batch decision implementation. | Required. |
| `apps/payments/govstack_failure_services.py` | RB-02.2/RB-02.3 durable failure/finality handling. | Required. |
| `apps/payments/govstack_models.py` | RB-02 binding/finality/decision persistence and minimal `BatchLease` repair model content. | Required, path-level extraction limited to RB-02 and repair content. |
| `apps/payments/govstack_tasks.py` | RB-02.1–RB-02.4 live task, lease, decision, and competing-worker path. | Required. |
| `apps/payments/tests/test_item02_rb02_batch_lease_live.py` | Focused real-database RB-02 lease, finality, policy, and competing-worker verification surface. | Required. |
| `apps/payments/migrations/0044_item02_rb022_credit_instruction_payment_attempt_binding.py` | RB-02.2 durable instruction-to-attempt binding schema. | Required. |
| `apps/payments/migrations/0045_item02_rb023_govstack_batch_decision.py` | RB-02.3 batch-decision schema. | Required. |
| `apps/payments/migrations/0033_alter_batchlease_created_at_alter_batchlease_id_and_more.py` | Minimal historical Payments `BatchLease` PostgreSQL migration repair. | Required only to the precise repair content; future builder must document necessity. |
| `config/settings/base.py` | RB-02.3 policy setting change appears in implementation history. | **Conditional:** include only the minimum policy setting content after separate path/content necessity review; no environment overlays. |

## Mandatory exclusions

| Excluded path or class | Reason |
|---|---|
| `apps/appointments/**`, including migrations, tests, services, models, tasks, and documentation | Scheduler SCH-01/SCH-02.x content, including mixed-anchor delta, is out of scope. |
| `docs/item-03-**`, Scheduler-specific records, and any path containing `sch01`, `sch02`, `scheduler`, or `appointment` | Scheduler provenance may be cited | `docs/item-03-**`, Scheduler-specific records, and any path containing `sch01`, `sch02`, `scheduler`, or `appointmenoutputs | Explicitly excluded workstream. |
| Any Payments path not listed above | Fail closed: unrelated Payments work is excluded absent a separate approved allowlist revision. |
| `.env*`, credentials, tokens, private keys, secret stores, customer/payment data | No-secret and no-sensitive-data boundary. |
| Environment-specific settings, deployment/release/CI/CD/infrastructure/container configuration, generated build artifacts | Operational material is outside the source-pure artifact boundary. |
| Mixed anchor `49e69fb8a3051de6c1cf7adff8e16c928cf84412` as a whole tree | Provenance only; never a future artifact or staging digest. |

## Fail-closed application rule

A later authorised builder must use exact project-relative path comparison. No directory-wide inclusion, wildcard expansion, symlink resolution, inferred transitive dependency, generated file, vendored copy, or newly discovered path is permitted without an explicit allowlist amendment and independent review. The `config/settings/base.py` entry is excluded unless the specific RB-02.3 policy setting necessity is proven and separately recorded.
