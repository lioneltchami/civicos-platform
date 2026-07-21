# GovStack BB Honest Readiness Assessment
## CivicOS vs. GovStack.global Certification Reality

**Last updated:** 2026-07-17
**Assessed by:** Assisted deep codebase + spec review
**Scope:** All GovStack BBs with live spec repos at `github.com/GovStackWorkingGroup`

---

## ⚠️ Critical Framing: Two Different Metrics

The previous readiness table (`docs/PROJECT_OVERVIEW_AND_STATUS.md`, last updated 2026-07-02) showed most building blocks as **✅ Complete**. That table was **accurate for the wrong metric**.

It measured **CivicOS internal production readiness** — does the app have tests, is it deployed, does it do its job well for a Canadian municipality? That is a legitimate and useful measure. But it was then labelled as "GovStack BB status", which it is not.

**GovStack BB certification** means:
1. Your API surface matches the OpenAPI spec published at `github.com/GovStackWorkingGroup/bb-<name>`
2. Your implementation passes the Gherkin test harness at `testing.govstack.global`
3. Your service can be discovered and orchestrated by other GovStack-compliant systems

CivicOS, as of this assessment, has **one BB** with a GovStack API layer: `apps/consent/` (with `govstack_views.py` + `govstack_urls.py`). All other apps are CivicOS-internal APIs that do not implement the GovStack OpenAPI surface.

This document corrects the record.

---

## The Corrected Table

| BB / Module | GovStack Spec Repo | CivicOS App(s) | Tests | CivicOS Status | GovStack BB Status | Domain Match? |
|---|---|---|---|---|---|---|
| **Consent BB** | `bb-consent` | `apps/consent/` | 246 | ✅ Production-Ready | 🔵 Harness submitted | ✅ Correct domain |
| **Payments BB** | `bb-payments` | `apps/payments/` | 1,115 | ✅ Production-Ready* | 🔴 Not started | ❌ Wrong domain |
| **Documents / Registries BB** | `bb-digital-registries` / `bb-file-management` | `apps/documents/` | 810 | ✅ Production-Ready* | 🔴 Not started | ⚠️ Partial overlap |
| **Scheduler BB** | `bb-scheduler` | `apps/appointments/` | 359 | 🟡 Wave 4 of 8 | 🔴 Not started | ✅ Correct domain |
| **Messaging BB** | `bb-messaging` | `apps/notifications/` | 77 | 🟡 Email only | 🔴 Not started | ⚠️ Partial overlap |
| **eServices / Forms BB** | `bb-eservices` | `apps/forms/` | 103 | 🟡 CMS-coupled | 🔴 Not started | ⚠️ Partial overlap |
| **Identity BB** | `bb-identity` | `apps/auth_extension/` | 75 | ✅ Works for CivicOS | 🔴 Not started | ❌ Wrong domain |
| **CMS BB** | `bb-cms` | `apps/cms/` | 0 | 🟡 No tests | 🔴 Not started | ✅ Correct domain |
| Citizen Portal | *(no GovStack BB)* | `apps/portal/` | 172 | ✅ Ready | N/A | N/A |
| Volunteers | *(no GovStack BB)* | `apps/volunteers/` | 831 | ✅ Ready | N/A | N/A |
| Analytics & Reporting | *(no GovStack BB)* | `apps/reports/` | 485 | ✅ Ready | N/A | N/A |
| Backoffice | *(no GovStack BB)* | `apps/backoffice/` | 121 | ✅ Ready | N/A | N/A |
| Workflows | *(no GovStack BB)* | `apps/workflows/` | 113 | ✅ Ready | N/A | N/A |
| REST API layer | *(no GovStack BB)* | `apps/api/` | 115 | ✅ Ready | N/A | N/A |
| Audit | *(cross-cutting)* | `apps/audit/` | 19 | 🟡 Thin coverage | N/A (cross-cutting) | N/A |
| Core / Config | *(cross-cutting)* | `apps/core/` | 64 | ✅ Ready | N/A (cross-cutting) | N/A |

