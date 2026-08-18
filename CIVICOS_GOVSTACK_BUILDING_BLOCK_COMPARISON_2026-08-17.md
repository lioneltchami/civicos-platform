# CivicOS vs. GovStack Building Blocks — Detailed Source Comparison

**Comparison baseline:** CivicOS source snapshot from commit `284ce7d` (`test: add service-backed integration contracts`).
**Assessment method:** Two independent reviews compared the snapshot with cloned, authoritative GitHub repositories from `GovStackWorkingGroup`. Every conformance conclusion below is restricted to the supplied source revisions and CivicOS code/readiness evidence. It is **not** an interoperability certification or a claim that an official harness has passed.

> **Core conclusion:** CivicOS has substantial, explicit GovStack implementation work for **Consent**, **Payments**, **Scheduler** (through Appointments), and **File Management** (through Documents). Its strongest quality is domain and security implementation; its principal deficit is **reproducible wire-level proof** against each official Building Block’s API/spec/test artifacts. The code should not make or retain a certified/compliant claim until the pre-proceeding gates are complete.

## 1. Authoritative comparison sources

| Official Building Block | GovStack repository | Pinned source revision | CivicOS classification | Comparison use |
|---|---|---:|---|---|
| Consent | [bb-consent](https://github.com/GovStackWorkingGroup/bb-consent) | `7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` | **Explicit implementation / conformance unproven** | Data-agreement lifecycle, auditability, feature/spec/test material. |
| Payments | [bb-payments](https://github.com/GovStackWorkingGroup/bb-payments) | `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` | **Explicit implementation / conformance unproven** | API/schema, asynchronous lifecycle, callbacks, typed errors, test harness. |
| Scheduler | [bb-scheduler](https://github.com/GovStackWorkingGroup/bb-scheduler) | `d425be5cc0d6c606f351e5bf89be6d5c6c83c468` | **Explicit implementation via Appointments / conformance unproven** | Entities, resources, subscribers, events, appointments, API/test material. |
| File Management | [bb-file-management](https://github.com/GovStackWorkingGroup/bb-file-management) | `cf50bf4952491bd1ede775aa3c90a228319c3977` | **Explicit implementation via Documents / conformance unproven** | Swagger/API, data/workflow/service specifications, test material. |
| Messaging | [bb-messaging](https://github.com/GovStackWorkingGroup/bb-messaging) | `b1f705a06c227f3757725b26e565ad6b791560f3` | **Application module only** | Local notifications exist; no CivicOS GovStack claim was established. |
| Workflow | [bb-workflow](https://github.com/GovStackWorkingGroup/bb-workflow) | `96b467556600b5df0616d196896c88135caa1a0c` | **Application module only** | Local workflows exist; no CivicOS GovStack claim was established. |
| CMS | [bb-cms](https://github.com/GovStackWorkingGroup/bb-cms) | `68aaaa7a92962026252eecc77affdb981f7bcb98` | **Application module only** | Wagtail CMS integration exists; no official API conformance claim was established. |

The official repository revisions above were cloned through GitHub and recorded in `official_source_revisions.tsv`. The catalog contains nineteen official `bb-*` repositories. The absence of an implementation claim is not treated as a defect; it is reported as **Not Done Yet**.

## 2. Classification rules

| Classification | Meaning |
|---|---|
| **Explicit implementation / conformance unproven** | CivicOS includes an explicit GovStack-facing claim, BB-specific code/tests/specification/readiness documentation, or both. The official wire contract still requires traceability and executed compatibility evidence. |
| **Application module only** | CivicOS has a valuable application module in a similar domain, but this snapshot does not establish a GovStack Building Block claim or an official adapter. It must not be described as an implemented GovStack BB without a separate conformance effort. |
| **Not Done Yet** | No CivicOS Building Block implementation or claim was found in the supplied snapshot. No gap analysis is inferred for absent code. |

This distinction resolves an important review difference: **Messaging, Workflow, and CMS are coded CivicOS modules**, but the durable CivicOS certification record does not place them within the current GovStack scope. They are assessed below only as future adapter candidates, not as deficient claimed BB implementations.

## 3. Executive inventory

| CivicOS area | Evidence in CivicOS snapshot | Official BB conclusion | Current status |
|---|---|---|---|
| `apps/consent` | GovStack URLs/views, lifecycle migrations, signatures, webhook/RTBF logic, serializers, tests, readiness/self-assessment documents. | Consent | Implemented domain and API work; official fixture/harness proof missing. |
| `apps/payments` | GovStack models, auth, serializers, services, tasks, URLs, views, voucher/P2G/bulk-payment migrations and tests. | Payments | Broadest explicit integration; async/callback/error conformance proof missing. |
| `apps/appointments` | GovStack auth/serializers/throttling/URLs/views and entity/resource/event/appointment/subscriber/alert services/tests. | Scheduler | Strong conceptual overlap and explicit claim; tenant and wire-contract gates remain. |
| `apps/documents` | Document models, service/API layers, scan/quarantine/download flows, migrations, tests, local document specifications. | File Management | Strong secure document service; official resource/operation mapping and harness proof missing. |
| `apps/notifications` | Queued email/inbox/services/task work. | Messaging | Application module only; no current official BB claim. |
| `apps/workflows` | Workflow models, services, views, URLs, tests. | Workflow | Application module only; no current official BB claim. |
| `apps/cms` and Wagtail models/templates | Content types/pages/blocks and application CMS behavior. | CMS | Application module only; no current official API claim. |
| `apps/volunteers`, `apps/forms`, `apps/portal`, `apps/reports`, `apps/audit`, `apps/auth_extension` | CivicOS-local capabilities. | None established by evidence | Application modules only. |

## 4. Detailed comparison: claimed Building Blocks

### 4.1 Consent

| Dimension | GovStack authority evidence | CivicOS evidence | Alignment assessment | Pre-proceeding work |
|---|---|---|---|---|
| Scope and lifecycle | The Consent repository describes a process-oriented, auditable bilateral data-agreement BB and ships feature/spec/test material. | `apps/consent/` supplies records, categories, revisions, signatures, GovStack routes/views, services, migrations, and `test_govstack_api.py`. | **Strong domain alignment.** Code proves a real consent implementation, not endpoint-by-endpoint compatibility. | Produce an operation/path/payload/status crosswalk from every official scenario and API asset to each CivicOS route/service. |
| Data agreement and identifiers | Official behavior is defined through source feature/spec assets. | CivicOS readiness material documents individual-ID parsing corrections and accepted signature-body behavior. | **Partially aligned.** Historical local fixes are encouraging but not a substitute for current official fixtures. | Run official fixtures/scenarios against an adapter or executable endpoint; preserve raw pass/fail output. |
| State semantics | Official source includes agreement behavior; revision handling is material to consent evidence. | CivicOS supports grants, withdrawal, revisions and signature updates. The readiness record documents a signature update represented through `granted` plus `details.trigger=signature_updated`. | **Compatible extension or maintainability risk, not an automatic defect.** The compatibility implication is undocumented. | Decide and document whether a dedicated audit action is required; add a mapping/contract test for signature-update events. |
| Security and audit | Auditable consent is a core official concern. | CivicOS includes audit work, RTBF behavior, signature controls, webhooks, and SSRF protections in its consent area. | **Strong internal security posture.** Official audit/error response equivalence remains unproven. | Trace official audit/error requirements to fields/events and add negative conformance tests. |
| Test proof | GovStack repository includes test material. | CivicOS has extensive local BB tests and a project readiness record. | **Internal test evidence only.** No supplied official run result. | Execute official test material at the recorded revision, archive inputs/output, and list any deviations. |

**Consent strengths.** CivicOS is not merely storing preference flags: its code and readiness evidence cover revision, signature, RTBF, audit, and protection against unsafe callbacks. That is an appropriate foundation for a GovStack adapter.

**Consent blockers.** Do not claim official compatibility until the scenario/operation crosswalk and reproducible official-run evidence exist. The audit-action representation requires an explicit compatibility decision.

### 4.2 Payments

| Dimension | GovStack authority evidence | CivicOS evidence | Alignment assessment | Pre-proceeding work |
|---|---|---|---|---|
| API/schema | The official repository contains Mobile Money/OpenAPI assets, bulk status artifacts, schema definitions and compatibility-oriented test material. | `apps/payments/govstack_models.py`, `govstack_serializers.py`, `govstack_urls.py`, `govstack_views.py`, and BB tests cover beneficiaries, vouchers, P2G, bulk payments and registered BBs. | **Strong implementation footprint.** Exact JSON/path/method/media-type equivalence is not established. | Generate a machine-readable endpoint/schema/status/error traceability map; diff official schemas against exposed CivicOS adapters. |
| Asynchronous lifecycle | Official assets define asynchronous request states, heartbeat/callback behavior, credit instructions/batches and status progression. | CivicOS uses services/tasks, webhooks, receipts, refunds, recurring payments and local state models. | **High-risk gap.** Similar local capabilities do not prove official lifecycle semantics. | Implement/verify official heartbeat, request-state retrieval, callback verification/retry, batch/instruction status and idempotent replay behavior. |
| Errors and authorization | Official API distinguishes authorization, business-rule, resource and server failures. | CivicOS has GovStack auth, throttling, encrypted data/gateway exception handling and local tests. | **Partially aligned; wire translation unproven.** | Map every official error/status object to a tested CivicOS response; cover unauthorized, invalid payload, missing resource, provider failure and replay. |
| Audit/security | Official interoperability relies on trusted identifiers and request correlation. | CivicOS migrations/tests show tenant IDs, correlation/request IDs, webhooks, refunds, credentials and gateway work. | **Promising internal hardening.** Proof that all official identifiers survive requests/callbacks is absent. | Publish identifier/correlation map and idempotency/audit evidence per official operation. |
| Harness evidence | Payments README/source includes a compatibility-oriented test harness. | CivicOS record reports local/regression tests only. | **Certification blocker.** | Execute the official harness at `4b63a6…`; retain raw results and pin dependencies. |

**Payments strengths.** This is CivicOS’s most developed explicit GovStack area, with specialized models, serializers, auth, services, tasks and tests—not merely a generic payment module.

**Payments blockers.** Payments must be the first conformance workstream because its official wire lifecycle, typed errors, callbacks, replay/idempotency and harness evidence are not yet proven by the snapshot.

### 4.3 Scheduler through Appointments

| Dimension | GovStack authority evidence | CivicOS evidence | Alignment assessment | Pre-proceeding work |
|---|---|---|---|---|
| Domain/API scope | Scheduler source supplies API/spec/test material for scheduling concepts. | `apps/appointments/` includes GovStack entities, resources, events, appointments, subscribers, affiliations, messages, alert schedules, logs, auth/throttling and tests. | **Strong concept alignment and explicit claim.** | Create official-operation to CivicOS-route mappings, including filters, query fields and response forms. |
| Response/filter semantics | Official sources define concrete request/response behavior. | CivicOS readiness record says eight array filters, status-200 behavior and wrapper handling were corrected. | **Evidence of prior interoperability work.** Current revision still needs repeatable fixture proof. | Replay official request fixtures and retain response/status/schema diffs. |
| State/conflict behavior | Official scheduler behavior depends on resource/event/appointment lifecycle and conflict constraints. | Local entity/resource/event/appointment services are substantive. | **Unproven wire/state compatibility.** | Publish state/conflict matrix; test conflicts, invalid transitions, missing references and concurrent bookings. |
| Credentials and tenant scoping | Official cross-cutting requirements require clear caller and tenant behavior. | CivicOS uses hashed BB credentials, token configuration and throttling. Its record accepts a single-government tenant model without per-tenant `GovStackRegisteredBB.role` scope. | **Deployment gate.** Safe only if scope is explicitly single-government. | Either document/enforce single-government scope at every boundary or implement per-tenant authorization/isolation before multi-tenant deployment. |
| Harness evidence | Official scheduler repo includes API/spec/test material. | CivicOS has self-tests/readiness evidence, not an official-run artifact. | **Conformance unproven.** | Execute pinned official scenarios/tests and archive results. |

**Scheduler strengths.** The appointment implementation has unusually broad BB-shaped coverage (resources, subscribers, events, messages, alerts and logs), and the local record identifies real prior spec mismatches.

**Scheduler blockers.** Resolve or explicitly accept the tenant boundary before multi-tenant use; produce an auditable API/state mapping and official test evidence.

### 4.4 File Management through Documents

| Dimension | GovStack authority evidence | CivicOS evidence | Alignment assessment | Pre-proceeding work |
|---|---|---|---|---|
| API/resource model | Official repository provides Swagger/OpenAPI plus data, workflow and service-API material. | `apps/documents/` has models, service/API code, migrations, commands, tests and document-management specifications. | **Substantial service exists.** Official resource/operation/status mapping is absent. | Map every official document/resource/content/metadata operation to CivicOS endpoints or an explicit adapter disposition. |
| Upload/storage lifecycle | Official source defines lifecycle/API behavior. | CivicOS records MIME validation, ClamAV scanning, encrypted-PDF/ZIP-bomb controls, quarantine-to-active promotion, retention and presigned downloads. | **Stronger internal security in several areas is plausible.** It is only a compatible extension if official responses remain available through the adapter. | Document which local security steps are internal and ensure official lifecycle/status responses remain contract-compatible. |
| Authorization/audit | Official tests/specs define access behavior. | CivicOS record captures staff-cap, scan-status, IDOR audit and forwarded-IP remediation. | **Good adversarial hardening.** BB-specific error/authorization translation remains unproven. | Add traceability plus unauthorized/IDOR/missing-resource/scan-failure contract tests. |
| Harness evidence | Official repository contains test material. | CivicOS reports extensive local document/cross-BB tests. | **Internal readiness, not external proof.** | Execute official test assets against a pinned adapter and archive raw output. |

**File Management strengths.** CivicOS’s scanning, quarantine, retention and access controls are meaningful implementation qualities that may exceed the bare official API surface. They should be retained behind an adapter, not removed to force internal-model similarity.

**File Management blockers.** Complete the operation/resource/status/error mapping and execute the official test material before a compatibility claim.

## 5. Coded but not currently claimed as GovStack Building Blocks

| CivicOS module | Closest official BB | Current evidence | Required decision before treating as a BB |
|---|---|---|---|
| Notifications | Messaging | `apps/notifications/` provides application email/inbox/service/task behavior. Current certification scope excludes notifications. | Keep explicitly local, or start a separate Messaging BB authority/mapping/harness workstream. Do not call existing notification behavior GovStack Messaging conformance. |
| Workflows | Workflow | `apps/workflows/` has internal models/services/views/tests. Current certification scope excludes workflows. | Keep explicitly local, or prepare a workflow-definition/task/transition/error adapter and separate official comparison. |
| CMS/Wagtail | CMS | `apps/cms/` and Wagtail content behavior are implemented. | Decide whether CivicOS needs the official CMS API. If not, document this as a local CMS integration; if yes, start a standalone BB effort. |

These are **not immediate conformance failures**. They should not be mixed into the four claimed-BB remediation unless product scope changes.

## 6. Clearly missing before proceeding

| Priority | Gap | Affected claimed BBs | Why it matters | Required gate |
|---:|---|---|---|---|
| P0 | Pinned authority manifest with artifact hashes and supported-version decision | Consent, Payments, Scheduler, File Management | Conformance cannot be reproduced when an upstream source changes. | Commit a machine-readable manifest of repository, commit, relevant API/spec/test paths and hashes. |
| P0 | Operation-level traceability matrix | All four | Internal code similarity is not proof of method/path/schema/status/error compatibility. | Every official operation is mapped to a CivicOS adapter endpoint or explicitly marked unsupported/deferred with justification. |
| P0 | Executed official fixture/harness evidence | All four | The existing readiness evidence is self-authored/local and cannot establish official interoperability. | Run the official test material at the recorded revision; retain raw commands, fixtures, logs, failures and remediation links. |
| P0 | Payments async/callback/error/idempotency verification | Payments | Payments has the largest wire-contract and financial-integrity surface. | Test heartbeat, callbacks, replay, request/batch/instruction states, typed errors, correlation IDs and authorization. |
| P0 | Tenant boundary decision | Scheduler | Current single-government role scoping is a known deployment constraint. | Enforce/document single-government scope or implement per-tenant authorization before multi-tenant release. |
| P1 | Consent lifecycle/audit compatibility decision | Consent | Signature-update audit semantics are a documented local decision. | Map/refine the event semantics and verify official scenario compatibility. |
| P1 | File resource/status/error adapter proof | File Management | Strong security controls can coexist with the official API only if the external contract is stable. | Add resource/status/error mapping and negative contract tests. |
| P1 | Shared cross-BB translation layer | All four | Auth, correlation IDs, error shapes, audit fields and pagination can drift independently. | Define reusable adapter primitives only after the per-BB mapping establishes common behavior; protect with regression tests. |
| P2 | Formal scope decisions for Messaging, Workflow and CMS | Application-only modules | Prevents overstated future compliance claims. | Publish “local only” decisions or open separate BB conformance epics with their own authority revisions. |

## 7. Recommended implementation order

| Order | Workstream | Concrete deliverable | Exit evidence |
|---:|---|---|---|
| 1 | Authority baseline | Versioned GovStack source manifest with hashes and named API/spec/test artifacts. | Reviewable source manifest, current at a known commit. |
| 2 | Traceability generation | Per-BB JSON/Markdown operation maps for paths, methods, auth, schemas, identifiers, statuses/errors and states. | No official operation is unmapped or silently ignored. |
| 3 | Payments adapter proof | Contract boundary plus replay/callback/state/error tests. | Pinned official compatibility output and negative-case coverage. |
| 4 | Scheduler tenancy and contract proof | Explicit deployment-scope policy plus state/filter/response tests. | Approved scope or multi-tenant isolation tests; harness output. |
| 5 | Consent and File Management contract proof | Lifecycle/resource/error adapters and official-fixture results. | Scenario/harness logs linked to the source manifest. |
| 6 | Cross-BB hardening | Shared error/auth/correlation/audit primitives where maps genuinely agree. | Regression suite prevents adapter drift. |
| 7 | Scope expansion | Messaging, Workflow, CMS only after a product decision and new comparison. | Separate authority, plan and conformance evidence. |

## 8. Official catalog: Not Done Yet

The following official GovStack catalog Building Blocks have **no corresponding CivicOS implementation or claim in this source snapshot** and are therefore **Not Done Yet**. They require no inferred gap analysis at this stage.

| Not Done Yet official BB |
|---|
| Cloud and Infrastructure Hosting |
| Digital Registries |
| eMarketplace |
| eSignature |
| GIS |
| Identity |
| IM Connector |
| Information Mediator |
| Registration |
| Template |
| UX |
| Wallet |

## 9. Validation approach and limitations

| Topic | Status |
|---|---|
| Source comparison | Completed against the pinned GitHub snapshots listed above. |
| CivicOS code review | Completed at source level for explicit BB modules, APIs, models, services, migrations, tests and readiness documentation. |
| Official harness execution | **Not evidenced in the supplied snapshot.** This is the primary certification limitation. |
| Live deployment/integration testing | Not performed. No production credentials, partner endpoints, or runtime traces were used. |
| “Done better” findings | Security controls such as document scanning/quarantine and consent signature/RTBF functionality may be stronger internal capabilities, but they are treated as compatible extensions only if an adapter preserves the official wire contract. |

## 10. References

[1]: [GovStackWorkingGroup/bb-consent at `7af4b62`](https://github.com/GovStackWorkingGroup/bb-consent/tree/7af4b62a1c0b0d7073d42b37c71b0f08bea63dda)
[2]: [GovStackWorkingGroup/bb-payments at `4b63a6b`](https://github.com/GovStackWorkingGroup/bb-payments/tree/4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a)
[3]: [GovStackWorkingGroup/bb-scheduler at `d425be5`](https://github.com/GovStackWorkingGroup/bb-scheduler/tree/d425be5cc0d6c606f351e5bf89be6d5c6c83c468)
[4]: [GovStackWorkingGroup/bb-file-management at `cf50bf4`](https://github.com/GovStackWorkingGroup/bb-file-management/tree/cf50bf4952491bd1ede775aa3c90a228319c3977)
[5]: [GovStackWorkingGroup/bb-messaging at `b1f705a`](https://github.com/GovStackWorkingGroup/bb-messaging/tree/b1f705a06c227f3757725b26e565ad6b791560f3)
[6]: [GovStackWorkingGroup/bb-workflow at `96b4675`](https://github.com/GovStackWorkingGroup/bb-workflow/tree/96b467556600b5df0616d196896c88135caa1a0c)
[7]: [GovStackWorkingGroup/bb-cms at `68aaaa7`](https://github.com/GovStackWorkingGroup/bb-cms/tree/68aaaa7a92962026252eecc77affdb981f7bcb98)
[8]: `GOVSTACK_BB_CERTIFICATION_RECORD.md` and BB-specific CivicOS source within the compared snapshot.
[9]: `govstack_bb_catalog.tsv` and `official_source_revisions.tsv` generated from the GovStack GitHub catalog and cloned sources during this assessment.
