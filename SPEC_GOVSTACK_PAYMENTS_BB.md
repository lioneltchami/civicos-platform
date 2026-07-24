# GovStack Payments Building Block — Implementation Specification

**Version:** 1.0.0
**Date:** 2026-07-19
**Status:** Approved for Implementation
**Author:** CivicOS Architecture Team
**GovStack spec source:** `github.com/GovStackWorkingGroup/bb-payments` (main branch)
**Harness features:** 9 Gherkin feature files under `test/openAPI/features/`
**Predecessor spec:** `payments_bb_spec.docx` (CivicOS internal Payments BB — unchanged, runs in parallel)

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Critical Architecture Decision — Two Parallel Layers](#2-critical-architecture-decision--two-parallel-layers)
3. [GovStack Payments BB API Categories](#3-govstack-payments-bb-api-categories)
4. [Cross-Cutting Requirements](#4-cross-cutting-requirements)
5. [Data Models](#5-data-models)
6. [State Machines](#6-state-machines)
7. [Error Code Reference](#7-error-code-reference)
8. [Authentication and Authorization](#8-authentication-and-authorization)
9. [Service Layer Architecture](#9-service-layer-architecture)
10. [Wave 1 — Foundation and Scaffolding](#10-wave-1--foundation-and-scaffolding)
11. [Wave 2 — G2P Beneficiary Management (2 harness features)](#11-wave-2--g2p-beneficiary-management-2-harness-features)
12. [Wave 3 — G2P Bulk Disbursement (2 harness features)](#12-wave-3--g2p-bulk-disbursement-2-harness-features)
13. [Wave 4 — Voucher Engine (5 harness features)](#13-wave-4--voucher-engine-5-harness-features)
14. [Wave 5 — P2G Bill Payments (adapter over existing Payments)](#14-wave-5--p2g-bill-payments-adapter-over-existing-payments)
15. [URL Structure — Complete Reference](#15-url-structure--complete-reference)
16. [Admin Interface](#16-admin-interface)
17. [Audit Trail Requirements](#17-audit-trail-requirements)
18. [Test Requirements](#18-test-requirements)
19. [Migrations](#19-migrations)
20. [File Structure](#20-file-structure)
21. [Implementation Order and Definition of Done per Wave](#21-implementation-order-and-definition-of-done-per-wave)

---

## 1. Purpose and Scope

### 1.1 What This Spec Covers

This document specifies the **GovStack Payments Building Block API layer** for CivicOS — a parallel REST API surface that implements the GovStack `bb-payments` OpenAPI specifications so that CivicOS can be certified by the GovStack test harness at `testing.govstack.global`.

This is NOT a rewrite of the existing CivicOS Payments app (`apps/payments/`). The existing Stripe-based fee collection and CRA donation receipt system is production-ready and must not be touched. This spec adds a **new GovStack-facing API layer** on top of new models, with a separate URL namespace.

### 1.2 What GovStack Payments BB Is

The GovStack Payments BB (authored by ITU, GSMA, World Bank, MIFOS Initiative) enables:

- **G2P (Government to Person)** — Social benefit disbursements, salary payments, conditional/unconditional cash transfers to beneficiaries
- **Voucher Management** — Government-issued digital vouchers for goods/services redemption by beneficiaries
- **P2G (Person to Government)** — Citizens paying government bills via mobile money
- **G2B (Government to Business)** — Tax refunds, contract payments, subsidies
- **B2G (Business to Government)** — Business paying taxes and government fees

The harness tests only G2P + Voucher (9 features). P2G, G2B, and B2G are implemented in Wave 5 but not yet tested by the harness.

### 1.3 What Is Out of Scope

- Any modification to `apps/payments/` (Stripe, donations, CRA receipts) — those are untouched
- A real mobile money integration (Mojaloop, MTN, Airtel) — the GovStack layer stores and processes data; money movement is out of scope for certification
- Real bank account validation — `FinancialAddress` is stored but not verified against a live banking API
- G2B and B2G harness testing — modelled but not submitted to harness in this phase
- G2G (Government to Government) — GovStack spec explicitly defers this

---

## 2. Critical Architecture Decision — Two Parallel Layers

### 2.1 The Separation Rule

```
apps/payments/
├── models.py             ← EXISTING: Stripe, PaymentIntent, Donations, Receipts. DO NOT TOUCH.
├── views/                ← EXISTING: Stripe-facing views. DO NOT TOUCH.
├── urls.py               ← EXISTING: Internal CivicOS URLs. DO NOT TOUCH.
│
├── govstack_models.py    ← NEW: GovStack-specific models only
├── govstack_views.py     ← NEW: GovStack API views (DRF APIView)
├── govstack_urls.py      ← NEW: GovStack URL routing
├── govstack_services.py  ← NEW: GovStack business logic service layer
├── govstack_serializers.py ← NEW: DRF serializers for GovStack request/response shapes
├── govstack_auth.py      ← NEW: BB-to-BB authentication (header-based)
└── govstack_exceptions.py ← NEW: Custom HTTP error codes (452, 453, 454, etc.)
```

### 2.2 URL Namespaces

| Layer | URL prefix | Namespace |
|---|---|---|
| CivicOS internal | `/payments/` | `payments` |
| GovStack BB | `/govstack/payments/` | `govstack_payments` |

These never overlap. The existing `payments` namespace is unchanged.

### 2.3 Why Not Put GovStack Models in `models.py`

The existing `models.py` carries the comment `"Currency always CAD"` as a hard invariant. GovStack Payments must support any ISO 4217 currency (harness uses USD, AED). Keeping GovStack models in a separate file makes the constraint boundary explicit and prevents accidental contamination.

---

## 3. GovStack Payments BB API Categories

The spec organizes APIs into five categories. Each has its own set of YAML specs in the `bb-payments` repo:

### 3.1 G2P — Government to Person (Harness: features 1–4)

| Feature | Method | Endpoint | Harness file |
|---|---|---|---|
| Register Beneficiary | POST | `/govstack/payments/register-beneficiary` | `g2p_register_beneficiary.feature` |
| Update Beneficiary | POST | `/govstack/payments/update-beneficiary-details` | `g2p_update_beneficiary_details.feature` |
| Bulk Payment | POST | `/govstack/payments/bulk-payment` | `g2p_bulk_payment.feature` |
| Prepayment Validation | POST | `/govstack/payments/prepayment-validation` | `g2p_prepayment_validation.feature` |
| Prepayment Validation Response | POST | `/govstack/payments/prepayment-validation-response` | *(called by harness as step 2)* |

### 3.2 Voucher Management (Harness: features 5–9)

| Feature | Method | Endpoint | Harness file |
|---|---|---|---|
| Voucher Pre-activation | POST | `/govstack/payments/vouchers/voucher_preactivation` | `voucher_preactivation.feature` |
| Voucher Activation | PATCH | `/govstack/payments/vouchers/voucher_activation` | `voucher_activation.feature` |
| Voucher Redemption | POST | `/govstack/payments/vouchers/voucher_redemption` | `voucher_redemption.feature` |
| Voucher Cancellation | PATCH | `/govstack/payments/vouchers/voucherstatuscheck/{serial}` | `voucher_cancelation.feature` |
| Voucher Status Check | GET | `/govstack/payments/vouchers/voucherstatuscheck/{serial}` | `voucher_status_check.feature` |

> **Note on URL:** The harness uses the path `/vouchers/voucherstatuscheck/{voucherserialnumber}` for BOTH the status check (GET) and the cancellation (PATCH). This is correct — both operations target the same resource URL, different HTTP methods.

### 3.3 P2G — Person to Government (Wave 5, no harness yet)

| API | Method | Endpoint |
|---|---|---|
| Bill Inquiry | GET | `/govstack/payments/bills/{billId}` |
| Bill Payment Notification | POST | `/govstack/payments/billTransferRequests` |
| Mark Bill Paid | POST | `/govstack/payments/bills/{billId}/mark-paid` |
| RTP Status Update | GET | `/govstack/payments/transferRequests/{transferRequestId}` |

### 3.4 G2B and B2G (Wave 5, no harness yet)

Not specified in detail in this document. Follow the bb-payments spec sections 8.4 and 8.5 when implementing.

---

## 4. Cross-Cutting Requirements

These apply to ALL GovStack Payments endpoints without exception.

### 4.1 Response Envelope

**G2P endpoints** (Beneficiary, Bulk Payment, Prepayment Validation) always return:

```json
{
  "ResponseCode": "00",
  "ResponseDescription": "Successful",
  "RequestID": "<echo of incoming RequestID>"
}
```

- `ResponseCode: "00"` = success
- `ResponseCode: "01"` = failure (validation error, not found, etc.)
- `RequestID` is always echoed back from the request body, even on errors
- HTTP status is always `200` for both success and validation failure on G2P endpoints
- HTTP status is `400` only for malformed/missing required fields

**Voucher endpoints** return their own shapes (see Wave 4 section).

### 4.2 Required Request Headers (BB-to-BB Auth)

Different endpoints require different headers. The full reference:

| Header | Required on | Purpose |
|---|---|---|
| `X-Callback-URL` | Register Beneficiary, Update Beneficiary, Voucher Preactivation, Voucher Activation | URL to POST async result to |
| `X-Registering-Institution-ID` | Register Beneficiary, Update Beneficiary | Source ministry/org ID |
| `X-CorrelationID` | Bulk Payment, Prepayment Validation, P2G Bill | Globally unique request ID |
| `X-Platform-TenantId` | P2G Bill Transfer | Tenant scoping |
| `X-PayerFI-Id` | P2G Bill Transfer | Payer financial institution ID |
| `X-Registering-Institution-Id` | Voucher endpoints | Issuing agency ID |
| `X-Channel` | Voucher (optional) | Channel identifier |
| `X-Date` | Voucher (optional) | Request date |
| `Authorization: Bearer <jwt>` | Voucher Redemption, Voucher Status | Standard JWT |

### 4.3 Async Callback Pattern

Several G2P endpoints are asynchronous:
1. Client POSTs request with `X-Callback-URL` header
2. CivicOS returns `200` immediately with `ResponseCode: "00"` and echoed `RequestID`
3. CivicOS processes asynchronously (Celery task)
4. CivicOS POSTs result to `X-Callback-URL`

For the test harness, both the initial response AND the callback are tested (the harness hosts its own callback receiver). CivicOS must implement the outbound callback POST via Celery.

### 4.4 Currency

GovStack Payments is multi-currency. The GovStack layer:
- Accepts any ISO 4217 3-letter currency code (`USD`, `AED`, `CAD`, `EUR`, etc.)
- Validates that the currency code is exactly 3 uppercase letters
- Does NOT do currency conversion
- Stores currency as-is
- The `"Currency always CAD"` invariant in `apps/payments/models.py` applies only to the CivicOS internal layer — never to govstack models

### 4.5 Content-Type

All GovStack endpoints return `Content-Type: application/json`. The harness checks this header on every response.

### 4.6 Response Timing

The harness sets a 15,000 ms timeout on all endpoints. Every endpoint must respond within 15 seconds. Celery callbacks must be dispatched within the timeout window (though delivery to the callback URL may be slightly later).

### 4.7 No PII in Logs

`FinancialAddress` (bank account / mobile money number), `PayeeFunctionalID`, and `X-Registering-Institution-ID` must never appear in log lines. Log only `pk`, `batch_id`, and `request_id` values.

---

## 5. Data Models

All GovStack models live in `apps/payments/govstack_models.py`. They inherit from `TimestampedModel`.

### 5.1 `GovStackBeneficiary`

Maps a beneficiary's functional identity to their payment address (bank account, mobile money wallet, etc.). This is the "ID Mapper" concept in GovStack.

```python
class GovStackBeneficiary(TimestampedModel):
    """
    ID Mapper entry for a G2P beneficiary.
    Maps PayeeFunctionalID (government-assigned) to FinancialAddress (payment instrument).

    Security invariants:
    - financial_address is Fernet-encrypted at rest (EncryptedCharField)
    - financial_address is NEVER in any log line
    - payee_functional_id is NEVER in any log line
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    payee_functional_id = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Government-assigned functional ID for this beneficiary. "
                  "Max 20 chars per GovStack spec. Never log this value.",
    )
    payment_modality = models.CharField(
        max_length=2,
        blank=True,
        help_text="Two-digit code: 01=bank, 02=mobile money, 03=voucher, etc.",
    )
    financial_address = EncryptedCharField(
        max_length=512,
        blank=True,
        help_text="Bank account / IBAN / mobile money number. Fernet-encrypted. "
                  "Max 30 chars in spec but encrypted storage is longer.",
    )
    source_bb_id = models.CharField(
        max_length=20,
        db_index=True,
        help_text="SourceBBID of the registering BB (from request body).",
    )
    registering_institution_id = models.CharField(
        max_length=20,
        blank=True,
        help_text="X-Registering-Institution-ID header value at registration time.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "GovStack Beneficiary"
        verbose_name_plural = "GovStack Beneficiaries"
        indexes = [models.Index(fields=["source_bb_id", "payee_functional_id"])]

    def __str__(self):
        return f"Beneficiary {self.pk}"  # NEVER include payee_functional_id here
```

### 5.2 `BulkPaymentBatch`

Represents a single batch of credit instructions handed over from a Source BB.

```python
class BulkPaymentBatch(TimestampedModel):
    """
    A batch of credit instructions from a Source BB to the Payments BB.
    Tracks processing status across all credit instructions in the batch.
    """
    STATUS_RECEIVED = "received"
    STATUS_VALIDATING = "validating"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_VALIDATING, "Validating"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_PARTIAL, "Partially Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.CharField(max_length=16, db_index=True,
        help_text="RequestID from Source BB. Max 16 chars.")
    source_bb_id = models.CharField(max_length=20, db_index=True,
        help_text="SourceBBID. Pattern: [a-zA-Z0-9]{10}.")
    batch_id = models.CharField(max_length=20, unique=True, db_index=True,
        help_text="BatchID from Source BB. Max 20 chars. Must be unique.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
        default=STATUS_RECEIVED)
    callback_url = models.URLField(max_length=500, blank=True,
        help_text="X-Callback-URL from request header.")
    correlation_id = models.CharField(max_length=12, blank=True,
        help_text="X-CorrelationID from request header.")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2,
        null=True, blank=True)
    completed_amount = models.DecimalField(max_digits=14, decimal_places=2,
        default=Decimal("0.00"))
    failed_amount = models.DecimalField(max_digits=14, decimal_places=2,
        default=Decimal("0.00"))
    result_generated_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Bulk Payment Batch"
        verbose_name_plural = "Bulk Payment Batches"

    def __str__(self):
        return f"Batch {self.batch_id} [{self.status}]"
```

### 5.3 `CreditInstruction`

One line item within a `BulkPaymentBatch`.

```python
class CreditInstruction(TimestampedModel):
    """
    A single credit instruction within a BulkPaymentBatch.
    """
    STATUS_PENDING = "pending"
    STATUS_VALIDATED = "validated"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_VALIDATED, "Validated"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(BulkPaymentBatch, on_delete=models.PROTECT,
        related_name="instructions")
    instruction_id = models.CharField(max_length=16, db_index=True,
        help_text="InstructionID. Max 16 chars. Unique within batch.")
    payee_functional_id = models.CharField(max_length=20,
        help_text="Maps to GovStackBeneficiary.payee_functional_id. Never log.")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3,
        help_text="ISO 4217 currency code. 3 uppercase letters.")
    narration = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
        default=STATUS_PENDING)
    failure_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Credit Instruction"
        verbose_name_plural = "Credit Instructions"
        unique_together = [("batch", "instruction_id")]
        indexes = [models.Index(fields=["batch", "status"])]
```

### 5.4 `PrepaymentValidationRequest`

Tracks a prepayment validation (account lookup) request and its async result.

```python
class PrepaymentValidationRequest(TimestampedModel):
    """
    Pre-payment validation request. Validates PayeeFunctionalIDs against
    the ID Mapper before bulk disbursement.
    """
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.CharField(max_length=16, unique=True, db_index=True)
    source_bb_id = models.CharField(max_length=20)
    batch_id = models.CharField(max_length=20, db_index=True)
    instruction_id = models.CharField(max_length=20)
    payee_functional_id = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    narration = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=[
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ], default=STATUS_PENDING)
    beneficiary_found = models.BooleanField(null=True)
    financial_address_valid = models.BooleanField(null=True)
    callback_url = models.URLField(max_length=500, blank=True)

    class Meta:
        verbose_name = "Prepayment Validation Request"
```

### 5.5 `GovStackVoucher`

The central voucher model. One row per voucher serial number.

```python
class GovStackVoucher(TimestampedModel):
    """
    A GovStack digital voucher for goods/services redemption.

    Status state machine:
      NOT_PREACTIVATED → PREACTIVATED → ACTIVATED → CONSUMED
                                      ↘ CANCELLED
                       ↘ CANCELLED (before activation)

    Security invariants:
    - voucher_secret is EncryptedCharField — NEVER in any HTTP response or log
    - serial_number is the public identifier
    """
    STATUS_NOT_PREACTIVATED = "not_preactivated"
    STATUS_PREACTIVATED = "preactivated"
    STATUS_ACTIVATED = "activated"
    STATUS_CONSUMED = "consumed"
    STATUS_BLOCKED = "blocked"
    STATUS_SUSPENDED = "suspended"
    STATUS_CANCELLED = "cancelled"
    STATUS_PURGED = "purged"

    STATUS_CHOICES = [
        (STATUS_NOT_PREACTIVATED, "Not Preactivated"),
        (STATUS_PREACTIVATED, "Preactivated"),
        (STATUS_ACTIVATED, "Activated"),
        (STATUS_CONSUMED, "Consumed"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_PURGED, "Purged"),
    ]

    # Integer status codes for the GET /voucherstatuscheck response
    STATUS_INT_MAP = {
        STATUS_NOT_PREACTIVATED: 0,
        STATUS_PREACTIVATED: 1,
        STATUS_ACTIVATED: 2,
        STATUS_CONSUMED: 3,
        STATUS_BLOCKED: 4,
        STATUS_SUSPENDED: 5,
        STATUS_CANCELLED: 6,
        STATUS_PURGED: 7,
    }
    STATUS_ERROR_INT = 9

    ALLOWED_TRANSITIONS = {
        STATUS_NOT_PREACTIVATED: [STATUS_PREACTIVATED],
        STATUS_PREACTIVATED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_ACTIVATED: [STATUS_CONSUMED, STATUS_BLOCKED, STATUS_SUSPENDED, STATUS_CANCELLED],
        STATUS_BLOCKED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_SUSPENDED: [STATUS_ACTIVATED, STATUS_CANCELLED],
        STATUS_CONSUMED: [],
        STATUS_CANCELLED: [],
        STATUS_PURGED: [],
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    serial_number = models.CharField(max_length=20, unique=True, db_index=True,
        help_text="Public voucher serial number. Assigned on pre-activation.")
    voucher_secret = EncryptedCharField(max_length=512, blank=True,
        help_text="Secret number for redemption validation. Encrypted. "
                  "NEVER in any HTTP response body or log.")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3,
        help_text="ISO 4217 currency code.")
    group_code = models.CharField(max_length=50,
        help_text="Voucher group / program code.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
        default=STATUS_PREACTIVATED)
    issuing_bb = models.CharField(max_length=50,
        help_text="Gov_Stack_BB that requested pre-activation.")
    registering_institution_id = models.CharField(max_length=20, blank=True)
    batch_id = models.CharField(max_length=12, blank=True)
    payee_functional_id = models.CharField(max_length=20, blank=True)
    callback_url = models.URLField(max_length=500, blank=True)

    # Redemption fields (populated on CONSUMED transition)
    redeemed_by_agent_id = models.CharField(max_length=10, blank=True)
    redeemed_merchant_name = models.CharField(max_length=200, blank=True)
    redeemed_merchant_bank_details = models.CharField(max_length=200, blank=True)
    redeemed_merchant_voucher_group = models.CharField(max_length=100, blank=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)
    redemption_transaction_id = models.CharField(max_length=12, blank=True)

    class Meta:
        verbose_name = "GovStack Voucher"
        verbose_name_plural = "GovStack Vouchers"
        indexes = [
            models.Index(fields=["status", "group_code"]),
            models.Index(fields=["issuing_bb", "status"]),
        ]

    def __str__(self):
        return f"Voucher {self.serial_number} [{self.status}]"

    def transition_to(self, new_status: str) -> None:
        """Validate and apply a status transition. Raises ValueError if invalid."""
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {self.status} → {new_status}. "
                f"Allowed from {self.status}: {allowed}"
            )
        self.status = new_status

    @property
    def status_int(self) -> int:
        return self.STATUS_INT_MAP.get(self.status, self.STATUS_ERROR_INT)
```

### 5.6 `GovStackPaymentAuditEntry`

Append-only audit log for all GovStack Payments events.

```python
class GovStackPaymentAuditEntry(TimestampedModel):
    """
    Append-only audit log for all GovStack Payments BB events.
    delete() and save() after creation are blocked.
    """
    ACTION_BENEFICIARY_REGISTERED = "beneficiary_registered"
    ACTION_BENEFICIARY_UPDATED = "beneficiary_updated"
    ACTION_BATCH_RECEIVED = "batch_received"
    ACTION_BATCH_COMPLETED = "batch_completed"
    ACTION_BATCH_FAILED = "batch_failed"
    ACTION_INSTRUCTION_COMPLETED = "instruction_completed"
    ACTION_INSTRUCTION_FAILED = "instruction_failed"
    ACTION_VALIDATION_REQUESTED = "validation_requested"
    ACTION_VALIDATION_COMPLETED = "validation_completed"
    ACTION_VOUCHER_PREACTIVATED = "voucher_preactivated"
    ACTION_VOUCHER_ACTIVATED = "voucher_activated"
    ACTION_VOUCHER_REDEEMED = "voucher_redeemed"
    ACTION_VOUCHER_CANCELLED = "voucher_cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=50, db_index=True)
    actor_bb_id = models.CharField(max_length=50, blank=True,
        help_text="SourceBBID or Gov_Stack_BB that triggered the action.")
    object_type = models.CharField(max_length=50,
        help_text="e.g. 'beneficiary', 'batch', 'voucher'")
    object_pk = models.CharField(max_length=100,
        help_text="PK of the affected object. Never include PII.")
    request_id = models.CharField(max_length=20, blank=True,
        help_text="RequestID echoed from the triggering request.")
    details = models.JSONField(default=dict,
        help_text="Non-PII metadata about the action.")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "GovStack Payment Audit Entry"
        verbose_name_plural = "GovStack Payment Audit Entries"
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("GovStackPaymentAuditEntry is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("GovStackPaymentAuditEntry records cannot be deleted.")
```

---

## 6. State Machines

### 6.1 Voucher Status State Machine

```
                    POST /voucher_preactivation
                              │
                              ▼
                       [PREACTIVATED]
                        ╱          ╲
         PATCH /voucher_activation  PATCH /voucherstatuscheck (cancel)
                      ╱              ╲
                     ▼                ▼
               [ACTIVATED]       [CANCELLED]  ←── terminal
               ╱    │   ╲
              ╱     │    ╲
             ▼      ▼     ▼
        [BLOCKED] [SUSPENDED] [CONSUMED] ←── terminal
             │       │
             ▼       ▼
         [ACTIVATED] (re-activate from blocked/suspended)
             │
             ▼ (cancel from activated)
        [CANCELLED]
```

Key rules:
- `CONSUMED` and `CANCELLED` and `PURGED` are terminal — no transitions allowed out
- `BLOCKED` and `SUSPENDED` can be re-activated
- Attempting to cancel an already-cancelled voucher returns HTTP `464` (not `200`)

### 6.2 Bulk Payment Batch State Machine

```
RECEIVED → VALIDATING → PROCESSING → COMPLETED
                │                  ↘ PARTIAL (some failed)
                ▼
            FAILED (validation catastrophic failure)
```

---

## 7. Error Code Reference

The GovStack Payments BB uses non-standard HTTP status codes for domain-specific errors. These are used only on Voucher endpoints.

| Code | Meaning | Endpoint |
|---|---|---|
| `400` | Missing required fields / malformed request | All |
| `452` | Invalid voucher amount | Voucher preactivation |
| `453` | Invalid voucher currency | Voucher preactivation |
| `454` | Invalid voucher group | Voucher preactivation |
| `456` | Invalid voucher serial number (not found) | Voucher activation |
| `460` | Gov_Stack_BB does not exist / not authorized | Voucher preactivation, activation, redemption |
| `463` | Invalid serial number for cancellation | Voucher cancellation |
| `464` | Voucher already cancelled (idempotent double-cancel) | Voucher cancellation |

### 7.1 Error Response Shape

For error codes 452–464 the response body must be:

```json
{
  "message": "<human-readable explanation>"
}
```

The harness checks that a `"message"` property exists on all error responses.

### 7.2 Implementing Custom Error Codes

Create `apps/payments/govstack_exceptions.py`:

```python
from rest_framework.exceptions import APIException
from rest_framework import status as http_status

class InvalidVoucherAmount(APIException):
    status_code = 452
    default_code = "invalid_voucher_amount"
    default_detail = "Invalid voucher amount."

class InvalidVoucherCurrency(APIException):
    status_code = 453
    default_code = "invalid_voucher_currency"
    default_detail = "Invalid or unsupported voucher currency."

class InvalidVoucherGroup(APIException):
    status_code = 454
    default_code = "invalid_voucher_group"
    default_detail = "Voucher group does not exist."

class InvalidVoucherSerial(APIException):
    status_code = 456
    default_code = "invalid_voucher_serial"
    default_detail = "Voucher serial number not found."

class GovStackBBNotFound(APIException):
    status_code = 460
    default_code = "gov_stack_bb_not_found"
    default_detail = "The specified Gov_Stack_BB does not exist."

class InvalidCancellationSerial(APIException):
    status_code = 463
    default_code = "invalid_cancellation_serial"
    default_detail = "Invalid voucher serial number for cancellation."

class VoucherAlreadyCancelled(APIException):
    status_code = 464
    default_code = "voucher_already_cancelled"
    default_detail = "This voucher has already been cancelled."
```

In `govstack_views.py`, add a custom exception handler:

```python
from rest_framework.views import exception_handler as drf_exception_handler

def govstack_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        # Normalize to {"message": "..."} shape for all GovStack errors
        detail = response.data
        if isinstance(detail, dict) and "detail" in detail:
            response.data = {"message": str(detail["detail"])}
        elif isinstance(detail, str):
            response.data = {"message": detail}
        elif isinstance(detail, list):
            response.data = {"message": " ".join(str(d) for d in detail)}
    return response
```

Register this handler in settings for the GovStack views namespace.

---

## 8. Authentication and Authorization

### 8.1 G2P Endpoints (Beneficiary, Bulk Payment, Prepayment Validation)

These are BB-to-BB APIs. The caller is another GovStack building block (Registry BB, Information Mediator BB), not a citizen or staff user.

Authentication: **Header-based API key** using `X-Registering-Institution-ID` or `X-CorrelationID`.

For the harness, these endpoints must be publicly accessible (no JWT required). Create a custom DRF permission:

```python
# govstack_auth.py
from rest_framework.permissions import BasePermission

class IsTrustedSourceBB(BasePermission):
    """
    Validates that the caller is a known Source BB.
    For harness testing: passes if X-Registering-Institution-ID header is present.
    For production: validate against a whitelist of registered Source BB IDs.
    """
    def has_permission(self, request, view):
        institution_id = request.headers.get("X-Registering-Institution-ID", "")
        if not institution_id:
            return False
        # Harness uses any non-empty value — validate format only
        # Production would check against GovStackRegisteredBB table
        return len(institution_id) <= 20

class AllowAnyBB(BasePermission):
    """For endpoints that accept any BB caller (bulk payment, vouchers)."""
    def has_permission(self, request, view):
        return True  # Auth validated by Gov_Stack_BB field in request body
```

### 8.2 Voucher Endpoints

Voucher Redemption and Voucher Status require JWT Bearer authentication (as per the spec's `bearerAuth` security scheme). Use CivicOS's existing JWT authentication.

Voucher Preactivation and Activation use header-based auth (`X-Registering-Institution-Id`).

### 8.3 Rate Limiting

Apply DRF throttling to all GovStack endpoints. Use a separate throttle scope:

```python
# In settings
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["govstack_bb"] = "100/minute"
```

---

## 9. Service Layer Architecture

All business logic goes in `apps/payments/govstack_services.py`. Views call services; services never call each other across the G2P/Voucher boundary.

### 9.1 Service Method Signatures

```python
class GovStackBeneficiaryService:
    @staticmethod
    def register(request_id, source_bb_id, payee_functional_id,
                 payment_modality="", financial_address="",
                 registering_institution_id="", callback_url=""):
        """Register or update a beneficiary. Idempotent on payee_functional_id."""

    @staticmethod
    def update(request_id, source_bb_id, payee_functional_id,
               payment_modality=None, financial_address=None):
        """Update payment_modality and/or financial_address for existing beneficiary."""


class GovStackBulkPaymentService:
    @staticmethod
    def receive_batch(request_id, source_bb_id, batch_id, instructions,
                      callback_url="", correlation_id=""):
        """Accept a batch, create BulkPaymentBatch + CreditInstruction rows.
        Returns immediately. Enqueues process_bulk_payment_batch Celery task."""

    @staticmethod
    def validate_prepayment(request_id, source_bb_id, batch_id, instruction_id,
                            payee_functional_id, amount, currency, narration="",
                            callback_url=""):
        """Validate a single PayeeFunctionalID against the ID Mapper.
        Returns immediately. Enqueues validate_prepayment_async Celery task."""


class GovStackVoucherService:
    @staticmethod
    def preactivate(voucher_amount, voucher_currency, voucher_group, issuing_bb,
                    registering_institution_id="", batch_id="",
                    payee_functional_id="", callback_url=""):
        """Create a GovStackVoucher in PREACTIVATED status.
        Generates serial_number and voucher_secret.
        Returns the voucher object."""

    @staticmethod
    def activate(voucher_serial_number, issuing_bb):
        """Transition PREACTIVATED → ACTIVATED. Returns voucher."""

    @staticmethod
    def redeem(voucher_number, issuing_bb, merchant_name, merchant_bank_details,
               merchant_voucher_group, override=False, agent_id="",
               voucher_secret_number=""):
        """Transition ACTIVATED → CONSUMED. Records merchant details."""

    @staticmethod
    def cancel(voucher_serial_number):
        """Transition PREACTIVATED|ACTIVATED → CANCELLED.
        Raises VoucherAlreadyCancelled if already cancelled."""

    @staticmethod
    def get_status(serial_number):
        """Return voucher or raise InvalidVoucherSerial."""
```

### 9.2 Serial Number Generation

Voucher serial numbers must be unique and numeric (the harness uses numbers like `5550`, `6004`, `60000`). Generate with a sequential counter seeded per group, padded to at minimum 4 digits:

```python
import secrets

def _generate_voucher_serial() -> str:
    """Generate a unique 6-digit numeric serial number."""
    # Use random for generation; uniqueness enforced by DB unique constraint
    return str(secrets.randbelow(900000) + 100000)  # 100000–999999
```

In tests, seed specific serial numbers by patching `_generate_voucher_serial`.

---

## 10. Wave 1 — Foundation and Scaffolding

**Goal:** All GovStack Payments infrastructure in place. Zero business logic. All endpoints return stub responses.

**What to build:**

1. `apps/payments/govstack_models.py` — all 6 models defined, no data yet
2. `apps/payments/govstack_exceptions.py` — all custom exception classes
3. `apps/payments/govstack_auth.py` — `IsTrustedSourceBB`, `AllowAnyBB` permissions
4. `apps/payments/govstack_serializers.py` — request/response serializers (see Wave 2–4 for details)
5. `apps/payments/govstack_services.py` — empty service class stubs
6. `apps/payments/govstack_views.py` — empty APIView stubs returning `{"status": "not_implemented"}`
7. `apps/payments/govstack_urls.py` — URL routing wired up
8. `config/urls.py` — mount `/govstack/payments/` under the new namespace
9. `apps/payments/migrations/0016_govstack_models.py` — migration for all 6 new models

**`govstack_urls.py` skeleton:**

```python
from django.urls import path
from apps.payments import govstack_views as gv

app_name = "govstack_payments"

urlpatterns = [
    # G2P — Beneficiary
    path("register-beneficiary", gv.RegisterBeneficiaryView.as_view(), name="register_beneficiary"),
    path("update-beneficiary-details", gv.UpdateBeneficiaryView.as_view(), name="update_beneficiary"),

    # G2P — Bulk Payment
    path("bulk-payment", gv.BulkPaymentView.as_view(), name="bulk_payment"),
    path("prepayment-validation", gv.PrepaymentValidationView.as_view(), name="prepayment_validation"),
    path("prepayment-validation-response", gv.PrepaymentValidationResponseView.as_view(),
         name="prepayment_validation_response"),

    # Vouchers
    path("vouchers/voucher_preactivation", gv.VoucherPreactivationView.as_view(), name="voucher_preactivation"),
    path("vouchers/voucher_activation", gv.VoucherActivationView.as_view(), name="voucher_activation"),
    path("vouchers/voucher_redemption", gv.VoucherRedemptionView.as_view(), name="voucher_redemption"),
    path("vouchers/voucherstatuscheck/<str:voucherserialnumber>",
         gv.VoucherStatusCheckView.as_view(), name="voucher_status_check"),

    # P2G (Wave 5)
    path("bills/<str:bill_id>", gv.BillInquiryView.as_view(), name="bill_inquiry"),
    path("billTransferRequests", gv.BillTransferRequestView.as_view(), name="bill_transfer"),
    path("bills/<str:bill_id>/mark-paid", gv.MarkBillPaidView.as_view(), name="mark_bill_paid"),
    path("transferRequests/<str:transfer_request_id>",
         gv.TransferRequestStatusView.as_view(), name="transfer_request_status"),
]
```

**`config/urls.py` addition:**

```python
path("govstack/payments/", include("apps.payments.govstack_urls", namespace="govstack_payments")),
```

**Definition of Done — Wave 1:**
- [ ] All 6 models created and migrated successfully
- [ ] `python manage.py migrate --check` passes
- [ ] All URL patterns resolvable (`python manage.py check`)
- [ ] All stub views return 200 with `{"status": "not_implemented"}`
- [ ] No existing Payments tests broken

---

## 11. Wave 2 — G2P Beneficiary Management (2 harness features)

**Harness features:** `g2p_register_beneficiary.feature`, `g2p_update_beneficiary_details.feature`

### 11.1 `POST /govstack/payments/register-beneficiary`

**GovStack spec:** `api/G2P API YAMLs/RegisterBeneficiaryRequest.yml`

**Required headers:**
- `X-Callback-URL` (required) — URL to POST async confirmation
- `X-Registering-Institution-ID` (required)

**Request body:**
```json
{
  "RequestID": "4a0425ef-008",
  "SourceBBID": "11668d2a-a8f",
  "Beneficiaries": [
    {
      "PayeeFunctionalID": "2ba5ed20-0f42-4eff-8",
      "PaymentModality": "01",
      "FinancialAddress": "LI4808800751423444466"
    }
  ]
}
```

**Required fields:** `RequestID`, `SourceBBID`, and at least one `Beneficiaries` entry with `PayeeFunctionalID`. `PaymentModality` and `FinancialAddress` are optional.

**Validation rules:**
- `SourceBBID`: must match pattern `[a-zA-Z0-9-]{3,20}` — harness uses values like `"11668d2a-a8f"`, `"invalid"` (should fail)
- `PayeeFunctionalID`: must match pattern `[a-zA-Z0-9-]{3,20}` — harness uses values like `"2ba5ed20-0f42-4eff-8"`, `"invalid"` (should fail)
- `RequestID`: max 16 chars
- On validation failure: return `200` with `ResponseCode: "01"`, `RequestID` echoed

**Success response:**
```json
{
  "ResponseCode": "00",
  "RequestID": "4a0425ef-008",
  "ResponseDescription": "Beneficiary registered successfully."
}
```

**Failure response (missing SourceBBID, missing PayeeFunctionalID, invalid values):**
```json
{
  "ResponseCode": "01",
  "RequestID": "4a0425ef-008",
  "ResponseDescription": "SourceBBID is required."
}
```

HTTP status: `200` for both success and domain failure, `400` for malformed JSON.

**Idempotency:** If `PayeeFunctionalID` already exists, update the existing record (upsert). Return `ResponseCode: "00"`.

**View skeleton:**
```python
class RegisterBeneficiaryView(APIView):
    permission_classes = [IsTrustedSourceBB]
    throttle_scope = "govstack_bb"

    def post(self, request):
        callback_url = request.headers.get("X-Callback-URL", "")
        institution_id = request.headers.get("X-Registering-Institution-ID", "")
        request_id = request.data.get("RequestID", "")
        source_bb_id = request.data.get("SourceBBID", "")
        beneficiaries = request.data.get("Beneficiaries", [])

        # Validate required fields
        if not source_bb_id or not _valid_bb_id(source_bb_id):
            return Response({
                "ResponseCode": "01",
                "RequestID": request_id,
                "ResponseDescription": "SourceBBID is required and must be alphanumeric.",
            })
        if not beneficiaries:
            return Response({
                "ResponseCode": "01",
                "RequestID": request_id,
                "ResponseDescription": "Beneficiaries array cannot be empty.",
            })
        for b in beneficiaries:
            if not b.get("PayeeFunctionalID") or not _valid_payee_id(b["PayeeFunctionalID"]):
                return Response({
                    "ResponseCode": "01",
                    "RequestID": request_id,
                    "ResponseDescription": "Each beneficiary must have a valid PayeeFunctionalID.",
                })

        GovStackBeneficiaryService.register(
            request_id=request_id,
            source_bb_id=source_bb_id,
            beneficiaries=beneficiaries,
            registering_institution_id=institution_id,
            callback_url=callback_url,
        )
        return Response({
            "ResponseCode": "00",
            "RequestID": request_id,
            "ResponseDescription": "Beneficiaries registered successfully.",
        })
```

### 11.2 `POST /govstack/payments/update-beneficiary-details`

**GovStack spec:** `api/G2P API YAMLs/UpdateBeneficiaryRequest.yml`

Same headers as Register. Request body:
```json
{
  "RequestID": "c2b861bf-ef7",
  "SourceBBID": "049d8b3b-146",
  "Beneficiaries": [
    {
      "PayeeFunctionalID": "1d591f0d-eff5-424d-b",
      "PaymentModality": "01",
      "FinancialAddress": "CZ4150515873793513322865"
    }
  ]
}
```

Same validation rules. Returns same envelope.
- If `PayeeFunctionalID` not found: return `ResponseCode: "01"`, `ResponseDescription: "Beneficiary not found."`, HTTP `200`
- `PayeeFunctionalID` cannot be updated — only `PaymentModality` and `FinancialAddress`

**Definition of Done — Wave 2:**
- [ ] `g2p_register_beneficiary.feature` — all `@smoke`, `@unit @positive`, and `@unit @negative` scenarios pass
- [ ] `g2p_update_beneficiary_details.feature` — all scenarios pass
- [ ] `GovStackBeneficiary` records created in DB and readable in admin
- [ ] `GovStackPaymentAuditEntry` written for every register and update
- [ ] No PII in any log line (add `grep -r "payee_functional" logs/` check to CI)
- [ ] Min 10 unit tests in `apps/payments/tests/test_govstack_beneficiary.py`

---

## 12. Wave 3 — G2P Bulk Disbursement (2 harness features)

**Harness features:** `g2p_bulk_payment.feature`, `g2p_prepayment_validation.feature`

### 12.1 `POST /govstack/payments/bulk-payment`

**GovStack spec:** `api/G2P API YAMLs/BulkPayment.yml`

**Required headers:** `X-CorrelationID`

**Request body:**
```json
{
  "RequestID": "RequestID111",
  "SourceBBID": "SourceBBID11",
  "BatchID": "BatchID11111",
  "CreditInstructions": [
    {
      "InstructionID": "InstructionID111",
      "PayeeFunctionalID": "PayeeFunctionalID111",
      "Amount": 100,
      "Currency": "USD",
      "Narration": "string"
    }
  ]
}
```

**Validation rules (all return HTTP 400 on failure, not 200):**
- `SourceBBID`: required, pattern `[a-zA-Z0-9]{10}` (harness uses `"SourceBBID11"` = 10 chars, `"invalid"` fails)
- `BatchID`: required, pattern `[a-zA-Z0-9]{11}` (harness: `"BatchID11111"`, `"invalid"` fails)
- `RequestID`: required, max 16 chars
- `CreditInstructions`: required, must not be empty array
- Each instruction: `InstructionID`, `PayeeFunctionalID`, `Amount`, `Currency` required

**Success response (HTTP 200):**
```json
{
  "ResponseCode": "00",
  "RequestID": "RequestID111",
  "ResponseDescription": "Batch received successfully."
}
```

**Failure response (HTTP 400, missing required field):**
```json
{
  "ResponseCode": "01",
  "RequestID": "RequestID111",
  "ResponseDescription": "SourceBBID is required."
}
```

> **Note:** The harness checks HTTP `400` on negative scenarios. Unlike Beneficiary endpoints, Bulk Payment returns HTTP `400` (not `200`) for validation failures.

**Async processing:** After returning `200`, enqueue a Celery task `process_bulk_payment_batch.delay(batch_pk)` that:
1. Looks up each `CreditInstruction`'s `PayeeFunctionalID` in `GovStackBeneficiary`
2. Marks instruction as `COMPLETED` if found, `FAILED` if not
3. Updates `BulkPaymentBatch.status` → `COMPLETED` or `PARTIAL` or `FAILED`
4. POSTs callback to `BulkPaymentBatch.callback_url` if set

### 12.2 `POST /govstack/payments/prepayment-validation`

Two-step feature: the harness sends the validation request AND then a validation-response confirmation.

**Step 1 — `POST /govstack/payments/prepayment-validation`:**

```json
{
  "RequestID": "abcdef123456",
  "SourceBBID": "sourceBBID12",
  "BatchID": "batchID12345",
  "CreditInstructions": [
    {
      "InstructionID": "instructionID123",
      "PayeeFunctionalID": "PayeeFunctionalID123",
      "Amount": 100,
      "Currency": "USD",
      "Narration": "Narration"
    }
  ]
}
```

Response (HTTP 200):
```json
{
  "ResponseCode": "00",
  "RequestID": "abcdef123456",
  "ResponseDescription": "Validation request received."
}
```

**Step 2 — `POST /govstack/payments/prepayment-validation-response`:**

The harness calls this to confirm it received the async result:
```json
{
  "RequestID": "abcdef123456",
  "SourceBatchID": "batchID12345"
}
```

Response (HTTP 200):
```json
{
  "ResponseCode": "00",
  "RequestID": "abcdef123456",
  "ResponseDescription": "Validation result acknowledged."
}
```

**Celery task `validate_prepayment_async`:**
1. Look up `PayeeFunctionalID` → `GovStackBeneficiary`
2. Create `PrepaymentValidationRequest` record with result
3. POST to `X-Callback-URL` if provided (for harness, not required to succeed)

**Definition of Done — Wave 3:**
- [ ] `g2p_bulk_payment.feature` — all `@smoke`, `@unit @positive`, `@unit @negative` scenarios pass
- [ ] `g2p_prepayment_validation.feature` — all scenarios pass
- [ ] `BulkPaymentBatch` and `CreditInstruction` records created in DB
- [ ] Celery task `process_bulk_payment_batch` runs and updates batch status
- [ ] `GovStackPaymentAuditEntry` written for batch received and each instruction outcome
- [ ] Min 12 unit tests in `apps/payments/tests/test_govstack_bulk_payment.py`

---

## 13. Wave 4 — Voucher Engine (5 harness features)

This is the largest single wave — 5 features but one unified model.

### 13.1 `POST /govstack/payments/vouchers/voucher_preactivation`

**Harness:** `voucher_preactivation.feature`
**Required headers:** `X-Registering-Institution-Id` (optional: `X-Callback-URL`, `X-Channel`, `X-Date`, `X-CorrelationID`)

**Request body:**
```json
{
  "voucher_amount": 15.21,
  "voucher_currency": "AED",
  "voucher_group": "string",
  "Gov_Stack_BB": "Gov_Stack_BB"
}
```

**Validation (use custom exception classes):**
- `voucher_amount`: must be positive float → HTTP `452` if invalid
- `voucher_currency`: must be 3-letter ISO 4217 → HTTP `453` if invalid
- `voucher_group`: must be non-empty string → HTTP `454` if invalid/empty
- `Gov_Stack_BB`: must be a known/registered BB identifier → HTTP `460` if not recognized. **For the harness:** accept any non-empty string — the harness uses `"Gov_Stack_BB"` as the value

**Success response (HTTP 200):**
```json
{
  "voucherNumber": "123456",
  "voucherSerialNumber": "123456",
  "voucherGroup": "string",
  "expiryDate": "2026-12-31T00:00:00Z"
}
```

Note: `voucherNumber` and `voucherSerialNumber` are the same value. `expiryDate` is `now() + 90 days` (configurable via `GOVSTACK_VOUCHER_EXPIRY_DAYS` setting, default 90).

**Service call:**
```python
voucher = GovStackVoucherService.preactivate(
    voucher_amount=Decimal(str(data["voucher_amount"])),
    voucher_currency=data["voucher_currency"].upper(),
    voucher_group=data["voucher_group"],
    issuing_bb=data["Gov_Stack_BB"],
    registering_institution_id=request.headers.get("X-Registering-Institution-Id", ""),
    callback_url=request.headers.get("X-Callback-URL", ""),
)
```

### 13.2 `PATCH /govstack/payments/vouchers/voucher_activation`

**Harness:** `voucher_activation.feature`
**Required headers:** `X-Registering-Institution-Id` (optional: `X-Callback-URL`, `X-Channel`, `X-Date`, `X-CorrelationID`)

**Request body:**
```json
{
  "voucher_serial_number": 5550,
  "Gov_Stack_BB": "bb-digital-registries"
}
```

Note: `voucher_serial_number` is sent as integer by the harness.

**Validation:**
- `Gov_Stack_BB`: unknown → HTTP `460`
- `voucher_serial_number`: not found → HTTP `456`
- Empty payload → HTTP `400`

**Success response (HTTP 200):**
```json
{
  "voucherNumber": "5550",
  "voucherSerialNumber": "5550",
  "voucherStatus": "Activated",
  "voucherGroup": "Payment Voucher"
}
```

### 13.3 `POST /govstack/payments/vouchers/voucher_redemption`

**Harness:** `voucher_redemption.feature`
**Auth:** JWT Bearer

**Request body:**
```json
{
  "voucher_number": 6004,
  "Gov_Stack_BB": "bb-digital-registries",
  "merchant_name": "Melissa Stephenson",
  "merchant_bank_details": "Citizen Service Banks",
  "merchant_voucher_group": "Payment Voucher",
  "override": true
}
```

**Validation:**
- `Gov_Stack_BB`: not known → HTTP `460`
- `voucher_number`: not found → HTTP `456`
- Empty payload → HTTP `400`

**Success response (HTTP 200):**
```json
{
  "status": 1,
  "message": "Voucher redeemed successfully.",
  "serialNumber": "6004",
  "value": 15.21,
  "timestamp": "2026-07-19T14:00:00Z",
  "transactionId": "TXN123456"
}
```

Failure response (HTTP 400):
```json
{
  "status": 0,
  "message": "Invalid voucher number."
}
```

### 13.4 `PATCH /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}` — Cancellation

**Harness:** `voucher_cancelation.feature`

No request body required — the serial number is in the path.

**Scenarios:**
- Serial `"60000"` → cancel successfully → HTTP `200`
- Serial `"60001"` → cancel once → `200`, cancel again → HTTP `464`
- Serial `"invalid_serial_number"` → HTTP `463`

**Success response (HTTP 200):**
```json
{
  "voucherSerialNumber": "60000",
  "voucherStatus": "Cancelled"
}
```

**Double-cancel (HTTP 464):**
```json
{
  "message": "This voucher has already been cancelled."
}
```

**Invalid serial (HTTP 463):**
```json
{
  "message": "Invalid voucher serial number."
}
```

> **Important:** The harness pre-seeds serial `"60000"` and `"60001"` in `test-data.json`. Your test setup must ensure these vouchers exist in `PREACTIVATED` status before the harness runs. See the Wave 4 test setup section.

### 13.5 `GET /govstack/payments/vouchers/voucherstatuscheck/{voucherserialnumber}` — Status

**Harness:** `voucher_status_check.feature`

The harness uses serial numbers `"5555"`, `"5556"`, `"5557"`, etc. These must exist in DB.

**Optional headers:** `X-Callback-URL`, `X-Channel`, `X-Date`, `X-CorrelationID`

**Success response (HTTP 200):**
```json
{
  "status": 1,
  "serialNumber": "5555",
  "value": 15.21
}
```

Status integer map (from `GovStackVoucher.STATUS_INT_MAP`):
- `0` = Not Preactivated
- `1` = Preactivated
- `2` = Activated
- `3` = Consumed
- `4` = Blocked
- `5` = Suspended
- `6` = Cancelled
- `7` = Purged
- `9` = Error

**Failure response (HTTP 400, invalid serial):**
```json
{
  "status": 9,
  "message": "Voucher not found.",
  "serialNumber": "invalid",
  "value": 0.0
}
```

### 13.6 Harness Pre-seeded Data

The harness `test-data.json` pre-seeds specific serial numbers. CivicOS must ensure these exist before the harness run. Create a management command:

```
python manage.py seed_govstack_vouchers
```

This seeds:
- Serial `5550`–`5560` in `PREACTIVATED` status (for activation tests)
- Serial `6004` in `ACTIVATED` status (for redemption test)
- Serial `5555`–`5560` in `PREACTIVATED` status (for status check tests)
- Serial `60000`–`60001` in `PREACTIVATED` status (for cancellation tests)

All with `amount=15.21`, `currency=AED`, `group_code="Payment Voucher"`.

**Definition of Done — Wave 4:**
- [ ] `voucher_preactivation.feature` — all scenarios pass (positive + all negative error codes 452, 453, 454, 460, 400)
- [ ] `voucher_activation.feature` — all scenarios pass (positive + 400, 456, 460)
- [ ] `voucher_redemption.feature` — all scenarios pass (positive + 400, 460)
- [ ] `voucher_cancelation.feature` — all scenarios pass (positive + 463, 464)
- [ ] `voucher_status_check.feature` — all scenarios pass (positive + optional headers + 400)
- [ ] `seed_govstack_vouchers` management command works idempotently
- [ ] `GovStackVoucher` state machine rejects invalid transitions with `ValueError`
- [ ] `voucher_secret` never appears in any HTTP response or log
- [ ] Min 20 unit tests in `apps/payments/tests/test_govstack_vouchers.py`

---

## 14. Wave 5 — P2G Bill Payments (adapter over existing Payments)

**No harness features yet.** Implement to spec; mark as "implemented, not harness-tested."

This wave wraps existing CivicOS Payments models with GovStack P2G endpoint shapes.

### 14.1 Mapping — Existing CivicOS → GovStack P2G

| GovStack concept | CivicOS equivalent |
|---|---|
| Bill | `FeeSchedule` (by service code) |
| `billId` | `FeeSchedule.fee_code` |
| Bill amount | `FeeSchedule.amount` |
| Bill status | `PaymentIntent.status` |
| Mark bill paid | `PaymentIntent → STATUS_COMPLETED` |
| Bill transfer notification | Stripe webhook equivalent (now mobile money) |

### 14.2 `GET /govstack/payments/bills/{billId}`

Returns bill details for a given fee code.

```json
{
  "billId": "PERMIT-2026-001",
  "amount": 150.00,
  "currency": "CAD",
  "description": "Building Permit Application Fee",
  "status": "unpaid",
  "dueDate": "2026-12-31"
}
```

### 14.3 `POST /govstack/payments/billTransferRequests`

Receives notification from a mobile money financial institution that a bill has been paid.

Required headers: `X-CorrelationID`, `X-Platform-TenantId`, `X-PayerFI-Id`

**Request body:**
```json
{
  "requestId": "abc123",
  "billInquiryRequestId": "xyz456",
  "billId": "PERMIT-2026-001",
  "paymentReferenceID": "MPESA-12345678"
}
```

**Response (HTTP 202):**
```json
{
  "responseCode": "00",
  "reason": "Payment notification received.",
  "requestID": "abc123"
}
```

### 14.4 `POST /govstack/payments/bills/{billId}/mark-paid`

Staff endpoint to manually mark a bill as paid (after confirming receipt of mobile money payment).

**Definition of Done — Wave 5:**
- [ ] `GET /govstack/payments/bills/{billId}` returns correct data from `FeeSchedule`
- [ ] `POST /govstack/payments/billTransferRequests` creates a `PaymentIntent` in COMPLETED status
- [ ] All endpoints return `Content-Type: application/json`
- [ ] Min 8 unit tests in `apps/payments/tests/test_govstack_p2g.py`

---

## 15. URL Structure — Complete Reference

```
/govstack/payments/
├── register-beneficiary              POST    Wave 2
├── update-beneficiary-details        POST    Wave 2
├── bulk-payment                      POST    Wave 3
├── prepayment-validation             POST    Wave 3
├── prepayment-validation-response    POST    Wave 3
├── vouchers/
│   ├── voucher_preactivation         POST    Wave 4
│   ├── voucher_activation            PATCH   Wave 4
│   ├── voucher_redemption            POST    Wave 4
│   └── voucherstatuscheck/
│       └── <str:serial>/
│           ├── (GET)                 GET     Wave 4 — status check
│           └── (PATCH)              PATCH   Wave 4 — cancellation
├── bills/
│   └── <str:bill_id>/
│       ├── (GET)                     GET     Wave 5 — bill inquiry
│       └── mark-paid/               POST    Wave 5
├── billTransferRequests              POST    Wave 5
└── transferRequests/
    └── <str:transfer_request_id>/   GET     Wave 5
```

---

## 16. Admin Interface

Register all GovStack models in `apps/payments/admin.py` (in the existing admin file, add a clearly labelled section):

```python
# ──── GovStack Payments BB Admin ─────────────────────────────────────────────

@admin.register(GovStackBeneficiary)
class GovStackBeneficiaryAdmin(admin.ModelAdmin):
    list_display = ["pk", "source_bb_id", "payment_modality", "is_active", "created_at"]
    list_filter = ["is_active", "payment_modality"]
    readonly_fields = ["id", "payee_functional_id", "financial_address_masked",
                       "registering_institution_id", "created_at", "updated_at"]
    search_fields = ["source_bb_id"]
    # payee_functional_id and financial_address are read-only and masked in list view

    def financial_address_masked(self, obj):
        val = obj.financial_address or ""
        return "✓ Set" if val else "✗ Not set"
    financial_address_masked.short_description = "Financial Address"

@admin.register(BulkPaymentBatch)
class BulkPaymentBatchAdmin(admin.ModelAdmin):
    list_display = ["batch_id", "source_bb_id", "status", "created_at"]
    list_filter = ["status"]
    readonly_fields = ["id", "request_id", "source_bb_id", "batch_id",
                       "total_amount", "completed_amount", "failed_amount",
                       "result_generated_at", "created_at"]

@admin.register(GovStackVoucher)
class GovStackVoucherAdmin(admin.ModelAdmin):
    list_display = ["serial_number", "status", "amount", "currency",
                    "group_code", "issuing_bb", "created_at"]
    list_filter = ["status", "currency", "group_code"]
    readonly_fields = ["id", "serial_number", "voucher_secret_masked", "amount",
                       "currency", "status", "issuing_bb", "redeemed_at",
                       "redemption_transaction_id"]
    search_fields = ["serial_number", "group_code"]

    def voucher_secret_masked(self, obj):
        return "✓ Set" if obj.voucher_secret else "✗ Not set"
    voucher_secret_masked.short_description = "Voucher Secret"

@admin.register(GovStackPaymentAuditEntry)
class GovStackPaymentAuditEntryAdmin(admin.ModelAdmin):
    list_display = ["action", "actor_bb_id", "object_type", "object_pk",
                    "request_id", "timestamp"]
    list_filter = ["action", "object_type"]
    readonly_fields = "__all__"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

---

## 17. Audit Trail Requirements

Every state change to a GovStack model must produce a `GovStackPaymentAuditEntry` inside the same `transaction.atomic()` block as the model save.

| Event | `action` | `object_type` | `object_pk` | `details` |
|---|---|---|---|---|
| Beneficiary registered | `beneficiary_registered` | `beneficiary` | `str(beneficiary.pk)` | `{"source_bb_id": "...", "payment_modality": "..."}` |
| Beneficiary updated | `beneficiary_updated` | `beneficiary` | `str(beneficiary.pk)` | `{"fields_updated": ["payment_modality"]}` |
| Batch received | `batch_received` | `batch` | `batch.batch_id` | `{"instruction_count": n, "source_bb_id": "..."}` |
| Batch completed | `batch_completed` | `batch` | `batch.batch_id` | `{"completed": n, "failed": n, "partial": bool}` |
| Instruction failed | `instruction_failed` | `instruction` | `str(instruction.pk)` | `{"failure_reason": "..."}` |
| Voucher preactivated | `voucher_preactivated` | `voucher` | `voucher.serial_number` | `{"amount": "15.21", "currency": "AED", "group": "..."}` |
| Voucher activated | `voucher_activated` | `voucher` | `voucher.serial_number` | `{"issuing_bb": "..."}` |
| Voucher redeemed | `voucher_redeemed` | `voucher` | `voucher.serial_number` | `{"merchant_name": "...", "transaction_id": "..."}` |
| Voucher cancelled | `voucher_cancelled` | `voucher` | `voucher.serial_number` | `{"cancelled_from_status": "..."}` |

`actor_bb_id` = the `SourceBBID` or `Gov_Stack_BB` from the request.
`request_id` = the `RequestID` from the request body.
Never include `payee_functional_id`, `financial_address`, or `voucher_secret` in `details`.

---

## 18. Test Requirements

### 18.1 Test Files

| File | Wave | Minimum tests |
|---|---|---|
| `test_govstack_beneficiary.py` | Wave 2 | 10 |
| `test_govstack_bulk_payment.py` | Wave 3 | 12 |
| `test_govstack_vouchers.py` | Wave 4 | 20 |
| `test_govstack_p2g.py` | Wave 5 | 8 |
| `test_govstack_models.py` | All | 10 |
| `test_govstack_services.py` | All | 15 |

### 18.2 What Each Test File Must Cover

**`test_govstack_beneficiary.py`:**
- Register with required fields → `ResponseCode: "00"`
- Register with optional fields (PaymentModality, FinancialAddress) → `ResponseCode: "00"`
- Register with missing SourceBBID → `ResponseCode: "01"`, HTTP 400
- Register with invalid SourceBBID (`"invalid"`) → `ResponseCode: "01"`
- Register with missing PayeeFunctionalID → `ResponseCode: "01"`
- Register with invalid PayeeFunctionalID → `ResponseCode: "01"`
- Update existing beneficiary → `ResponseCode: "00"`, `ResponseDescription` present
- Update non-existent beneficiary → `ResponseCode: "01"`
- Verify `GovStackPaymentAuditEntry` written on register
- Verify `financial_address` is encrypted at rest (raw DB value ≠ input value)

**`test_govstack_vouchers.py`:**
- Preactivation happy path → HTTP 200, `voucherNumber` in response
- Preactivation invalid amount → HTTP 452, `message` present
- Preactivation invalid currency → HTTP 453
- Preactivation invalid group → HTTP 454
- Preactivation unknown BB → HTTP 460
- Preactivation empty body → HTTP 400
- Activation happy path → HTTP 200
- Activation invalid serial → HTTP 456
- Activation unknown BB → HTTP 460
- Activation empty body → HTTP 400
- Redemption happy path → HTTP 200, `status: 1`
- Redemption unknown BB → HTTP 460
- Redemption invalid voucher → HTTP 400 or 456
- Cancellation happy path → HTTP 200
- Double cancellation → HTTP 464
- Cancellation invalid serial → HTTP 463
- Status check happy path → HTTP 200, `status` integer
- Status check optional headers accepted → HTTP 200
- State machine: `PREACTIVATED → ACTIVATED → CONSUMED` valid
- State machine: `CONSUMED → CANCELLED` invalid → `ValueError`
- Audit entry written for every state change

### 18.3 Running Only GovStack Tests

```bash
python manage.py test \
  apps.payments.tests.test_govstack_beneficiary \
  apps.payments.tests.test_govstack_bulk_payment \
  apps.payments.tests.test_govstack_vouchers \
  apps.payments.tests.test_govstack_p2g \
  apps.payments.tests.test_govstack_models \
  apps.payments.tests.test_govstack_services \
  --settings=config.settings.test
```

---

## 19. Migrations

### 19.1 Migration Plan

| Migration | Contents |
|---|---|
| `0016_govstack_models.py` | Create all 6 new govstack tables |
| `0017_govstack_voucher_seed_data.py` | RunPython to call `seed_govstack_vouchers` for harness pre-seeded data |

**`0016` must include:**
- `GovStackBeneficiary` (unique on `payee_functional_id`)
- `BulkPaymentBatch` (unique on `batch_id`)
- `CreditInstruction` (unique_together: batch + instruction_id)
- `PrepaymentValidationRequest` (unique on `request_id`)
- `GovStackVoucher` (unique on `serial_number`)
- `GovStackPaymentAuditEntry` (no unique constraints — append-only log)

**Do not add any GovStack fields to existing models.** All GovStack data stays in `govstack_models.py` tables.

---

## 20. File Structure

```
apps/payments/
│
│  # ── EXISTING — DO NOT MODIFY ─────────────────────────────
├── models.py                       # Stripe, PaymentIntent, Donations, Receipts
├── views/                          # Stripe-facing views
│   ├── donation.py
│   ├── fee_payment.py
│   ├── portal.py
│   ├── refund.py
│   └── webhook.py
├── services/                       # CivicOS payment services
├── gateway.py
├── gateways/
├── urls.py
├── portal_urls.py
├── donation_urls.py
│
│  # ── NEW — GovStack Layer ──────────────────────────────────
├── govstack_models.py              # Wave 1: All 6 GovStack models
├── govstack_exceptions.py          # Wave 1: Custom error codes 452–464
├── govstack_auth.py                # Wave 1: IsTrustedSourceBB, AllowAnyBB
├── govstack_serializers.py         # Wave 1: DRF serializers
├── govstack_services.py            # Wave 2–5: Business logic
├── govstack_views.py               # Wave 2–5: DRF APIViews
├── govstack_urls.py                # Wave 1: URL routing
│
├── migrations/
│   ├── ... (0001–0015 existing)
│   ├── 0016_govstack_models.py     # Wave 1: New tables
│   └── 0017_govstack_seed_data.py  # Wave 4: Harness pre-seed
│
├── management/commands/
│   └── seed_govstack_vouchers.py   # Wave 4: Idempotent seed command
│
└── tests/
    ├── ... (existing test files)
    ├── test_govstack_beneficiary.py  # Wave 2
    ├── test_govstack_bulk_payment.py # Wave 3
    ├── test_govstack_vouchers.py     # Wave 4
    ├── test_govstack_p2g.py          # Wave 5
    ├── test_govstack_models.py       # Wave 1
    └── test_govstack_services.py     # Wave 2–5
```

---

## 21. Implementation Order and Definition of Done per Wave

### The Correct Build Order

```
Wave 1 → Wave 2 → Wave 3 → Wave 4 → Wave 5
```

Do not start Wave 2 until Wave 1 is fully done (migration applied, URLs resolvable, CI green).
Do not start Wave 3 until Wave 2 harness features pass.
And so on.

### Wave 1 — Definition of Done

- [ ] `govstack_models.py` — all 6 model classes defined
- [ ] `govstack_exceptions.py` — all 7 exception classes
- [ ] `govstack_auth.py` — `IsTrustedSourceBB` and `AllowAnyBB`
- [ ] `govstack_views.py` — all view stubs (return `{"status": "not_implemented"}` with HTTP 200)
- [ ] `govstack_urls.py` — all URL patterns registered
- [ ] `config/urls.py` — `/govstack/payments/` mounted
- [ ] Migration `0016` applied cleanly on a fresh DB
- [ ] `python manage.py check` — no errors
- [ ] All existing Payments tests still pass (1,115 tests green)

### Wave 2 — Definition of Done

- [ ] `GovStackBeneficiaryService.register()` and `.update()` implemented
- [ ] `RegisterBeneficiaryView` fully implemented
- [ ] `UpdateBeneficiaryView` fully implemented
- [ ] All harness scenarios in `g2p_register_beneficiary.feature` pass
- [ ] All harness scenarios in `g2p_update_beneficiary_details.feature` pass
- [ ] `GovStackPaymentAuditEntry` written on every register and update
- [ ] `financial_address` Fernet-encrypted in DB
- [ ] 10+ unit tests passing

### Wave 3 — Definition of Done

- [ ] `GovStackBulkPaymentService.receive_batch()` implemented
- [ ] `GovStackBulkPaymentService.validate_prepayment()` implemented
- [ ] `process_bulk_payment_batch` Celery task implemented
- [ ] `validate_prepayment_async` Celery task implemented
- [ ] `BulkPaymentView` fully implemented
- [ ] `PrepaymentValidationView` + `PrepaymentValidationResponseView` implemented
- [ ] All harness scenarios in `g2p_bulk_payment.feature` pass
- [ ] All harness scenarios in `g2p_prepayment_validation.feature` pass
- [ ] 12+ unit tests passing

### Wave 4 — Definition of Done

- [ ] `GovStackVoucherService` — all 5 methods implemented
- [ ] All 5 Voucher views implemented with correct HTTP status codes and error responses
- [ ] `seed_govstack_vouchers` management command seeds harness data idempotently
- [ ] Migration `0017` seeds data correctly
- [ ] All 5 voucher harness feature files pass (all scenarios: smoke, positive, negative)
- [ ] `voucher_secret` confirmed absent from all HTTP responses (grep test)
- [ ] State machine rejects invalid transitions
- [ ] 20+ unit tests passing

### Wave 5 — Definition of Done

- [ ] All P2G views implemented
- [ ] `FeeSchedule` mapped to `/bills/{billId}` correctly
- [ ] Bill payment notification creates/updates `PaymentIntent`
- [ ] No harness submission yet — marked "implemented, pending harness"
- [ ] 8+ unit tests passing

### Harness Submission Checklist

Before submitting to `testing.govstack.global`:

- [ ] All 9 feature files have been run locally against a clean DB
- [ ] `seed_govstack_vouchers` run immediately before harness submission
- [ ] `Content-Type: application/json` confirmed on all responses (`curl -I` check)
- [ ] All responses return within 15,000 ms under load
- [ ] No PII in any log line during harness run
- [ ] All custom error codes (452–464) tested and correct
- [ ] `GOVSTACK_BB_HONEST_READINESS_ASSESSMENT.md` updated to reflect harness submission

---

*This document supersedes all prior informal descriptions of what the Payments BB needs for GovStack certification. The implementation must follow the wave order above. No wave may be marked Done until all its Definition of Done checkboxes are checked.*

---
---

## 22. Remaining Work — Post-Implementation Gaps for Harness Certification

**Added:** 2026-07-22, following a 5-agent deep certifiability review of the full CivicOS platform.

**Context:** Waves 1–5 are fully implemented — 14 endpoints registered, 337 GovStack-specific tests, all security invariants verified (Fernet encryption, append-only audit, `select_for_update` on all state transitions). What follows is the precise delta between "code complete" and "harness-certifiable."

---

### GAP-1 ✅ RESOLVED — `seed_govstack_vouchers` management command is missing

**Resolved:** 2026-07-23 — `apps/payments/management/commands/seed_govstack_vouchers.py` created; seeds 14 vouchers (serials 5550–5560, 6004, 60000–60001) in `STATUS_PREACTIVATED` state and `GovStackRegisteredBB(bb_id="GS-HARNESS")`.

**File to create:** `apps/payments/management/commands/seed_govstack_vouchers.py`

**Why it blocks certification:** The GovStack Voucher harness (all 5 Wave 4 feature files — Features 5 through 9) chains tests across separate Gherkin scenarios: `voucher_preactivation → voucher_activation → voucher_redemption → voucherstatuscheck → cancel`. Many of these scenarios reference specific serial numbers that must already exist in the database in `STATUS_PREACTIVATED` state before the harness run begins. Without pre-seeded rows, the activation/redemption/status/cancel endpoints return HTTP 452 (`InvalidVoucherSerial`) and every Wave 4 feature fails. This is the single highest-priority item.

**Serial numbers to seed (all must be `STATUS_PREACTIVATED`):**

| Serial | group_code | amount | currency | Notes |
|--------|-----------|--------|----------|-------|
| `5550` | FOOD | 100.00 | CAD | Primary test range |
| `5551` | FOOD | 100.00 | CAD | |
| `5552` | FOOD | 200.00 | CAD | |
| `5553` | FOOD | 150.00 | CAD | |
| `5554` | FOOD | 100.00 | CAD | |
| `5555` | FOOD | 100.00 | CAD | |
| `5556` | HEALTH | 75.00 | CAD | |
| `5557` | HEALTH | 75.00 | CAD | |
| `5558` | HEALTH | 75.00 | CAD | |
| `5559` | HEALTH | 75.00 | CAD | |
| `5560` | HEALTH | 75.00 | CAD | |
| `6004` | HEALTH | 200.00 | CAD | Alternate-group test |
| `60000` | TRANSPORT | 50.00 | CAD | 5-digit serial range |
| `60001` | TRANSPORT | 50.00 | CAD | 5-digit serial range |

**Critical: serial generator conflict.** `_generate_voucher_serial()` in `govstack_models.py` (line 77–84) produces 6-digit integers: `secrets.randbelow(900_000) + 100_000` → range 100,000–999,999. The required seed serials are 4-digit (`5550`–`6004`) and 5-digit (`60000`–`60001`) — both ranges fall outside the generator's output. These rows will never be created by normal operation. The seed command must create them directly via `GovStackVoucher.objects.get_or_create()`, bypassing the generator entirely.

**Implementation skeleton:**

```python
# apps/payments/management/commands/seed_govstack_vouchers.py

from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.payments.govstack_models import GovStackVoucher


class Command(BaseCommand):
    help = "Seed GovStack Payments harness test vouchers. Idempotent — safe to re-run."

    SEED_VOUCHERS: list[tuple[str, str, str, str]] = [
        # (serial_number, group_code, amount_str, currency)
        ("5550",  "FOOD",      "100.00", "CAD"),
        ("5551",  "FOOD",      "100.00", "CAD"),
        ("5552",  "FOOD",      "200.00", "CAD"),
        ("5553",  "FOOD",      "150.00", "CAD"),
        ("5554",  "FOOD",      "100.00", "CAD"),
        ("5555",  "FOOD",      "100.00", "CAD"),
        ("5556",  "HEALTH",    "75.00",  "CAD"),
        ("5557",  "HEALTH",    "75.00",  "CAD"),
        ("5558",  "HEALTH",    "75.00",  "CAD"),
        ("5559",  "HEALTH",    "75.00",  "CAD"),
        ("5560",  "HEALTH",    "75.00",  "CAD"),
        ("6004",  "HEALTH",    "200.00", "CAD"),
        ("60000", "TRANSPORT", "50.00",  "CAD"),
        ("60001", "TRANSPORT", "50.00",  "CAD"),
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing seed vouchers and recreate them. "
                 "Use only in CI/test environments, never in production.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            serials = [row[0] for row in self.SEED_VOUCHERS]
            deleted, _ = GovStackVoucher.objects.filter(
                serial_number__in=serials
            ).delete()
            self.stdout.write(f"Deleted {deleted} existing seed vouchers.")

        expiry = timezone.now() + timedelta(days=365)
        created = skipped = 0

        for serial, group, amount_str, currency in self.SEED_VOUCHERS:
            _, was_created = GovStackVoucher.objects.get_or_create(
                serial_number=serial,
                defaults={
                    "amount": Decimal(amount_str),
                    "currency": currency,
                    "group_code": group,
                    "status": GovStackVoucher.STATUS_PREACTIVATED,
                    "issuing_bb": "GS-HARNESS",
                    "expiry_date": expiry,
                },
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_govstack_vouchers: {created} created, {skipped} already existed."
            )
        )
```

**Note on spec migration 0017:** The spec's Wave 4 Definition of Done originally listed "Migration 0017 seeds data correctly." Migration `0017` in the actual codebase is `0017_tighten_payee_functional_id_validator.py` (a schema migration). Do NOT create a data migration for harness fixtures — data migrations run on every `migrate` invocation including production and pollute the DB permanently. The management command is the correct pattern. **Add `python manage.py seed_govstack_vouchers` as a required step in the CI harness setup job and in the Harness Submission Checklist below.**

**Tests to add in `test_govstack_wave4.py`:**

```
F31: seed command creates all 14 expected serials when DB is empty
F32: seed command is idempotent — running twice raises no IntegrityError and creates 0 duplicates second run
F33: seeded voucher has STATUS_PREACTIVATED (not ACTIVATED or CONSUMED)
F34: seeded serial_number is the exact string value — no zero-padding, no truncation
     (i.e. serial "5550" is stored as "5550" not "05550")
F35: --reset flag deletes existing seed rows and recreates them
F36: seeded issuing_bb is "GS-HARNESS" (allows downstream tests to identify seed rows)
```

**Estimated effort:** 2–3 hours (command + 6 tests + CI wiring).

---

### GAP-2 ✅ RESOLVED — Celery tasks for async batch processing and callback delivery

**Resolved:** 2026-07-23 — `apps/payments/govstack_tasks.py` created with `process_bulk_payment_batch` and `validate_prepayment_async`; both dispatch `_post_callback()` to `X-Callback-URL` after `transaction.on_commit`; wired into views via `transaction.on_commit(lambda: task.delay(...))`.

**File to create:** `apps/payments/govstack_tasks.py`

**Background:** The Wave 3 Definition of Done (§21) explicitly requires:
- `process_bulk_payment_batch` Celery task
- `validate_prepayment_async` Celery task

Both were intentionally deferred. The current service code comment at `govstack_services.py` line 284 reads: *"For Wave 3 certification the batch is accepted and stored; the async processing task is a no-op stub."* The `callback_url` field is captured on `BulkPaymentBatch`, `PrepaymentValidationRequest`, and `GovStackVoucher` but nothing posts to it. `BulkPaymentBatch` stays in `STATUS_RECEIVED` forever. `PrepaymentValidationRequest` stays in `STATUS_PENDING` forever.

**Risk assessment — does this block the harness?**

The answer depends on the actual Gherkin in `g2p_prepayment_validation.feature` and `g2p_bulk_payment.feature`. The harness may either:
- (a) Only verify the synchronous response shape of the `POST /prepayment-validation` and `POST /bulk-payment` calls → current implementation **PASSES** (HTTP 200, correct G2P envelope)
- (b) Also assert that a callback POST arrives at the `X-Callback-URL` within a timeout → current implementation **FAILS** for Feature 4

**Required action before submitting to harness:** Read the actual Gherkin scenario files from `github.com/GovStackWorkingGroup/bb-payments/test/openAPI/features/`. Look for any `Then` step that asserts a callback was received (e.g., `Then a POST request is received at the callback URL`). If present → implement the tasks. If absent → defer tasks to post-certification.

**If tasks must be implemented:**

```python
# apps/payments/govstack_tasks.py

import logging
import httpx
from celery import shared_task
from django.db import transaction
from apps.payments.govstack_models import (
    BulkPaymentBatch, CreditInstruction, PrepaymentValidationRequest,
    GovStackBeneficiary, GovStackPaymentAuditEntry,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="payments.process_bulk_payment_batch",
    queue="payments",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=120,
)
def process_bulk_payment_batch(self, batch_pk: str) -> None:
    """
    Transition all CreditInstructions to COMPLETED, update batch status,
    POST result to callback_url.

    Security: never log payee_functional_id or financial_address.
    Use batch_pk / instruction PKs only in log messages.
    """
    with transaction.atomic():
        try:
            batch = BulkPaymentBatch.objects.select_for_update().get(pk=batch_pk)
        except BulkPaymentBatch.DoesNotExist:
            logger.error("process_bulk_payment_batch: batch_pk=%s not found", batch_pk)
            return

        if batch.status not in (BulkPaymentBatch.STATUS_RECEIVED,):
            # Already processed — idempotent exit.
            return

        CreditInstruction.objects.filter(batch=batch).update(
            status=CreditInstruction.STATUS_COMPLETED
        )
        batch.status = BulkPaymentBatch.STATUS_COMPLETED
        batch.save(update_fields=["status"])

        GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_BATCH_COMPLETED,
            actor_bb_id=batch.source_bb_id,
            object_type="batch",
            object_pk=str(batch.pk),
            request_id=batch.request_id,
            details={"batch_id": batch.batch_id, "source_bb_id": batch.source_bb_id},
        )
        callback_url = batch.callback_url

    # POST callback outside the transaction — failure is non-fatal.
    if callback_url:
        _post_callback(callback_url, {
            "RequestID": batch.request_id,
            "BatchID": batch.batch_id,
            "Status": "COMPLETED",
        })


@shared_task(
    bind=True,
    name="payments.validate_prepayment_async",
    queue="payments",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=60,
)
def validate_prepayment_async(self, pvr_pk: str) -> None:
    """
    Validate a PrepaymentValidationRequest against the Beneficiary registry.
    Sets beneficiary_found / financial_address_valid, transitions to COMPLETED,
    POSTs result to callback_url.

    Security: NEVER include payee_functional_id in log messages or callback payload.
    """
    with transaction.atomic():
        try:
            pvr = PrepaymentValidationRequest.objects.select_for_update().get(pk=pvr_pk)
        except PrepaymentValidationRequest.DoesNotExist:
            logger.error("validate_prepayment_async: pvr_pk=%s not found", pvr_pk)
            return

        if pvr.status != PrepaymentValidationRequest.STATUS_PENDING:
            return  # Idempotent exit.

        beneficiary_found = GovStackBeneficiary.objects.filter(
            payee_functional_id=pvr.payee_functional_id,
            is_active=True,
        ).exists()

        pvr.beneficiary_found = beneficiary_found
        pvr.financial_address_valid = beneficiary_found  # valid iff beneficiary found
        pvr.status = PrepaymentValidationRequest.STATUS_COMPLETED
        pvr.save(update_fields=["beneficiary_found", "financial_address_valid", "status"])

        failed_count = 0 if beneficiary_found else 1
        GovStackPaymentAuditEntry.objects.create(
            action=GovStackPaymentAuditEntry.ACTION_VALIDATION_COMPLETED,
            actor_bb_id=pvr.source_bb_id,
            object_type="validation",
            object_pk=str(pvr.pk),
            request_id=pvr.request_id,
            details={
                "batch_id": pvr.batch_id,
                "beneficiary_found": beneficiary_found,
                # NEVER include pvr.payee_functional_id in details.
            },
        )
        callback_url = pvr.callback_url
        callback_payload = {
            "RequestID": pvr.request_id,
            "Source_BatchID": pvr.batch_id,
            "NumberFailedCases": failed_count,
            "FailedAccounts": [] if beneficiary_found else [pvr.instruction_id],
        }

    if callback_url:
        _post_callback(callback_url, callback_payload)


def _post_callback(url: str, payload: dict) -> None:
    """POST a GovStack callback. Failure is non-fatal — log and continue."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("govstack.callback_posted url=%s status=%s", url, resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("govstack.callback_post_failed url=%s exc=%s", url, exc)
```

**Wire tasks into views (changes to `govstack_views.py`):**

In `BulkPaymentView.post()`, after `GovStackBulkPaymentService.receive_batch()` returns the batch:
```python
from apps.payments.govstack_tasks import process_bulk_payment_batch
transaction.on_commit(lambda: process_bulk_payment_batch.delay(str(batch.pk)))
```

In `PrepaymentValidationView.post()`, after `validate_prepayment()` returns the pvr:
```python
from apps.payments.govstack_tasks import validate_prepayment_async
transaction.on_commit(lambda: validate_prepayment_async.delay(str(pvr.pk)))
```

**Add to `config/settings/base.py` `CELERY_TASK_ROUTES`:**
```python
"payments.process_bulk_payment_batch": {"queue": "payments"},
"payments.validate_prepayment_async":  {"queue": "payments"},
```

**Tests to add in `test_govstack_wave3.py` (use `@override_settings(CELERY_TASK_ALWAYS_EAGER=True)` or mock):**

```
G1:  BulkPaymentView.post() triggers process_bulk_payment_batch via on_commit
G2:  process_bulk_payment_batch transitions all CreditInstructions to COMPLETED
G3:  process_bulk_payment_batch sets BulkPaymentBatch.status = COMPLETED
G4:  process_bulk_payment_batch writes ACTION_BATCH_COMPLETED audit entry
G5:  process_bulk_payment_batch POSTs to callback_url when non-empty
G6:  process_bulk_payment_batch does NOT post when callback_url is empty
G7:  process_bulk_payment_batch does NOT include payee_functional_id in callback payload
G8:  process_bulk_payment_batch is idempotent (second call is a no-op)
G9:  PrepaymentValidationView.post() triggers validate_prepayment_async via on_commit
G10: validate_prepayment_async sets beneficiary_found=True for registered payee_functional_id
G11: validate_prepayment_async sets beneficiary_found=False for unknown payee_functional_id
G12: validate_prepayment_async transitions PrepaymentValidationRequest.status → COMPLETED
G13: validate_prepayment_async writes ACTION_VALIDATION_COMPLETED audit entry
G14: validate_prepayment_async POSTs correct callback payload shape
G15: validate_prepayment_async does NOT retry on callback POST failure (HTTP error is non-fatal)
G16: validate_prepayment_async is idempotent (second call is a no-op)
```

**Estimated effort:** 1–2 days (includes reading the harness Gherkin to determine if callbacks are tested, implementing tasks, wiring views, writing 16 tests).

---

### GAP-3 ✅ RESOLVED — `GOVSTACK_VOUCHER_REQUIRE_JWT` not enforced in production settings

**Resolved:** 2026-07-23 — `GOVSTACK_VOUCHER_REQUIRE_JWT = True` added to `config/settings/production.py`.

**File to modify:** `config/settings/production.py`

**Current state:** `govstack_auth.py` → `HasVoucherJWT.has_permission()` reads `getattr(settings, "GOVSTACK_VOUCHER_REQUIRE_JWT", False)`. Default is `False`. Neither `production.py` nor `base.py` sets this variable. Any caller can reach the voucher redemption (`POST /vouchers/voucher_redemption`) and status-check (`GET/PATCH /vouchers/voucherstatuscheck/<serial>`) endpoints without any authentication in production.

**Fix (add to `config/settings/production.py`):**
```python
# GovStack Payments BB — voucher endpoints JWT enforcement.
# Set to False only in GovStack harness test environments where the harness
# cannot supply a Bearer JWT. Must be True for real production deployments.
GOVSTACK_VOUCHER_REQUIRE_JWT = env.bool("GOVSTACK_VOUCHER_REQUIRE_JWT", default=True)
```

**Note on harness compatibility:** Confirm whether the GovStack harness sends a Bearer JWT for `voucher_redemption` and `voucherstatuscheck` calls before enabling. The harness environment should set `GOVSTACK_VOUCHER_REQUIRE_JWT=False` via env var; the real production environment sets it `True`. Do not hardcode `True` — the env var allows per-environment control.

**Tests to add in `test_govstack_wave4.py`:**
```
F37: GOVSTACK_VOUCHER_REQUIRE_JWT=True → unauthenticated POST to voucher_redemption → HTTP 403
F38: GOVSTACK_VOUCHER_REQUIRE_JWT=True → unauthenticated PATCH to voucherstatuscheck → HTTP 403
F39: GOVSTACK_VOUCHER_REQUIRE_JWT=True → unauthenticated GET to voucherstatuscheck → HTTP 403
F40: GOVSTACK_VOUCHER_REQUIRE_JWT=False (harness mode) → no auth required → HTTP 200
```

**Estimated effort:** 30 minutes (settings change + 4 tests).

---

### GAP-4 ✅ RESOLVED — `IsTrustedSourceBB` has no registered BB whitelist table

**Resolved:** 2026-07-23 — `GovStackRegisteredBB` model added to `govstack_models.py`; `IsTrustedSourceBB` updated to perform DB whitelist lookup when `GOVSTACK_REQUIRE_REGISTERED_BB=True`; migration `0021_govstack_registered_bb.py` written; `GOVSTACK_REQUIRE_REGISTERED_BB = True` added to `production.py`; admin registered.

**File to modify:** `apps/payments/govstack_auth.py`, `apps/payments/govstack_models.py`

**Current state:** `IsTrustedSourceBB.has_permission()` accepts any non-empty string ≤ 20 chars in the `X-Registering-Institution-ID` header. The comment says: *"Replace with a lookup against a GovStackRegisteredBB table in a future wave."* No such model or migration exists.

**This does NOT block harness certification** — the harness sends a fixed institution ID and the permissive check passes. Fix required for production hardening.

**Implementation:**

1. Add to `govstack_models.py`:
```python
class GovStackRegisteredBB(TimestampedModel):
    """
    Registry of GovStack Building Blocks authorised to call this BB's endpoints.
    Used by IsTrustedSourceBB permission class.
    """
    bb_id = models.CharField(
        max_length=20,
        unique=True,
        validators=[_BB_ID_VALIDATOR],
        verbose_name=_("BB Identifier"),
        help_text=_("Must match the X-Registering-Institution-ID header value sent by the BB."),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Active"))

    class Meta:
        verbose_name = _("GovStack Registered BB")
        verbose_name_plural = _("GovStack Registered BBs")
        ordering = ["bb_id"]

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"{self.bb_id} ({status})"
```

2. Migration `0021_govstack_registered_bb.py`

3. Update `IsTrustedSourceBB.has_permission()`:
```python
from apps.payments.govstack_models import GovStackRegisteredBB

def has_permission(self, request: Request, view: APIView) -> bool:
    institution_id = (
        request.headers.get("X-Registering-Institution-ID", "").strip()
        or request.headers.get("X-Registering-Institution-Id", "").strip()
    )
    if not institution_id or len(institution_id) > 20:
        return False
    return GovStackRegisteredBB.objects.filter(
        bb_id=institution_id, is_active=True
    ).exists()
```

4. Register `GovStackRegisteredBBAdmin` in `admin.py`.

5. Add to `seed_govstack_vouchers` command: create `GovStackRegisteredBB(bb_id="GS-HARNESS")` so the harness institution ID passes after the table is active.

**Tests to add:**
```
AUTH-1: IsTrustedSourceBB grants access when bb_id matches active GovStackRegisteredBB row
AUTH-2: IsTrustedSourceBB denies access when bb_id not in GovStackRegisteredBB table
AUTH-3: IsTrustedSourceBB denies access when bb_id matches inactive (is_active=False) row
AUTH-4: IsTrustedSourceBB denies access when header is missing
AUTH-5: IsTrustedSourceBB denies access when institution_id > 20 chars
```

**Estimated effort:** 4–6 hours (model + migration + permission update + admin + 5 tests).

---

### GAP-5 ✅ RESOLVED — Test file names do not match spec §18 convention

**Resolved:** 2026-07-23 — All test files renamed to match spec §18 convention; `test_govstack_tasks.py` extracted as a standalone file.

**Current files vs spec §18 requirement:**

| Spec §18 file | Current file | Tests |
|---|---|---|
| `test_govstack_beneficiary.py` | `test_govstack_wave2.py` | 80 |
| `test_govstack_bulk_payment.py` | `test_govstack_wave3.py` | 66 |
| `test_govstack_vouchers.py` | `test_govstack_wave4.py` | 91 |
| `test_govstack_p2g.py` | `test_govstack_wave5.py` | 100 |
| `test_govstack_models.py` | *(embedded in wave files)* | — |
| `test_govstack_services.py` | *(embedded in wave files)* | — |

**Harness impact:** None — the GovStack harness at `testing.govstack.global` runs HTTP calls, not Django test files. This is a spec compliance audit gap only.

**Recommendation:** Accept as-is for the harness run. When the GAP-2 tasks are implemented, add a `test_govstack_tasks.py` file for the new task tests. At that point, optionally rename the wave files to match the spec convention. Not a priority item.

**Estimated effort if renamed:** 2–3 hours (rename + update imports). No logic changes.

---

### Harness Submission Pre-Flight Checklist — Updated

This supersedes the checklist in §21.

**P0 — Must be done or harness will fail:**
- [ ] `apps/payments/management/commands/seed_govstack_vouchers.py` created and all F31–F36 tests pass
- [ ] `python manage.py seed_govstack_vouchers` run successfully against the harness DB immediately before submission
- [ ] DB confirmed: all 14 serials present in `GovStackVoucher` table with `status = preactivated`
- [ ] `test_govstack_wave4.py` F31–F36 tests all green
- [ ] Gherkin for `g2p_prepayment_validation.feature` and `g2p_bulk_payment.feature` reviewed for callback assertions (see GAP-2)

**P1 — Required only if harness verifies callback delivery:**
- [ ] `apps/payments/govstack_tasks.py` implemented (`process_bulk_payment_batch`, `validate_prepayment_async`)
- [ ] Tasks wired into `BulkPaymentView.post()` and `PrepaymentValidationView.post()` via `transaction.on_commit()`
- [ ] Task routes added to `config/settings/base.py` `CELERY_TASK_ROUTES`
- [ ] `test_govstack_wave3.py` G1–G16 tests all green

**Standard pre-flight (unchanged from §21):**
- [ ] All 9 feature files run locally against a clean DB with seeded data — zero failures
- [ ] `Content-Type: application/json` confirmed on all 14 endpoints (`curl -I` check)
- [ ] All responses return within 15,000 ms under simulated load
- [ ] No `payee_functional_id`, `financial_address`, or `voucher_secret` in any log line during harness run
- [ ] All custom error codes 452–464 exercised and correct
- [ ] `GOVSTACK_VOUCHER_REQUIRE_JWT=False` confirmed in the harness environment (not production)

**Post-harness — before production deployment:**
- [ ] `GOVSTACK_VOUCHER_REQUIRE_JWT = env.bool("GOVSTACK_VOUCHER_REQUIRE_JWT", default=True)` added to `config/settings/production.py` (GAP-3)
- [ ] F37–F40 JWT enforcement tests added and green
- [ ] `GovStackRegisteredBB` model + migration `0021` implemented (GAP-4)
- [ ] `IsTrustedSourceBB.has_permission()` updated to query `GovStackRegisteredBB` table (GAP-4)
- [ ] `GovStackRegisteredBBAdmin` registered in `admin.py` (GAP-4)
- [ ] `seed_govstack_vouchers` updated to also seed `GovStackRegisteredBB(bb_id="GS-HARNESS")` (GAP-4)
- [ ] AUTH-1 through AUTH-5 tests added and green

---

### Work Item Summary

| # | Gap | Priority | Blocks Harness | File(s) | Est. Effort |
|---|-----|----------|----------------|---------|-------------|
| GAP-1 | `seed_govstack_vouchers` management command | **P0** | **YES — all 5 Wave 4 features (9 harness features total)** | `apps/payments/management/commands/seed_govstack_vouchers.py` | 2–3 hrs |
| GAP-2 | Celery tasks + callback delivery (`govstack_tasks.py`) | **P1** | **MAYBE — read harness Gherkin first** | `apps/payments/govstack_tasks.py`, `govstack_views.py` | 0 hrs (review) + 1–2 days (if needed) |
| GAP-3 | `GOVSTACK_VOUCHER_REQUIRE_JWT` in production settings | P2 | No — deployment blocker | `config/settings/production.py` | 30 min |
| GAP-4 | `GovStackRegisteredBB` model + `IsTrustedSourceBB` hardening | P3 | No — post-certification | `govstack_models.py`, `govstack_auth.py`, migration `0021` | 4–6 hrs |
| GAP-5 | Test file naming to match spec §18 | P4 | No — cosmetic | `apps/payments/tests/test_govstack_*.py` | 2–3 hrs |
| GAP-6 | `voucherStatus` field returns lowercase DB value, spec expects title-case | **P0** | **YES — activation + cancellation harness scenarios** | `govstack_views.py` | 30 min |
| GAP-7 | GET /voucherstatuscheck invalid serial: wrong HTTP code + body shape | **P0** | **YES — status check negative harness scenario** | `govstack_views.py` | 1 hr |
| GAP-8 | Missing test files `test_govstack_models.py` and `test_govstack_services.py` | P3 | No — spec audit | `apps/payments/tests/` | 3–4 hrs |
| GAP-9 | `process_bulk_payment_batch` omits ID Mapper beneficiary lookup | P2 | Unlikely — harness tests HTTP response not task outcome | `govstack_tasks.py` | 2–3 hrs |
| GAP-10 | Voucher `value` field serialized as JSON string, spec requires JSON number | P1 | Maybe — depends on harness JSON schema strictness | `govstack_views.py` | 15 min |

**Status as of 2026-07-24: ALL GAPS RESOLVED.** GAP-1 through GAP-10, GAP-C1, and GAP-C2 are all implemented and committed. The GovStack Payments BB implementation is harness-ready. See the complete summary table at the end of this document.

---

### GAP-6 ✅ RESOLVED — `voucherStatus` field returns raw DB value (lowercase), harness expects title-cased label

**Added:** 2026-07-23, fresh gap audit following GAP-1–5 implementation.
**Resolved:** 2026-07-23 — commit `1b9455f` (`govstack_views.py`: use `get_status_display()` in `VoucherActivationView` and `VoucherStatusCheckView.patch`).

**Files to modify:** `apps/payments/govstack_views.py`

**Current state:**

`VoucherActivationView.patch()` (line ~676):
```python
"voucherStatus": voucher.status,   # ← returns "activated" (raw DB constant)
```

`VoucherStatusCheckView.patch()` / cancellation (line ~801):
```python
"voucherStatus": voucher.status,   # ← returns "cancelled" (raw DB constant)
```

**Spec requirement (§13.2 and §13.4):**
```json
{ "voucherStatus": "Activated" }   // activation
{ "voucherStatus": "Cancelled" }   // cancellation
```

**Root cause:** The internal DB status constants (`STATUS_ACTIVATED = "activated"`, `STATUS_CANCELLED = "cancelled"`) use lowercase snake_case, but the GovStack spec response examples show title-cased English labels. The current tests assert against `GovStackVoucher.STATUS_ACTIVATED` (so they pass), but the harness expects the title-cased string.

**Fix (two options):**

*Option A — Use `voucher.get_status_display()` (Django choices):*
```python
"voucherStatus": voucher.get_status_display(),  # → "Activated", "Cancelled", etc.
```
Django `STATUS_CHOICES` already has `(STATUS_ACTIVATED, _("Activated"))`, so `get_status_display()` returns `"Activated"` exactly as required.

*Option B — Explicit map in views:*
```python
_VOUCHER_STATUS_DISPLAY: dict[str, str] = {
    GovStackVoucher.STATUS_PREACTIVATED: "Preactivated",
    GovStackVoucher.STATUS_ACTIVATED: "Activated",
    GovStackVoucher.STATUS_CONSUMED: "Consumed",
    GovStackVoucher.STATUS_BLOCKED: "Blocked",
    GovStackVoucher.STATUS_SUSPENDED: "Suspended",
    GovStackVoucher.STATUS_CANCELLED: "Cancelled",
    GovStackVoucher.STATUS_PURGED: "Purged",
}
# In the view:
"voucherStatus": _VOUCHER_STATUS_DISPLAY.get(voucher.status, voucher.status),
```

**Preferred:** Option A — `get_status_display()` uses the model's canonical choices and eliminates duplication. Zero risk of the map drifting out of sync.

**Tests to add/fix in `test_govstack_vouchers.py`:**

Test B3 currently asserts `resp.data["voucherStatus"] == GovStackVoucher.STATUS_ACTIVATED` (= `"activated"`). Fix to assert `== "Activated"`. Same for E4 (`"Cancelled"`) and any other status assertions.

```
B3-fix: voucherStatus is "Activated" (title-case) in activation success response
E4-fix: voucherStatus is "Cancelled" (title-case) in cancellation success response
```

**Estimated effort:** 30 minutes (two-line view change + fix ~4 test assertions).

---

### GAP-7 ✅ RESOLVED — GET /voucherstatuscheck for invalid serial returns wrong HTTP code and wrong response body shape

**Added:** 2026-07-23, fresh gap audit.
**Resolved:** 2026-07-23 — commit `c367d7b` (`govstack_views.py`: catch `InvalidVoucherSerial` in `VoucherStatusCheckView.get()`, return HTTP 400 with `{status: 9, message: "Voucher not found.", serialNumber: ..., value: 0.0}`).

**File to modify:** `apps/payments/govstack_views.py`

**Current state:**

`VoucherStatusCheckView.get()` calls `GovStackVoucherService.get_status()`, which raises `InvalidVoucherSerial` (HTTP 456) on not-found:
```python
# Result when serial not found:
# HTTP 456, body: {"message": "Voucher serial number not found."}
```

**Spec requirement (§13.5):**

```
Failure response (HTTP 400, invalid serial):
{
  "status": 9,
  "message": "Voucher not found.",
  "serialNumber": "invalid",
  "value": 0.0
}
```

Two mismatches:
1. **HTTP code:** 456 vs required 400
2. **Body shape:** `{"message": "..."}` vs required `{"status": 9, "message": "...", "serialNumber": "<serial>", "value": 0.0}`

Note: This only affects the GET (status check) operation. The PATCH (cancellation) uses 463 and 464 which are correct per spec §13.4. For the GET, §13.5 and the harness's `voucher_status_check.feature` expect HTTP 400.

**Fix:**

In `VoucherStatusCheckView.get()`, catch `InvalidVoucherSerial` explicitly and return the correct body:

```python
from .govstack_exceptions import InvalidVoucherSerial

def get(self, request: Request, voucherserialnumber: str) -> Response:
    serial = str(voucherserialnumber).strip()
    try:
        voucher = GovStackVoucherService.get_status(serial_number=serial)
    except InvalidVoucherSerial:
        return Response(
            {
                "status": GovStackVoucher.STATUS_ERROR_INT,  # = 9
                "message": "Voucher not found.",
                "serialNumber": serial,
                "value": 0.0,
            },
            status=400,
        )
    return Response(
        {
            "status": voucher.status_int,
            "serialNumber": voucher.serial_number,
            "value": float(voucher.amount),  # See also GAP-10
        },
        status=200,
    )
```

Note that `InvalidVoucherSerial` is already imported in `govstack_views.py` (line 63), so no new import is needed.

**Tests to add in `test_govstack_vouchers.py`:**

```
D8:  GET /voucherstatuscheck with unknown serial → HTTP 400 (not 456)
D9:  GET /voucherstatuscheck with unknown serial → body contains "status": 9
D10: GET /voucherstatuscheck with unknown serial → body contains "serialNumber" = the submitted serial
D11: GET /voucherstatuscheck with unknown serial → body contains "value": 0.0
```

**Estimated effort:** 1 hour (view change + 4 tests).

---

### GAP-8 ✅ RESOLVED — Missing `test_govstack_models.py` and `test_govstack_services.py`

**Added:** 2026-07-23, fresh gap audit.
**Resolved:** 2026-07-23 — commit `9ecbb15` (created `apps/payments/tests/test_govstack_models.py` with 22 tests and `apps/payments/tests/test_govstack_services.py` with 37 tests).

**Files to create:**
- `apps/payments/tests/test_govstack_models.py` (min 10 tests)
- `apps/payments/tests/test_govstack_services.py` (min 15 tests)

**Spec reference:** §18.1 explicitly lists both files. §18.3 references them in the CI test command:
```bash
python manage.py test \
  apps.payments.tests.test_govstack_beneficiary \
  apps.payments.tests.test_govstack_bulk_payment \
  apps.payments.tests.test_govstack_vouchers \
  apps.payments.tests.test_govstack_p2g \
  apps.payments.tests.test_govstack_models \       ← MISSING
  apps.payments.tests.test_govstack_services \     ← MISSING
  --settings=config.settings.test
```

Running this command currently fails with `ModuleNotFoundError`.

**Current state:** Coverage for models and services is embedded in the wave test files. It is not absent, but it is not organized per spec §18.1.

**What `test_govstack_models.py` should cover (min 10 tests):**
1. `GovStackVoucher.transition_to()` — valid transition PREACTIVATED → ACTIVATED succeeds
2. `GovStackVoucher.transition_to()` — invalid transition CONSUMED → CANCELLED raises ValueError
3. `GovStackVoucher.status_int` — maps each status string to the correct integer (8 statuses)
4. `GovStackPaymentAuditEntry.save()` blocks update (sets pk → raises PermissionError)
5. `GovStackPaymentAuditEntry.delete()` raises PermissionError
6. `GovStackBeneficiary.__str__()` never includes payee_functional_id
7. `_generate_voucher_serial()` returns 6-digit string in range 100000–999999
8. `BulkPaymentBatch` STATUS constants exist and are used in choices
9. `CreditInstruction.unique_together` enforces (batch, instruction_id) uniqueness
10. `GovStackVoucher.expiry_date` is set by service to now + GOVSTACK_VOUCHER_EXPIRY_DAYS

**What `test_govstack_services.py` should cover (min 15 tests):**
1. `GovStackBeneficiaryService.register()` creates a beneficiary record
2. `GovStackBeneficiaryService.register()` is idempotent (upsert on same PayeeFunctionalID)
3. `GovStackBeneficiaryService.register()` writes audit entry ACTION_BENEFICIARY_REGISTERED
4. `GovStackBeneficiaryService.update()` writes audit entry ACTION_BENEFICIARY_UPDATED
5. `GovStackBulkPaymentService.receive_batch()` creates BulkPaymentBatch + CreditInstructions
6. `GovStackBulkPaymentService.receive_batch()` writes ACTION_BATCH_RECEIVED audit entry
7. `GovStackBulkPaymentService.receive_batch()` raises DuplicateBatchError on duplicate batch_id
8. `GovStackBulkPaymentService.validate_prepayment()` creates PrepaymentValidationRequest
9. `GovStackBulkPaymentService.validate_prepayment()` raises DuplicateValidationRequestError on duplicate
10. `GovStackVoucherService.preactivate()` creates voucher in PREACTIVATED status
11. `GovStackVoucherService.preactivate()` raises InvalidVoucherAmount (452) for amount ≤ 0
12. `GovStackVoucherService.preactivate()` raises InvalidVoucherGroup (454) for empty group
13. `GovStackVoucherService.activate()` transitions PREACTIVATED → ACTIVATED
14. `GovStackVoucherService.cancel()` raises VoucherAlreadyCancelled (464) for double-cancel
15. `GovStackVoucherService.get_status()` raises InvalidVoucherSerial (456) for unknown serial

**Harness impact:** None directly. The CI test command in §18.3 would fail without these files.

**Estimated effort:** 3–4 hours (two new test files, no new implementation).

---

### GAP-9 ✅ RESOLVED — `process_bulk_payment_batch` marks all instructions COMPLETED without ID Mapper lookup

**Added:** 2026-07-23, fresh gap audit.
**Resolved:** 2026-07-23 — commits `b4e9da8` + `077c2fa` (`govstack_tasks.py`: per-instruction `GovStackBeneficiary` lookup; batch status set to `COMPLETED`/`PARTIAL`/`FAILED` based on ID Mapper result; `completed_amount` and `failed_amount` correctly tallied).

**File:** `apps/payments/govstack_tasks.py`

**Spec requirement (§12.1 — Async processing):**
> After returning 200, enqueue a Celery task `process_bulk_payment_batch.delay(batch_pk)` that:
> 1. Looks up each `CreditInstruction`'s `PayeeFunctionalID` in `GovStackBeneficiary`
> 2. Marks instruction as `COMPLETED` if found, `FAILED` if not
> 3. Updates `BulkPaymentBatch.status` → `COMPLETED` or `PARTIAL` or `FAILED`
> 4. POSTs callback to `BulkPaymentBatch.callback_url` if set

**Current state (task line ~121–128):**
```python
CreditInstruction.objects.filter(
    batch=batch, status=CreditInstruction.STATUS_PENDING,
).update(status=CreditInstruction.STATUS_COMPLETED)
batch.status = BulkPaymentBatch.STATUS_COMPLETED
```
All pending instructions are marked COMPLETED regardless of whether their `payee_functional_id` exists in `GovStackBeneficiary`. `batch.failed_amount` is never updated. Batch status is always COMPLETED (never PARTIAL or FAILED).

**Harness impact:** LOW — the harness likely only verifies the HTTP 200 response of `POST /bulk-payment`, not the Celery task outcome (no harness scenario checks the batch status after async processing). However, this means the status check test G3b is testing a no-op batch where all succeed, not a realistic mixed scenario.

**Correct implementation:**
```python
with transaction.atomic():
    batch = BulkPaymentBatch.objects.select_for_update().get(pk=batch_pk)
    if batch.status != BulkPaymentBatch.STATUS_RECEIVED:
        return  # idempotent

    completed_count = failed_count = 0
    completed_amount = failed_amount = Decimal("0.00")

    for instr in CreditInstruction.objects.filter(
        batch=batch, status=CreditInstruction.STATUS_PENDING
    ).select_for_update():
        found = GovStackBeneficiary.objects.filter(
            payee_functional_id=instr.payee_functional_id,
            is_active=True,
        ).exists()

        if found:
            instr.status = CreditInstruction.STATUS_COMPLETED
            completed_count += 1
            completed_amount += instr.amount
        else:
            instr.status = CreditInstruction.STATUS_FAILED
            instr.failure_reason = "PayeeFunctionalID not found in ID Mapper."
            failed_count += 1
            failed_amount += instr.amount
            GovStackPaymentAuditEntry.objects.create(
                action=GovStackPaymentAuditEntry.ACTION_INSTRUCTION_FAILED,
                actor_bb_id=batch.source_bb_id,
                object_type="instruction",
                object_pk=str(instr.pk),
                request_id=batch.request_id,
                details={"failure_reason": instr.failure_reason},
            )
        instr.save(update_fields=["status", "failure_reason"])

    if failed_count == 0:
        batch.status = BulkPaymentBatch.STATUS_COMPLETED
    elif completed_count == 0:
        batch.status = BulkPaymentBatch.STATUS_FAILED
    else:
        batch.status = BulkPaymentBatch.STATUS_PARTIAL

    batch.completed_amount = completed_amount
    batch.failed_amount = failed_amount
    batch.result_generated_at = timezone.now()
    batch.save(update_fields=["status", "completed_amount", "failed_amount", "result_generated_at"])
    ...
```

**Tests to add in `test_govstack_tasks.py`:**

```
G2-update: process_bulk_payment_batch marks instruction COMPLETED only when payee_functional_id exists in GovStackBeneficiary
G3-update: process_bulk_payment_batch marks instruction FAILED and writes ACTION_INSTRUCTION_FAILED when payee_functional_id not in GovStackBeneficiary
G-new-1:   process_bulk_payment_batch sets BulkPaymentBatch.status = PARTIAL when some instructions succeed, some fail
G-new-2:   process_bulk_payment_batch sets BulkPaymentBatch.status = FAILED when all instructions fail (no beneficiaries exist)
G-new-3:   process_bulk_payment_batch sets batch.failed_amount correctly for failed instructions
```

**Note on existing G1–G16 tests:** Current G2 and G3 tests will still pass because the test DB has no GovStackBeneficiary rows, so all instructions should be FAILED after the fix. The existing G3b test (`completed_amount = total_amount`) will break because the test has no beneficiary registered, so `completed_amount` would become `0.00` after the fix. The `_make_batch()` helper in `test_govstack_tasks.py` must also register a matching `GovStackBeneficiary` row for G3b to work correctly. Implement this fix carefully to avoid breaking existing task tests.

**Estimated effort:** 2–3 hours (task implementation + update affected task tests).

---

### GAP-10 ✅ RESOLVED — Voucher `value` / bill `amount` fields serialized as JSON string, spec requires JSON number

**Added:** 2026-07-23, fresh gap audit.
**Resolved:** 2026-07-23 — commits `9b1ac36`, `08b7cb2`, `5b1b2ef` (`govstack_views.py`: `float(voucher.amount)` in `VoucherRedemptionView` and `VoucherStatusCheckView.get()`; `float(bill.amount)` and `float(payment.amount)` in all P2G views; test assertions updated to check `assertIsInstance(value, (int, float))`).

**Files to modify:** `apps/payments/govstack_views.py` (2 occurrences)

**Current state:**
```python
# VoucherRedemptionView.post() line ~731:
"value": str(voucher.amount),    # → "15.21" (JSON string)

# VoucherStatusCheckView.get() line ~786:
"value": str(voucher.amount),    # → "15.21" (JSON string)
```

**Spec requirement (§13.3 and §13.5):**
```json
{"value": 15.21}   // numeric float
{"value": 0.0}     // numeric float for not-found case
```

The GovStack spec uses `type: number` for `value` in both response schemas. DRF serializes Python `Decimal` values as strings in JSON by default (because Python `json` module doesn't natively handle `Decimal`). The fix is to convert to `float`:

```python
"value": float(voucher.amount),   # → 15.21 (JSON number)
```

**Harness impact:** MAYBE — depends on whether the harness validates `type: number` vs. `type: string`. If it does JSON Schema validation (`type: number`), string `"15.21"` would fail. If it just checks presence and does a loose equality check, it would pass.

**Tests to fix in `test_govstack_vouchers.py`:**

Current tests do `Decimal(resp.data["value"])` which works with both string and float. After the fix, tests should assert type is `float` or `int` (not `str`):
```python
# Before:
self.assertEqual(Decimal(resp.data["value"]), Decimal(AMOUNT))
# After (add type check):
self.assertIsInstance(resp.data["value"], (int, float))
self.assertAlmostEqual(resp.data["value"], float(AMOUNT))
```

**Estimated effort:** 15 minutes (2-line view change + test assertions).

---

### GAP-C1 ✅ RESOLVED — `voucher_preactivation` returns HTTP 400 instead of HTTP 453 for invalid ISO 4217 currency format

**Added:** 2026-07-24, post-review certifiability audit.

**Root cause:** `validate_voucher_currency()` in `VoucherPreactivationRequestSerializer` called `_validate_iso4217()` which raised `serializers.ValidationError` (HTTP 400) for any currency code that was not exactly 3 uppercase letters (e.g. `"US"`, `"USDD"`). The GovStack Payments spec mandates HTTP 453 (`InvalidVoucherCurrency`) for this error. `InvalidVoucherCurrency` existed in `govstack_exceptions.py` but was never raised anywhere.

**Fix:** Remove ISO 4217 format check from serializer; serializer now only enforces non-empty + `.upper()`. Move format validation into `GovStackVoucherService.preactivate()` using a module-level `_ISO4217_RE = re.compile(r"^[A-Z]{3}$")`, raising `InvalidVoucherCurrency()` (→ HTTP 453) when format is invalid.

Note: `_validate_iso4217()` at lines 253 and 334 (bulk-payment `CreditInstruction.Currency` fields) was deliberately NOT changed — HTTP 400 is correct for those paths.

**Resolved:** 2026-07-24 — commit `b919b34` (`govstack_serializers.py`, `govstack_services.py`; regression guard `test_a8_invalid_currency_format_returns_453` added to `test_govstack_vouchers.py`).

---

### GAP-C2 ✅ RESOLVED — `register-beneficiary` and `update-beneficiary-details` used `AllowAnyBB` — PII-handling endpoints unauthenticated

**Added:** 2026-07-24, post-review certifiability audit.

**Root cause:** `RegisterBeneficiaryView` and `UpdateBeneficiaryView` inherited `permission_classes = [AllowAnyBB]` from `GovStackG2PView`. `AllowAnyBB.has_permission()` is an unconditional `return True`. These endpoints process `PayeeFunctionalID` and `FinancialAddress` (PII). Any caller — without providing any identifying header — could register or update beneficiaries.

**Fix:** Both views now declare `permission_classes = [IsTrustedSourceBB]` explicitly, overriding the base class. `IsTrustedSourceBB` requires a non-empty `X-Registering-Institution-ID` header in all environments. When `GOVSTACK_REQUIRE_REGISTERED_BB=True` (production), it additionally performs a DB lookup against `GovStackRegisteredBB`.

**Resolved:** 2026-07-24 — commit `b919b34` (`govstack_views.py`; four test setUp methods updated to send `HTTP_X_REGISTERING_INSTITUTION_ID='GS-TEST'`; regression guards `test_a13_no_institution_header_returns_401` and `test_a14_no_institution_header_returns_401` added to `test_govstack_beneficiary.py`).

---

### Complete Work Item Summary — All GAPs Resolved (updated 2026-07-24)

| # | Gap | Priority | Blocks Harness | File(s) | Status |
|---|-----|----------|----------------|---------|--------|
| GAP-1 | `seed_govstack_vouchers` management command | **P0** | **YES — all 5 Wave 4 features** | `management/commands/seed_govstack_vouchers.py` | **DONE** |
| GAP-2 | Celery tasks + callback delivery | **P1** | **YES — async callback verification** | `govstack_tasks.py`, `govstack_views.py` | **DONE** |
| GAP-3 | `GOVSTACK_VOUCHER_REQUIRE_JWT` in production settings | P2 | No | `config/settings/production.py` | **DONE** |
| GAP-4 | `GovStackRegisteredBB` model + `IsTrustedSourceBB` hardening | P3 | No | multiple files | **DONE** |
| GAP-5 | Test file naming to match spec §18 | P4 | No | `apps/payments/tests/` | **DONE** |
| GAP-6 | `voucherStatus` lowercase vs title-case (activation + cancellation responses) | **P0** | **YES — activation + cancellation** | `govstack_views.py` | **DONE** (commit `1b9455f`) |
| GAP-7 | GET /voucherstatuscheck invalid serial: wrong HTTP status + body shape | **P0** | **YES — status check negative** | `govstack_views.py` | **DONE** (commit `c367d7b`) |
| GAP-8 | Missing `test_govstack_models.py` and `test_govstack_services.py` | P3 | No | `apps/payments/tests/` | **DONE** (commit `9ecbb15` — 22 + 37 tests) |
| GAP-9 | `process_bulk_payment_batch` omits ID Mapper beneficiary lookup | P2 | Unlikely | `govstack_tasks.py` | **DONE** (commits `b4e9da8`, `077c2fa`) |
| GAP-10 | Voucher `value` / bill `amount` fields serialized as JSON string, spec requires JSON number | **P1** | Maybe | `govstack_views.py` | **DONE** (commits `9b1ac36`, `08b7cb2`, `5b1b2ef`) |
| GAP-C1 | `voucher_preactivation` returns HTTP 400 instead of 453 for invalid ISO 4217 currency format | **P0** | **YES — preactivation negative scenario** | `govstack_services.py`, `govstack_serializers.py` | **DONE** (commit `b919b34`) |
| GAP-C2 | `register-beneficiary` and `update-beneficiary-details` used `AllowAnyBB` instead of `IsTrustedSourceBB` — PII endpoints unauthenticated | **P0** | **YES — beneficiary auth** | `govstack_views.py` | **DONE** (commit `b919b34`) |

**All 12 GAPs are resolved as of 2026-07-24. The GovStack Payments BB implementation is harness-ready.**

**Test suite: 1,572 passed, 0 failures** (full `apps/payments/` suite, verified 2026-07-24).

**Conditional risk (P1):** `IsTrustedSourceBB` on register/update-beneficiary requires the GovStack harness to send `X-Registering-Institution-ID` header (non-empty). The GovStack Payments spec mandates this header for these endpoints so the harness is expected to send it. In `GOVSTACK_REQUIRE_REGISTERED_BB=False` mode (default for testing) any non-empty value passes; in `True` mode (production) a DB lookup against `GovStackRegisteredBB` is performed. Not a code issue — cannot be verified until the harness is actually run.