*\* "Production-Ready" with asterisk = needs env config before live deployment (Stripe keys / S3 + ClamAV).*

**Status legend:**
- ✅ Production-Ready — fully tested, deployed, meets CivicOS requirements
- 🟡 Partial — implemented but incomplete
- 🔵 Pending — GovStack layer built, harness submitted, awaiting result
- 🔴 Not started — no `govstack_views.py` / `govstack_urls.py` exists

---

## Deep-Dive: Each BB

### 1. Consent BB — `bb-consent`

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-consent` (OpenAPI v1.1.0-rc1, info.version `23Q4`)
**CivicOS app:** `apps/consent/`
**Tests:** 246 passing

**What exists:**
- `apps/consent/govstack_views.py` — GovStack-facing API views (the only such file in the codebase)
- `apps/consent/govstack_urls.py` — URL routing for GovStack endpoints
- Full service layer: `ConsentService.grant()`, `withdraw()`, `attach_signature()`
- Full audit trail: `ConsentAuditEntry`, `ConsentRevision`
- Webhook delivery with encrypted secrets (`EncryptedCharField`)
- Three rounds of Codex audit fixes applied (Rounds 1, 2, 3)

**What's pending:**
- Harness result from `testing.govstack.global` (submitted)
- H-03: `serializedSnapshot` returns JSON object, spec says string
- M-08: Inconsistent pagination envelopes across list endpoints

**Verdict:** The only BB with a real GovStack API layer. Correctly in-progress. Previous table said "In Progress" — that was accurate.

---

### 2. Payments BB — `bb-payments` ❌ Domain Mismatch

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-payments`
**GovStack spec authored by:** ITU, GSMA, MIFOS Initiative, World Bank
**GovStack scope:** G2P (government-to-person) cash disbursements, social transfers, mobile money wallets, GSMA Mojaloop interoperability, cross-border payments

**CivicOS app:** `apps/payments/`
**CivicOS scope:** Stripe-based fee collection and donations for Canadian municipal services (CAD only, hardcoded in model header: `"Currency always CAD"`)

**Why these are different domains:**
- GovStack Payments is about *disbursing* money from government to citizens (welfare, disaster relief, social benefits). CivicOS Payments is about *collecting* money from citizens to government (permit fees, park bookings, donations).
- GovStack integrates with Mojaloop, mobile money operators (MTN, Airtel), and national payment switches. CivicOS integrates with Stripe.
- GovStack Payments has no Stripe connector. Stripe is not a Mojaloop participant.

**What the previous table said:** ✅ Ready (1,115 tests)
**What it should have said:** 🔴 Not started (wrong domain — would require a new parallel G2P layer)

**Path to GovStack certification:** Build `apps/payments/govstack_views.py` implementing the `bb-payments` OpenAPI (G2P endpoints: `/payments`, `/bulk-transfer`, `/voucher`, etc.). This is a new domain layer, not a refactor of the Stripe app.

---

### 3. Documents / Digital Registries BB — `bb-digital-registries` ⚠️ Partial Overlap

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-digital-registries`
**GovStack scope:** National-scale civil registries (birth/death/marriage records), land registries, tax roll databases, business registries. Linked to national identity systems.

**CivicOS app:** `apps/documents/`
**CivicOS scope:** S3-backed municipal document storage, ClamAV virus scanning, PIPEDA-compliant download tokens, per-citizen file management.

**Overlap vs. gap:**
- Both deal with document storage and retrieval — some API patterns transfer
- GovStack Digital Registries is about *national authoritative databases*, not file storage. It has registry-of-registries concepts, version-controlled records, and integration with MOSIP identity.
- There is also `bb-file-management` (a newer GovStack spec) that is closer to what CivicOS Documents does. This may be a better alignment target.

**What the previous table said:** ✅ Ready (810 tests)
**What it should have said:** 🔴 Not started (different target spec; wrong GovStack BB identified)

**Path to GovStack certification:** Evaluate `bb-file-management` spec first (closer domain). Then build `govstack_views.py` against whichever spec fits better.

---

### 4. Scheduler BB — `bb-scheduler` ✅ Best Next Candidate

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-scheduler`
**GovStack scope:** Resource booking, time-slot management, appointment scheduling, capacity management for government services.

