# Item 01–07 GovStack readiness reconciliation

**Reconciled:** 2026-08-19
**Repository state reviewed:** `main` through `88a23c5`
**Purpose:** Answer whether the Item 01–07 records remain accurate after the later Payments and Scheduler work, and distinguish internal implementation gaps from external evidence and submission gates.

> **Conclusion:** The incomplete items are **not all in the same category as Consent**. Consent, Payments, and Scheduler remain mixed **internal engineering/runtime-evidence plus external-observable-evidence** items. File Management and Item 05 are complete only for their deliberately bounded local scopes. Item 06 is principally an **authorised staging-evidence** gate. Item 07 is principally a **release, observable-evidence, and submission-governance** gate that inherits unresolved candidate blockers from the individual Building Blocks.

No staging target, credentials, secret, deployment, official test harness, portal account, form, or submission was accessed or used for this reconciliation.

## Official baseline applied

The official testing site describes a **software-version and Building-Block-specific self-assessment** against functional and cross-functional requirements; its results model separately records deployment, requirement-specification, and API compliance. A local test, operation matrix, or source review does not by itself populate that assessment.[1]

The official requirements model distinguishes **functional** and **cross-functional** requirements, and distinguishes **observable** requirements—verified through running-system behavior—from **auditable** requirements—verified through deterministic evidence artefacts such as source, deployment, policy, or audit material.[2] Every Building Block extends the core cross-functional requirements; a solution cannot make a compliance claim while failing a REQUIRED requirement.[3]

| Building Block | Official Working Group source checked on 2026-08-19 | Default-branch revision | CivicOS record alignment |
|---|---|---|---|
| Consent | [GovStackWorkingGroup/bb-consent][4] | `7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` | Matches the pinned Consent authority used by the Item 01 records. |
| Payments | [GovStackWorkingGroup/bb-payments][5] | `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` | Matches the pinned Payments authority used by the Item 02 records. |
| Scheduler | [GovStackWorkingGroup/bb-scheduler][6] | `d425be5cc0d6c606f351e5bf89be6d5c6c83c468` | Matches the pinned Scheduler authority used by the Item 03 records. |
| File Management | [GovStackWorkingGroup/bb-file-management][7] | `cf50bf4952491bd1ede775aa3c90a228319c3977` | Matches the pinned File Management authority used by the Item 04 records. |

Each official repository exposes the expected `api/`, `spec/`, and `test/` source families. Revision pinning and local operation matrices establish traceability; they do **not** establish route equivalence, deployed behavior, official-suite passage, or testing-site readiness.

## Blocker taxonomy

| Code | Category | Meaning in this reconciliation |
|---|---|---|
| **A** | Internal engineering, specification parity, or local runtime evidence | A required path is absent, optional, helper-only, not mounted/enforced, insufficiently tested, or not yet shown to satisfy the pinned source contract. |
| **B** | Authorised external environment or official-suite evidence | A versioned deployed candidate, approved dependency/staging arrangement, observable run, or official-suite result is required. Repository-only work cannot close it. |
| **C** | Release and submission governance | Immutable candidate identity, deployment provenance, accountable approval, and portal/receipt conditions are required. |
| **D** | Bounded local/documentation scope already complete | The item itself is done exactly as scoped, but it intentionally does not make a broader deployment, official-validation, or submission claim. |

## Current item-by-item determination

