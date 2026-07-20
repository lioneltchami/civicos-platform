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