**CivicOS app:** `apps/appointments/`
**CivicOS scope:** Slot-based booking system with `Booking`, `Wave`, `ServiceLocation`, `AppointmentPolicy` models. Waves 1–4 implemented (creation, confirmation, cancellation, rescheduling).

**Domain match:** Strong. Both are about booking government service slots. The GovStack spec patterns (availability query, slot reservation, booking confirmation, cancellation) map almost 1:1 to CivicOS Appointments receivers (`on_booking_created`, `on_booking_confirmed`, `on_booking_cancelled`).

**Gap:** No `govstack_views.py` / `govstack_urls.py`. The business logic is done; only the GovStack API surface is missing.

**What the previous table said:** 🟡 Partial (Wave 4 of 8) — this was about CivicOS internal completeness, not GovStack status.
**What it should have said for GovStack:** 🔴 Not started (no GovStack API layer, but best candidate for next sprint)

**Path to GovStack certification:** 1–2 sprints to build `govstack_views.py` + Gherkin test fixtures. This is the recommended next BB after Consent certification.

---

### 5. Messaging BB — `bb-messaging` ⚠️ Partial Overlap

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-messaging`
**GovStack scope:** Multi-channel outbound messaging (SMS, email, push notification, in-app), templated message delivery, delivery receipt tracking.

**CivicOS app:** `apps/notifications/`
**CivicOS scope:** Email-only notifications (Django email backend + Celery). GC Notify API key is wired in settings but SMS templates are not implemented.

**Gap:**
- Only one channel (email). GovStack Messaging expects multi-channel.
- No delivery receipt / status callback model.
- No `govstack_views.py`.

**What the previous table said:** 🟡 Partial — accurate, but for CivicOS reasons not GovStack reasons.
**Path to GovStack certification:** Add SMS via GC Notify (key already wired), implement delivery receipts, then build GovStack API layer.

---

### 6. eServices / Forms BB — `bb-eservices` ⚠️ Partial Overlap

**GovStack spec:** No dedicated `bb-forms`; forms/e-services are covered under `bb-eservices` (Service Delivery)
**GovStack scope:** Programmatic form definitions, dynamic field rendering, submission lifecycle, integration with backend registries and workflows.

**CivicOS app:** `apps/forms/`
**CivicOS scope:** Wagtail StreamField-coupled form builder. Forms are defined via CMS pages, not via a standalone API. No programmatic form creation endpoint.

**Gap:**
- Forms are tightly CMS-coupled (Wagtail page). GovStack expects a standalone form engine with API-first creation.
- No `govstack_views.py`.

**Path to GovStack certification:** Decouple form definitions from Wagtail pages into a standalone model/API, then build GovStack layer.

---

### 7. Identity BB — `bb-identity` ❌ Domain Mismatch

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-identity`
**GovStack scope:** National digital identity: MOSIP enrollment, biometric verification, OpenID Connect identity provider (IdP), identity deduplication, foundational ID issuance.

**CivicOS app:** `apps/auth_extension/`
**CivicOS scope:** Django `AbstractUser` extended with MFA, JWT tokens, email/password login, citizen self-registration. Standard web auth, not a national IdP.

**Why these are different domains:** GovStack Identity BB makes CivicOS an IdP that *issues* national digital identities. CivicOS `auth_extension` is an authentication layer that *consumes* an identity (email + password). These are not extensible to each other — they serve different roles in an identity ecosystem.

**Correct GovStack relationship:** CivicOS would be a *relying party* (RP) that integrates with an external MOSIP IdP via OIDC. CivicOS would implement `bb-identity` by connecting to MOSIP, not by extending `auth_extension`.

