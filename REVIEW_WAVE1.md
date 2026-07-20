# Code Review: GovStack Payments BB — Wave 1

**Reviewer:** Claude (automated deep review)
**Date:** 2026-07-19
**Files reviewed:** 10 (8 new + 2 modified)

---

## Summary

Wave 1 lays a solid foundation. The architecture is clean, the security invariants (encrypted fields, PII-free logs, append-only audit) are correctly implemented, and the layer is properly isolated from the CivicOS-internal payments layer. Six issues were found and **fixed inline** during this review. Four more are documented for Wave 2–4 attention.

---

## Issues Found and Fixed

All fixes were applied directly to source files. `manage.py check` passes with 0 errors after all changes.

### 1. `govstack_models.py` — Unused import: `timezone`
**Severity:** 🟡 Minor (linting / clean build)
`from django.utils import timezone` was imported but never referenced anywhere in the file.
**Fix:** Removed the import.

---

### 2. `govstack_models.py` — Redundant `db_index=True` on 4 unique fields
**Severity:** 🟡 Minor (schema bloat, misleading)
`unique=True` already instructs PostgreSQL to create a unique B-tree index. Adding `db_index=True` on the same field generates a redundant second index entry in the migration and is confusing to future maintainers.

Affected fields: `GovStackBeneficiary.payee_functional_id`, `BulkPaymentBatch.batch_id`, `PrepaymentValidationRequest.request_id`, `GovStackVoucher.serial_number`.

**Fix:** Removed `db_index=True` from all four, left a clarifying comment.

> **Migration note:** The existing `0016_govstack_models.py` migration already ran and created those fields with both `unique=True` and `db_index=True` — in PostgreSQL the redundant index is silently ignored (the unique constraint wins). A squash migration to remove the redundancy is low priority but recommended before the first production deploy.

---

### 3. `govstack_models.py` — `CreditInstruction.__str__` returns FK UUID, not batch_id string
**Severity:** 🟡 Minor (admin/debug usability)
`f"Instruction {self.instruction_id} [...] (batch {self.batch_id})"` — on a ForeignKey field, `self.batch_id` returns the UUID of the related `BulkPaymentBatch` row, NOT the batch's human-readable `batch_id` string. In the Django admin the string reads `(batch 3f8a…-uuid)` which is unhelpful.

**Fix:** Added a comment clarifying this and changed the label to `batch pk=` so the output is unambiguous. Accessing `self.batch.batch_id` would be correct but triggers a DB query on every `__str__` call; using `pk=` keeps it query-free.

---

### 4. `govstack_exceptions.py` — Unnecessary runtime import of `Response`
**Severity:** 🟡 Minor (unused import)
`from rest_framework.response import Response` was only needed for the type annotation `-> Response | None`. Because the file has `from __future__ import annotations`, all annotations are strings at runtime and the import is not executed — but it still appeared in the import block and would be flagged by linters.

**Fix:** Moved to `TYPE_CHECKING` guard: `if TYPE_CHECKING: from rest_framework.response import Response`.

---

### 5. `govstack_serializers.py` — `BeneficiaryItemSerializer.FinancialAddress` max_length mismatch
**Severity:** 🔴 Bug (data loss / silent truncation risk)
The serializer declared `max_length=30` but the model field (`EncryptedCharField`) is `max_length=512`. IBANs can be up to 34 characters for some countries (e.g. Saudi Arabia = 24, UAE = 23, Malta = 31, Mauritania = 27). A valid 31-character IBAN would be rejected at the serializer level with HTTP 400 even though it is perfectly legal per spec.

**Fix:** Changed `max_length=30` → `max_length=512` to match the model.

---

### 6. `govstack_serializers.py` — `validate_voucher_currency` skipped the ISO 4217 regex
**Severity:** 🔴 Bug (harness will fail)
`VoucherPreactivationRequestSerializer.validate_voucher_currency` only called `.upper()` and returned, without applying `_validate_iso4217`. A caller sending `voucher_currency: "12"` or `"US"` would bypass serializer validation entirely. The harness expects HTTP 453 from the service layer for unsupported currencies, but syntactically invalid codes (non-alphabetic, wrong length) should be rejected at the serializer level with HTTP 400.

**Fix:** Pipe the uppercased value through `_validate_iso4217()` which checks `^[A-Z]{3}$`.

---

### 7. `govstack_serializers.py` — Double validation on `PayeeFunctionalID`
**Severity:** 🟡 Minor (wasted CPU on every request)
`BeneficiaryItemSerializer.PayeeFunctionalID` had both `validators=[_validate_payee_id]` on the field declaration AND a `validate_PayeeFunctionalID()` method that also calls `_validate_payee_id`. The function ran twice on every beneficiary item — harmless but wasteful.

**Fix:** Removed the `validators=[...]` kwarg from the field declaration; the per-field method is sufficient and is the DRF-idiomatic pattern.

---

### 8. `govstack_auth.py` — Lazy settings import inside `has_permission()`
**Severity:** 🟡 Minor (performance)
`HasVoucherJWT.has_permission()` did `from django.conf import settings` on every request. Django settings are a module-level singleton; importing them at the top of the file is both correct and faster.

**Fix:** Moved `from django.conf import settings` to module-level imports.

---

### 9. `govstack_auth.py` — Docstring said "HTTP 403", but DRF returns 401
**Severity:** 🟡 Minor (misleading documentation)
When an unauthenticated (anonymous) request fails `IsTrustedSourceBB`, DRF returns HTTP 401 (`NotAuthenticated`) not 403 (`PermissionDenied`), because no authentication was attempted. This was discovered during Wave 1 testing (the test was updated from `assert 403` to `assert 401`), but the docstring still said "HTTP 403".

