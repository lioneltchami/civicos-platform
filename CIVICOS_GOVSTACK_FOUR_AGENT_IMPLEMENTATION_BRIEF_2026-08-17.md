# CivicOS GovStack Four-Agent Implementation Brief

**CivicOS baseline:** `284ce7d` (`test: add service-backed integration contracts`)
**Authority:** the pinned GovStack repository revisions in `official_source_revisions.tsv`.
**Purpose:** This unique brief contains only the evidence-backed work approved by two independent comparisons and two fresh blind alignment reviews. It is the sole input for a fresh implementation pass. It does **not** authorize an unsupported certification claim or guessed GovStack payloads.

> Implement traceability, evidence, local regressions, explicit scope boundaries, and safe internal guards. Do **not** alter external paths/schemas/statuses to guessed shapes, declare any Building Block compliant, add a new official Building Block claim, or fabricate official harness results.

## Confirmed scope

| Current classification | Building Blocks |
|---|---|
| **Explicit GovStack implementation; wire conformance unproven** | Consent, Payments, Scheduler through Appointments, File Management through Documents. |
| **Application module only; do not create a BB claim in this pass** | Messaging/Notifications, Workflow, CMS/Wagtail. |
| **Not Done Yet; do not implement in this pass** | Cloud and Infrastructure Hosting, Digital Registries, eMarketplace, eSignature, GIS, Identity, IM Connector, Information Mediator, Registration, Template, UX and Wallet. |

## Approved implementation items

| ID | Required implementation | Required behavior and limits |
|---|---|---|
| GSI-01 | **Create a machine-readable authority manifest.** | Record every pinned official source repository, commit, relevant API/spec/test artifact paths, SHA-256 hashes, and supported comparison status. Generate or validate it with a repository script/test so revision drift is detectable. Use only the supplied source snapshots; no network calls or live claims are needed. |
| GSI-02 | **Create per-BB operation traceability maps for Consent, Payments, Scheduler and File Management.** | Capture every discoverable official operation/artifact and map it to a CivicOS route/service/test, or mark it `UNVERIFIED`, `DEFERRED`, or `UNSUPPORTED` with reason. Include method/path, request/response schema source, identifiers, auth/authorization, states, status/errors, and local test evidence where official sources define those dimensions. Do not manufacture endpoint equivalence. Make maps machine-readable and add a validation test for required fields/status vocabulary. |
| GSI-03 | **Preserve and test the Consent signature-update decision.** | Identify the exact local signature-update event/audit behavior. Add a focused regression test proving its current `signature_updated` semantics and audit preservation. The map must mark official acceptance as `UNVERIFIED` unless an official fixture proves it; do not guess or rename external behavior. |
| GSI-04 | **Strengthen Payments local conformance evidence without guessing official wire shapes.** | Add or improve tests for existing Payment GovStack-facing behavior: duplicate/replayed callback or request idempotency, correlation/request identifiers, authorization boundaries, retry/failure handling, and local error translation. Tests must assert current documented behavior and link traceability rows, not invent unsupported status codes or fields. |
| GSI-05 | **Make Scheduler deployment scope explicit and guarded.** | Locate the existing single-government tenant limitation. Add an explicit configuration/setting/documentation declaration and tests that prevent unscoped cross-tenant behavior or make multi-tenant activation fail closed until role/isolation semantics are implemented. Preserve current valid single-government behavior. |
| GSI-06 | **Add File Management negative contract evidence.** | For existing document-facing APIs, add source-backed tests for unauthorized access, missing resource, scan failure/quarantine, and IDOR/ownership boundaries. Retain internal scanning/quarantine/retention controls. Traceability must distinguish local security behavior from unverified official response equivalence. |
| GSI-07 | **Make non-claimed scope explicit.** | Update certification/readiness/product-facing documentation to label Messaging, Workflow and CMS as CivicOS-local modules, not current GovStack BB conformance claims. Keep the Not Done Yet official catalog visible without treating its absence as a defect. |
| GSI-08 | **Add official-run archival tooling without claiming execution.** | Provide a safe command/script/template that can run pinned official test assets later, capture dependency/runtime versions, commands, raw stdout/stderr, adapter configuration identifier and traceability-row links. It must fail clearly when prerequisites are unavailable and it must not call production services or mark a run passed by default. |

## Explicit external/decision blocks

| Item | Why it is blocked | Required evidence before any implementation decision |
|---|---|---|
| Official Payments callback/heartbeat/request/batch/instruction semantics | No executed fixture/harness result or complete proven field map in the supplied evidence. | Pinned official harness/fixture run and a reviewed traceability map. |
| Official Consent acceptance of `signature_updated` audit representation | Local semantics are clear; official consumer/fixture acceptance is not. | Official scenario replay or upstream clarification. |
| File Management external scan/quarantine status semantics | Internal controls are meaningful but official externally visible lifecycle requirements are not proven. | Operation-level mapping and official fixture evidence. |
| Scheduler multi-government tenancy | Current recorded scope is single-government; multi-tenant role isolation is unproven. | Product/deployment decision plus isolation model and tests. |
| Shared cross-BB adapter abstraction | Commonality is plausible but BB-specific contracts are not yet fully mapped. | Complete individual maps showing a stable shared primitive. |
| Certification/compliance claim | No official run output is in scope. | Archived pinned official harness results and reviewed traceability evidence. |

## Verification requirements

| Area | Minimum verification |
|---|---|
| New governance artifacts | Deterministic hash/manifest validation; maps parse and enforce required fields/status vocabulary. |
| Consent | Focused existing/new consent GovStack tests plus the signature-update/audit regression. |
| Payments | Focused existing/new Payment GovStack tests proving duplicate/replay, correlation, auth/error/failure behavior that the code actually exposes. |
| Scheduler | Focused tenant/scope and GovStack Appointments tests. |
| Documents | Focused document API/security tests for unauthorized, missing, scan/quarantine and ownership cases. |
| Documentation/tooling | Markdown/style checks, script help/dry-run where available, `git diff --check`. |
| Official tools | Do not report them as executed unless dependencies and a safe adapter target were actually available. |

## Definition of done

The codebase will contain reproducible authority/traceability artifacts and stronger local evidence for its current implementation, while visibly separating **implemented local behavior**, **unverified official equivalence**, and **unsupported/deferred work**. This pass is successful only if it improves evidence without overstating conformance.

## References

[1]: `CIVICOS_GOVSTACK_BUILDING_BLOCK_COMPARISON_2026-08-17.md` — independent source-based comparison.
[2]: `blind-alignment-review-1.md` and `blind-alignment-review-2.md` — fresh code-by-code alignment review evidence.
[3]: `official_source_revisions.tsv` — pinned GovStack repository revisions.
[4]: `govstack_bb_catalog.tsv` — official catalog inventory.