**What the previous table said:** "Limited scope, JWT only" — understates how far off this is.
**What it should have said:** 🔴 Not started (wrong role; this is an RP, not an IdP)

---

### 8. CMS BB — `bb-cms` ✅ Correct Domain, No GovStack Layer

**GovStack spec:** `github.com/GovStackWorkingGroup/bb-cms` (nascent spec, September 2025, 0 forks)
**GovStack scope:** Government content management — pages, news, service descriptions, multilingual content.

**CivicOS app:** `apps/cms/`
**CivicOS scope:** Wagtail pages (HomePage, ServicePage, NewsPage, GenericPage), bilingual EN/FR, Wagtail StreamFields.

**Domain match:** Strong. Both are about publishing multilingual government information pages.

**Gap:**
- 0 tests — regression risk for any GovStack layer work
- No `govstack_views.py`
- The GovStack spec itself is very new and lightly defined (0 forks, low activity)

**What the previous table said:** 🟡 Partial (no tests)
**Recommendation:** Write tests before touching this app for any reason. The GovStack bb-cms spec is immature — lower priority than Scheduler or Messaging.

---

## What Was Right in the Previous Table

| Entry | Previous assessment | Verdict |
|---|---|---|
| Consent BB as "In Progress" | ✅ Correct | It was the only one actually in progress |
| Volunteers as N/A or CivicOS-specific | ✅ Correct | No GovStack BB exists for volunteers |
| Reports as N/A | ✅ Correct | No GovStack BB exists for analytics/reporting |
| Backoffice as N/A | ✅ Correct | CivicOS-specific module |
| Workflows as N/A | ✅ Correct | CivicOS-specific module |
| CMS as 🟡 (no tests) | ✅ Correct | Accurately flagged the test coverage gap |

---

## What Was Wrong in the Previous Table

| Entry | Previous claim | Correct claim | Why it was wrong |
|---|---|---|---|
| Payments BB | ✅ Ready | 🔴 Not started (wrong domain) | Measured Stripe/CAD test suite against a G2P/Mojaloop spec |
| Documents BB | ✅ Ready | 🔴 Not started (misaligned spec) | Measured S3/PIPEDA storage against national civil registry spec |
| Identity BB | "Limited scope" (implied close) | 🔴 Not started (wrong role) | CivicOS is an RP, not an IdP; fundamentally different |
| Appointments BB | 🟡 Partial | 🔴 Not started for GovStack (but best candidate) | "Partial" was about internal wave completion, not GovStack status |
| Overall framing | "GovStack BB status" | "CivicOS internal status" | The table measured internal production readiness, not certifiability |

---

## Evidence: Only One GovStack API Layer Exists

Confirmed via direct filesystem check (2026-07-17):

```bash
$ find apps -name "govstack_views.py" -o -name "govstack_urls.py"
apps/consent/govstack_views.py
apps/consent/govstack_urls.py
```

Two files. Both in `apps/consent/`. Every other app has zero GovStack-facing API surface.

---

## Recommended Certification Roadmap

### Phase 1 — Complete (awaiting result)
**Consent BB** (`bb-consent`)
- ✅ `govstack_views.py` + `govstack_urls.py` built
- ✅ Rounds 1–3 Codex audit fixes applied
- ✅ Submitted to `testing.govstack.global`
- ⏳ Awaiting harness result
- 📋 Remaining: H-03 (serializedSnapshot type), M-08 (pagination envelopes)

### Phase 2 — Next sprint
**Scheduler BB** (`bb-scheduler`) — `apps/appointments/` → add `govstack_views.py`
- Best domain match of all remaining BBs
- Business logic already implemented (slots, bookings, policies, waves 1–4)
- No new domain knowledge required — only GovStack API surface
- Estimated effort: 1–2 sprints (views + URL conf + Gherkin fixtures)

### Phase 3 — After Scheduler
**File Management BB** (`bb-file-management`) — `apps/documents/` → add `govstack_views.py`
- Closer to CivicOS Documents than `bb-digital-registries`
- S3 backend already in place
- Estimated effort: 2–3 sprints (API surface + access control alignment)