**Fix:** Corrected docstring to say "HTTP 401".

---

### 10. `govstack_views.py` + `settings/base.py` — `throttle_scope` was silently ignored
**Severity:** 🔴 Bug (rate limiting not active)
`GovStackAPIView` set `throttle_scope = "govstack_bb"` but:

- `DEFAULT_THROTTLE_CLASSES` contains `CitizenRateThrottle` and `AnonRateThrottle`, not `ScopedRateThrottle`. Without `ScopedRateThrottle` in the throttle chain, `throttle_scope` is silently ignored.
- `"govstack_bb"` was not present in `DEFAULT_THROTTLE_RATES`, so even if `ScopedRateThrottle` were active, it would raise `ImproperlyConfigured`.

**Fix (two changes):**
1. Added `throttle_classes = [ScopedRateThrottle]` explicitly on `GovStackAPIView` so it does not depend on the global default (which controls citizen flows and should not be modified).
2. Added `"govstack_bb": "100/minute"` to `DEFAULT_THROTTLE_RATES` in `base.py`.

---

## Remaining Known Issues (Not Fixed — Deferred to Implementation Waves)

### R1 — `validate_voucher_amount` raises HTTP 400 instead of HTTP 452
**File:** `govstack_serializers.py:273`
**Deferred to:** Wave 4
The harness expects HTTP 452 (`InvalidVoucherAmount`) for `voucher_amount <= 0`. The current serializer raises `serializers.ValidationError` (HTTP 400) for this case. For Wave 4, remove the `<= 0` check from the serializer and let `GovStackVoucherService.preactivate()` raise `InvalidVoucherAmount`. A comment has been added documenting this.

Non-numeric values (e.g. `"abc"`) are correctly rejected by `DecimalField` with HTTP 400, which IS the correct behaviour.

---

### R2 — `BulkPaymentRequestSerializer.validate_SourceBBID` passes "invalid"
**File:** `govstack_serializers.py:190`
**Deferred to:** Wave 3
The harness bulk-payment negative scenario sends `SourceBBID: "invalid"` and expects HTTP 400. The current regex `^[a-zA-Z0-9\-]{1,20}$` accepts "invalid" (7 alpha chars). If the harness expects rejection based on length or format, this will fail. Investigation required: does the harness expect rejection because "invalid" is 7 chars (not 10) or because of a different constraint? The view/service layer must enforce this if the serializer cannot.

---

### R3 — Voucher serial collision has no retry loop
**File:** `govstack_models.py:_generate_voucher_serial`
**Deferred to:** Wave 4
The function generates 6-digit serials (900,000 possibilities). The DB `unique=True` constraint prevents duplicates, but an `IntegrityError` will surface if a collision occurs. By the birthday paradox, collisions become likely around ~950 vouchers. Wave 4's `GovStackVoucherService.preactivate()` must catch `IntegrityError` and retry with a fresh serial, up to a bounded retry count.

---

### R4 — No unit tests for Wave 1 components
**Deferred to:** Wave 2 (as prerequisite)
There are no tests for: `GovStackVoucher.transition_to()` state machine, `GovStackPaymentAuditEntry` append-only enforcement, `govstack_exception_handler` normalisation, serializer validation edge cases, or permission class behaviour. These should be written before Wave 2 implementation begins — the foundation tests catch regressions as each wave is added.

---

## What Looks Good ✅

- **Layer isolation is complete.** Zero cross-references between `govstack_*.py` and `models.py` / `views/` / `urls.py`. The `"Currency always CAD"` invariant in `models.py` is correctly absent from govstack models.
- **Encryption at rest.** `financial_address` and `voucher_secret` both use `EncryptedCharField` (Fernet AES-128). The migration correctly reflects `BinaryField` storage.
- **PII discipline.** `payee_functional_id` and `financial_address` are absent from every `__str__`, every admin `list_display`, every response serializer, and every log line. `voucher_secret` likewise.
- **Append-only audit.** `GovStackPaymentAuditEntry.save()` and `.delete()` both raise `PermissionError` after first creation. The pattern is correct and well-documented.
- **State machine design.** `ALLOWED_TRANSITIONS`, `STATUS_INT_MAP`, `transition_to()`, `is_terminal` — clean, explicit, and extensible for Wave 4.
- **Custom HTTP codes.** APIException subclasses with explicit `status_code` attributes are the right DRF pattern. The handler correctly normalises all shapes to `{"message": "..."}`.
- **`govstack_exception_handler` coverage.** Handles dict+detail, dict+field-errors, list, and string — all four shapes DRF can produce.
- **URL harness alignment.** `voucher_preactivation` (underscore), `voucherstatuscheck` (one word), `billTransferRequests` (camelCase) all match the harness `@endpoint` annotations exactly.
- **Service stubs.** All method signatures are correctly typed with all required parameters for Waves 2–5. `NotImplementedError` messages include the target wave for quick searchability.
- **Admin security.** All 6 govstack admin classes are read-only (no add/change/delete). PII fields show "✓ Set"/"✗ Not set" instead of values. `payee_functional_id` is absent from `search_fields`.
- **`_generate_voucher_serial` uses `secrets`.** Correct — avoids `random` for security-sensitive serial generation.
- **Multi-currency support.** `_ISO4217_VALIDATOR` accepts any 3-letter ISO 4217 code. No CAD restriction. DecimalField everywhere — no FloatField.
- **Migration quality.** All 8 named indexes present. `unique_together` for `CreditInstruction`. No alterations to existing tables. Clear dependency chain.

---

## Verdict

**✅ Approved with fixes applied** — all 10 fixable issues have been corrected inline. The 4 remaining items are deliberately deferred and documented. Wave 1 foundation is solid and ready for Wave 2 implementation.