| Item | Current truthful status | Primary category | Same combined category as Consent? | What the later codebase work changes |
|---|---|---|---|---|
| **01 — Consent** | **Partially aligned / remediation required** | **A + B + C** | **Reference category** | Local authority pinning, 42-operation traceability, validation, audit/model mapping, claim guards, and a fail-closed integration boundary improved evidence quality. They did not prove operation-level wire parity, complete lifecycle/provenance parity, approved Identity/Information Mediator interoperability, staging/official-suite evidence, or approval. |
| **02 — Payments** | **Partially aligned / remediation required; not ready for readiness/submission claim** | **A + B + C** | **Yes** | The later passes materially added verified provider-observation finality, conservative status projection, runtime controls, idempotency/batch/reconciliation primitives, and 203 focused local passing tests. Critical live-path enforcement remains open. |
| **03 — Scheduler** | **Partially aligned / remediation required** | **A + B** | **Yes** | The later pass added a migration-backed recipient/outbox core, claim/lease lifecycle controls, a dedicated task, and 60 focused local passing tests. The durable path remains opt-in, publish recovery is not claim-fenced, runtime evidence is incomplete, fakes are helper-level, and durable authorized status is absent. |
| **04 — File Management** | **Fully aligned / ready for the Item 04 local/CI runner checklist only** | **D**, then **B** for broader official evidence | **No** | The local runner, exact test layers, CI wiring, redacted reports, operation matrix, and archived 1,159-test evidence close the scoped internal work. No additional local runner defect was identified. Official runtime/harness evidence remains deliberately absent. |
| **05 — Per-block requirements assessments** | **Fully aligned / ready for the bounded assessment-documentation scope only** | **D** | **No** | The assessment, 28-record evidence index, offline validator, static test, dependency map, and Items 6–7 gate are complete. It documents downstream gaps; it cannot itself close them. |
| **06 — Authorised staging rehearsals** | **Partially aligned / remediation required** | **B**, with preparatory local controls | **No, not in the same combined sense** | The fail-closed rehearsal manifest, offline preflight, control matrix, and static tests improve preparation. The remaining acceptance rows require authorised remote execution, not further repository-only claims. |
| **07 — Testing-site submissions** | **Partially aligned / remediation required** | **B + C**, dependent on unresolved **A** in candidates | **No, it is the downstream gate** | Candidate dossiers, a negative-state evidence index, stop conditions, receipt ledger, and offline validation are complete preparation. All candidates remain `NOT_READY`, non-release, not performed, and not submitted. |

## What remains for each Item

### Item 01 — Consent

Consent remains a true mixed-gap item. The current Consent package proves source traceability and bounded local safeguards, but all 42 operation rows remain intentionally partial. The mounted routes and local model/audit structures do not yet establish full field, relationship, revision, signature, withdrawal, state-transition, provenance, authentication, error, or wire-equivalence behavior. The Identity and Information Mediator boundary is deliberately fail-closed rather than an approved interoperating path.

The next internal step is a narrow operation-by-operation parity pass for a selected supported surface, with mounted request/serializer/status/auth/error regression coverage and no promotion of unsupported rows. The next external step requires an authorised synthetic non-production dependency arrangement, a frozen candidate/deployment/configuration/migration identity, and a pinned official-suite run. Functional approval and any testing-site action remain human-governed.

### Item 02 — Payments

Payments is **not merely awaiting staging**. The current record identifies critical internal gaps: provider registration is process-local; G2P bulk can create executable attempts with an empty tenant scope; prepayment is not a provider-attempt flow; submission lacks a durable in-flight claim; status-first recovery is not unified; HTTP idempotency is not enforced at all required mutating routes; bulk work does not require a live batch lease; and reconciliation route authorization/tenant derivation lacks adequate request-level proof.

The later work substantially improved the fail-closed lifecycle boundary, but a reusable service or optional enqueue is not equivalent to an enforced route-to-worker-to-adapter flow. Internal remediation must close those runtime gaps and add mounted-route, task, migration-upgrade, race, negative-security, and deterministic-adapter tests. Only then can authorised non-production provider/source integrations and a versioned official-suite run supply the observable evidence required by the official baseline.

### Item 03 — Scheduler

Scheduler is also **not merely awaiting staging**. It now has a useful durable core: `SchedulerRecipientDelivery`, `SchedulerOutbox`, materialization, lease-fenced transitions, retry/dead-letter/replay/acknowledgement, and a dedicated delivery task. That closes the absence of persistent per-recipient state.

However, its default production-internal path still preserves the legacy Boolean behavior because durable dispatch is configuration-gated. The outbox publisher has no durable claim/lease, all 37 operations do not have a Django request/runtime trace, Payments/Consent fakes are not wired through a local topology, and no database-backed authorized non-PII operational status projection exists. These are internal engineering and evidence-topology items before any staging or official suite can be meaningful.

### Item 04 — File Management

Item 04 is different. Its completed scope was **test-runner enablement**, not official conformance. The scoped result is fully aligned and locally evidenced: a canonical runner, explicit and exact unit/integration/e2e layers, CI wiring, redacted reports, contributor guidance, operation mapping, checksum-protected archives, and 1,159 passing scoped tests. The candidate and adapter remain local-only/disabled by design.

Accordingly, no new File Management runner pass is justified merely to chase a broader status. If an official assessment is later desired, the next action is an authorised versioned target and an approved official-harness process. That is external observable evidence, not an identified local test-runner defect.