### Phase 4 — After GC Notify SMS is added
**Messaging BB** (`bb-messaging`) — `apps/notifications/` → add SMS + `govstack_views.py`
- GC Notify API key already wired in settings
- Need to implement SMS templates first
- Estimated effort: 3 sprints (SMS + delivery receipts + GovStack layer)

### Phase 5 — Longer term / optional
**Payments BB** (`bb-payments`) — new G2P layer, parallel to `apps/payments/`
- Requires building a new domain from scratch (G2P, Mojaloop, mobile money)
- CivicOS Payments (Stripe/fees) should stay untouched — serve different purposes
- Estimated effort: 6+ sprints, external expertise recommended

**Identity BB** (`bb-identity`) — MOSIP OIDC integration
- CivicOS becomes a relying party connecting to a MOSIP IdP
- Does not modify `apps/auth_extension/` — adds an OIDC login path
- Only relevant if deploying in a jurisdiction with national digital ID

**CMS BB** (`bb-cms`) — low priority
- GovStack bb-cms spec is nascent (September 2025, 0 community forks)
- Write tests first, then evaluate spec maturity before investing

---

## File Audit: GovStack-Specific Files

| File | Purpose | Status |
|---|---|---|
| `apps/consent/govstack_views.py` | GovStack Consent BB API views | ✅ Exists |
| `apps/consent/govstack_urls.py` | GovStack Consent BB URL routing | ✅ Exists |
| `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md` | Reusable 10-dimension assessment prompt | ✅ Exists (root) |
| `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` | This file — corrected readiness record | ✅ Exists (root) |
| `CONSENT-SUBMISSION-CHECKLIST.md` | Pre-submission checklist for Consent harness | ✅ Exists (root) |
| `GovStack_Consent_BB_Self_Assessment_v2.docx` | Self-assessment submitted with harness | ✅ Exists (root) |
| `CivicOS_GovStack_Compliance_Report.docx` | Compliance report | ✅ Exists (root) |
| `SPEC_APPOINTMENTS_BB.md` | Appointments BB spec notes | ✅ Exists (root) |
| `SPEC_DOCUMENT_MANAGEMENT_BB.md` | Document Management BB spec notes | ✅ Exists (root) |
| `payments_bb_spec.docx` | GovStack Payments BB spec notes | ✅ Exists (root) |

---

## BB Status Tier Definitions

For future assessments, use these four tiers **for GovStack certification status only**:

| Tier | Symbol | Meaning |
|---|---|---|
| Not Started | 🔴 | No `govstack_views.py` / `govstack_urls.py` exists |
| In Progress | 🟠 | GovStack API layer partially built; not submitted |
| Harness Submitted | 🔵 | Submitted to `testing.govstack.global`; awaiting result |
| Certified | 🟢 | Harness passed; GovStack certification issued |

These are **separate** from CivicOS internal readiness tiers (which measure test coverage, deployment readiness, feature completeness for the municipal use case).

---

## How to Use This Document

When Codex or any tool asks "is [X] BB ready?":
1. Check the **GovStack BB Status** column, not the **CivicOS Status** column.
2. If GovStack BB Status is 🔴, the answer is "not started" regardless of how many tests the CivicOS app has.
3. To advance a BB from 🔴 to 🟠: read the GovStack spec repo at `github.com/GovStackWorkingGroup/bb-<name>`, implement `govstack_views.py` + `govstack_urls.py` against the OpenAPI, write Gherkin fixtures.
4. Use `GOVSTACK_BB_READINESS_ASSESSMENT_PROMPT.md` to run an honest assessment before claiming any tier upgrade.

---

*Assessment performed 2026-07-17 via direct codebase inspection + GovStack spec repo review. Supersedes readiness claims in `docs/PROJECT_OVERVIEW_AND_STATUS.md` for GovStack certification purposes.*
