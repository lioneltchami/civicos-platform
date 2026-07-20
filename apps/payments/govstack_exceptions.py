"""
GovStack Payments BB — custom exception classes and DRF exception handler.

The GovStack Payments spec uses non-standard HTTP status codes for
domain-specific errors on Voucher endpoints. Standard DRF does not support
status codes outside 100–599; we subclass APIException and set status_code
directly.

Custom error codes (Voucher endpoints only):
  452 — Invalid voucher amount
  453 — Invalid voucher currency
  454 — Invalid voucher group
  456 — Invalid voucher serial number (not found on activation)
  460 — Gov_Stack_BB does not exist / not registered
  463 — Invalid serial number for cancellation
  464 — Voucher already cancelled (idempotent double-cancel)

All other errors use standard HTTP codes:
  400 — Missing required fields / malformed request (all endpoints)
  401 — Authentication required (Voucher redemption, status)
  403 — Insufficient permissions
  404 — Resource not found (P2G bill endpoints)

Error response shape for 452–464:
  {"message": "<human-readable explanation>"}

The harness checks that a "message" property is present on all error responses.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as _drf_exception_handler

if TYPE_CHECKING:
    from rest_framework.response import Response

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception classes
# ---------------------------------------------------------------------------

class InvalidVoucherAmount(APIException):
    """
    HTTP 452 — Invalid voucher amount.
    Raised when voucher_amount is zero, negative, or not a valid number.
    """
    status_code = 452
    default_code = "invalid_voucher_amount"
    default_detail = "Invalid voucher amount. Amount must be a positive number."


class InvalidVoucherCurrency(APIException):
    """
    HTTP 453 — Invalid voucher currency.
    Raised when voucher_currency is not a valid ISO 4217 3-letter code.
    """
    status_code = 453
    default_code = "invalid_voucher_currency"
    default_detail = "Invalid or unsupported voucher currency. Use a 3-letter ISO 4217 code."


class InvalidVoucherGroup(APIException):
    """
    HTTP 454 — Invalid voucher group.
    Raised when voucher_group is empty, missing, or does not exist.
    """
    status_code = 454
    default_code = "invalid_voucher_group"
    default_detail = "Invalid or unrecognised voucher group code."


class InvalidVoucherSerial(APIException):
    """
    HTTP 456 — Invalid voucher serial number (not found on activation).
    Raised when the provided serial number does not match any PREACTIVATED voucher.
    """
    status_code = 456
    default_code = "invalid_voucher_serial"
    default_detail = "Voucher serial number not found."


class GovStackBBNotFound(APIException):
    """
    HTTP 460 — Gov_Stack_BB does not exist or is not registered.
    Raised when the Gov_Stack_BB field in the request body is empty or unknown.
    """
    status_code = 460
    default_code = "gov_stack_bb_not_found"
    default_detail = "The specified Gov_Stack_BB does not exist or is not registered."


class InvalidCancellationSerial(APIException):
    """
    HTTP 463 — Invalid serial number for cancellation.
    Raised when the serial number in the PATCH /voucherstatuscheck/{serial}
    path does not match any voucher in the system.
    """
    status_code = 463
    default_code = "invalid_cancellation_serial"
    default_detail = "Invalid voucher serial number for cancellation."


class VoucherAlreadyCancelled(APIException):
    """
    HTTP 464 — Voucher already cancelled (idempotent double-cancel guard).
    Raised when the client attempts to cancel a voucher that is already in
    CANCELLED status.
    """
    status_code = 464
    default_code = "voucher_already_cancelled"
    default_detail = "This voucher has already been cancelled."


class DuplicateBatchError(Exception):
    """
    Raised by GovStackBulkPaymentService.receive_batch() when a Source BB
    submits a BatchID that already exists in BulkPaymentBatch.

    This is NOT an APIException — it is a domain exception caught at the view
    layer and converted into a G2P envelope error response (HTTP 400,
    ResponseCode "01").  Using a plain Exception (not APIException) keeps the
    service layer decoupled from HTTP concerns.

    BatchID uniqueness is enforced by the DB unique constraint on
    BulkPaymentBatch.batch_id.  If the same BatchID arrives twice — whether
    from a Source BB retry or a genuine duplicate — we surface this as a
    controlled error rather than letting IntegrityError propagate as an
    unhandled 500.

    The batch_id is stored as an attribute but intentionally omitted from the
    exception message string — if this exception is captured by an error reporter
    (e.g. Sentry), the batch_id should not appear in the message alongside any
    other request context that could include PII.
    """

    def __init__(self, batch_id: str) -> None:
        self.batch_id = batch_id
        super().__init__("Batch ID has already been received.")


class DuplicateValidationRequestError(Exception):
    """
    Raised by GovStackBulkPaymentService.validate_prepayment() when a Source BB
    submits a RequestID that already exists in PrepaymentValidationRequest.

    This is NOT an APIException — it is a domain exception caught at the view
    layer and converted into a G2P envelope error response (HTTP 200,
    ResponseCode "01").

    RequestID uniqueness is enforced by the DB unique constraint on
    PrepaymentValidationRequest.request_id.  Retries or replays with the same
    RequestID are surfaced as a controlled G2P error rather than an unhandled 500.

    The /prepayment-validation endpoint MUST always return HTTP 200 (even for
    errors), so this exception must be caught in the view and converted to a
    ResponseCode "01" response at HTTP 200 — unlike DuplicateBatchError which
    returns HTTP 400.

    The request_id is stored as an attribute but intentionally omitted from the
    exception message string for the same reason as DuplicateBatchError.
    """

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__("Prepayment validation request ID has already been received.")


# ---------------------------------------------------------------------------
# Exception handler
# ---------------------------------------------------------------------------

def govstack_exception_handler(exc: Exception, context: dict) -> Response | None:
    """
    Custom DRF exception handler for GovStack Payments endpoints.

    Normalises all error response bodies to the GovStack-required shape:
        {"message": "<human-readable explanation>"}

    The harness validates that every error response contains a "message" key.
    Standard DRF uses "detail" as the key; this handler remaps it.

    Registration:
        In govstack_views.py each view sets:
            settings.REST_FRAMEWORK is NOT modified globally.
        Instead, views override get_exception_handler() individually,
        or the DEFAULT_EXCEPTION_HANDLER is set in the govstack namespace
        via view-level dispatch (see GovStackAPIView base class).

    Usage in views:
        from apps.payments.govstack_exceptions import govstack_exception_handler

        class MyView(GovStackAPIView):
            ...
        # GovStackAPIView.get_exception_handler() returns govstack_exception_handler
    """
    response = _drf_exception_handler(exc, context)

    if response is not None:
        raw = response.data

        # Normalise to {"message": "..."} for all GovStack error responses.
        if isinstance(raw, dict):
            # DRF standard: {"detail": "..."} or {"field": ["error"]}
            if "detail" in raw:
                message = str(raw["detail"])
            elif "message" in raw:
                # Already normalised (e.g. from a custom exception handler round-trip)
                message = str(raw["message"])
            else:
                # Validation errors: {"field": ["msg1", "msg2"]}
                parts = []
                for field, errors in raw.items():
                    if isinstance(errors, list):
                        parts.append(f"{field}: {'; '.join(str(e) for e in errors)}")
                    else:
                        parts.append(f"{field}: {errors}")
                message = " | ".join(parts) if parts else "An error occurred."
        elif isinstance(raw, list):
            message = " ".join(str(item) for item in raw)
        elif isinstance(raw, str):
            message = raw
        else:
            message = "An error occurred."

        response.data = {"message": message}

    return response


# ---------------------------------------------------------------------------
# G2P exception handler
# ---------------------------------------------------------------------------

def govstack_g2p_exception_handler(exc: Exception, context: dict) -> Response | None:
    """
    Custom DRF exception handler for G2P (Government-to-Person) endpoints.

    G2P error responses must follow the G2P envelope shape required by the
    GovStack harness g2pResponseSchema:
        {
          "ResponseCode":      "01",        # exactly "00" or "01"
          "RequestID":         "<echoed>",  # from request body, exactly 12 chars in harness
          "ResponseDescription": "<text>"   # 1–200 chars
        }

    This is fundamentally different from the Voucher/P2G handler which returns
    {"message": "..."}.  G2P views (RegisterBeneficiaryView, UpdateBeneficiaryView)
    handle validation errors manually via _g2p_ok/_g2p_bad, but this handler
    catches unexpected exceptions (throttle, auth, unexpected server errors) and
    wraps them in the same G2P envelope so the harness schema check is never
    violated.

    Registered on GovStackG2PView via get_exception_handler().
    """
    response = _drf_exception_handler(exc, context)

    if response is not None:
        # Extract RequestID from the request body for echo-back.
        request = context.get("request")
        request_id = ""
        if request is not None:
            try:
                data = request.data
                if isinstance(data, dict):
                    request_id = str(data.get("RequestID", ""))
            except Exception:
                pass  # malformed body — RequestID not available

        # Build a human-readable description from the DRF error data.
        raw = response.data
        if isinstance(raw, dict) and "detail" in raw:
            description = str(raw["detail"])
        elif isinstance(raw, dict):
            parts = []
            for field, errors in raw.items():
                if isinstance(errors, list):
                    parts.append(f"{field}: {'; '.join(str(e) for e in errors)}")
                else:
                    parts.append(f"{field}: {errors}")
            description = " | ".join(parts) if parts else "An error occurred."
        elif isinstance(raw, str):
            description = raw
        else:
            description = "An error occurred."

        response.data = {
            "ResponseCode": "01",
            "RequestID": request_id,
            "ResponseDescription": description[:200],
        }

    return response