### Item 05 — Per-block requirements assessments

Item 05 is also different. It is an internal evidence-management artifact and is complete for that stated purpose. It correctly records rather than resolves the Consent, Payments, Scheduler, and File Management boundaries. Its validator and static test protect consistency; it must not be misread as a statement that any assessed Building Block is ready for staging or the testing site.

No additional Item 05 engineering remediation is required. It remains the authoritative guide for selecting a specific later flow and evidence gate.

### Item 06 — Authorised staging rehearsals

Item 06 is principally an external evidence gate. Repository work created a non-secret, synthetic-only, network-disabled `NOT_RUN` manifest, an offline fail-closed validator, a control/regression matrix, and tests that protect the non-execution boundary. Those are the correct preparations, but they cannot establish topology parity, access control, protected promotion, immutable image use, secret-store behavior, rollback/restore, monitoring, or end-to-end regression success.

An authorised human must select the target and operators, define the release/image/configuration/migration identity and evidence custodian, approve the time/data/rollback policy, and then perform the rehearsal. No repository-only modification can truthfully substitute for that execution evidence.

### Item 07 — Testing-site submissions

Item 07 is a downstream evidence and governance gate, not a code feature. The testing site’s own page describes assessment by **software version** and **Building Block specification/version**, with separate deployment, requirement-specification, and API compliance dimensions.[1] The current dossiers truthfully label every candidate as not ready, non-release, not performed, and not submitted.

The first step is to select one candidate only after its Item 01/02/03/04 prerequisites are objectively met, freeze a release/version/image identity, bind it to staged deployment and official result evidence, and obtain explicit human approval. Only immediately before any posting action may the official form and receipt ledger be completed. This reconciliation neither authorises nor performs that action.

## Direct answer to the Consent comparison

| Group | Are the incomplete items in the same category as Consent? | Why |
|---|---|---|
| **Consent, Payments, Scheduler** | **Yes** | All retain substantive internal implementation/runtime-evidence gaps **and** lack authorised observable/official evidence. Their technical details differ, but neither can be solved solely by staging nor solely by more documentation. |
| **File Management** | **No** | The scoped local/CI runner work is complete. Its only material broader boundary is future authorised official/runtime evidence. |
| **Item 05** | **No** | The assessment/documentation scope is complete. It records other items’ gaps rather than being a Building Block runtime gap itself. |
| **Item 06** | **No** | Its unresolved acceptance criteria are principally authorised environment, operational control, and rehearsal evidence—not the same core implementation/interoperability parity work as Consent. |
| **Item 07** | **No** | It is the final submission/release governance gate. It inherits unresolved candidate gaps but does not create a separate Consent-like code-parity problem. |

## Ordered, permissible next actions

1. **Continue one item at a time with Item 02 Payments**, because it has unresolved critical internal runtime-enforcement defects. Do not treat it as staging-only.
2. After Item 02 is independently fully aligned for internal scope, complete the unresolved **Item 03 Scheduler** durable-path, outbox, 37-operation runtime-evidence, fake-topology, and status-projection work.
3. Keep **Item 04** and **Item 05** closed at their explicitly bounded local scopes; do not inflate those results into official conformance claims.
4. Only after the individual Building Block internal gates are truly complete, seek explicit human authorisation for **Item 06** staging rehearsal. The repository is prepared to document this safely but cannot perform unauthorised environment work.
5. Treat **Item 07** as a final, per-Building-Block submission gate. A single platform-wide claim is not supported; submit only a specific, release-bound candidate whose required evidence is complete and whose final form posting has explicit human approval.

## References

[1]: [GovStack testing-site requirements](https://testing.govstack.global/en/requirements)

[2]: [GovStack requirements model](https://specs.govstack.global/architecture/5-specification-framework/5.3-requirements-model)

[3]: [GovStack Building Block specification framework](https://specs.govstack.global/architecture/2.0.0/5-specification-framework/5.2-building-block-specification)

[4]: [GovStackWorkingGroup Consent Building Block](https://github.com/GovStackWorkingGroup/bb-consent)

[5]: [GovStackWorkingGroup Payments Building Block](https://github.com/GovStackWorkingGroup/bb-payments)

[6]: [GovStackWorkingGroup Scheduler Building Block](https://github.com/GovStackWorkingGroup/bb-scheduler)

[7]: [GovStackWorkingGroup File Management Building Block](https://github.com/GovStackWorkingGroup/bb-file-management)
