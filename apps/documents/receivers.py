"""
Document Management Building Block — Signal Receivers.

Connects handlers to signals declared in apps.documents.signals.
This module is auto-imported by DocumentsConfig.ready() once it exists
(guarded by importlib.util.find_spec so the app starts safely if the
file is absent during earlier waves).

Privacy invariants (enforced throughout):
  - No PII (names, email, filename) is ever written to logs.
  - Document is referenced by pk only.
  - Actor/uploader is referenced by pk only.

Receivers MUST be idempotent — they can be called more than once if a
signal is sent with send_robust() and the task is retried.

Governing law: PIPEDA clause 4.5.3, Privacy Act s.6(1) & s.6(3),
               TBS SPIN 2023-06-13.
"""

import logging

from django.dispatch import receiver

from apps.documents.signals import (
    document_hard_deleted,
    document_legal_hold_changed,
    document_soft_deleted,
)

logger = logging.getLogger(__name__)


# ─── document_soft_deleted ────────────────────────────────────────────────────


@receiver(document_soft_deleted, weak=False)
def on_document_soft_deleted(
    sender,
    *,
    document_pk: str,
    deleted_by_id,
    **kwargs,
) -> None:
    """
    Fires after a document is soft-deleted and the DB transaction commits.

    Responsibilities (current wave):
      - Emit a structured INFO log for operator visibility and SIEM ingestion.
      - Serve as a hook for future cross-building-block wiring
        (e.g. notify Case Management BB that a linked document was deleted).

    Privacy: document_pk and deleted_by_id (pk, not email) only.
    """
    logger.info(
        "document_soft_deleted: document_pk=%s deleted_by_pk=%s",
        document_pk,
        deleted_by_id,
    )
    # Future: dispatch async task to notify linked BBs (Case Mgmt, Permits, etc.)
    # Future: update search index to remove document from citizen-facing results


# ─── document_hard_deleted ────────────────────────────────────────────────────


@receiver(document_hard_deleted, weak=False)
def on_document_hard_deleted(
    sender,
    *,
    document_pk: str,
    category_slug: str,
    **kwargs,
) -> None:
    """
    Fires after a document's storage object is irreversibly purged (NIST SP 800-88
    §11.2 hard delete) and the surrounding transaction commits.

    Signal kwargs (per signals.py declaration):
      - document_pk (str)  — the document's UUID pk as a string.
      - category_slug (str) — the document's category slug (captured under lock).

    Responsibilities (current wave):
      - Emit a structured INFO log for PIPEDA 4.5.3 operator audit trail.
      - Hook for future cross-BB cleanup (revoke cached CDN URLs, etc.)

    Privacy: document_pk only. category_slug carries no PII.
    NIST SP 800-88 §11.2: the DB row is RETAINED for audit; only the
    _storage_key column is cleared by the service before this signal fires.
    """
    logger.info(
        "document_hard_deleted: document_pk=%s category_slug=%s",
        document_pk,
        category_slug,
    )
    # Future: purge any CDN edge-cache entries for this document's presigned URL
    # Future: notify Case Management BB that the physical record is gone


# ─── document_legal_hold_changed ──────────────────────────────────────────────


@receiver(document_legal_hold_changed, weak=False)
def on_document_legal_hold_changed(
    sender,
    *,
    document_pk: str,
    legal_hold: bool,
    set_by_id,
    **kwargs,
) -> None:
    """
    Fires after a legal hold is applied or released and the DB transaction commits.

    Responsibilities (current wave):
      - Emit a structured INFO log for PIPEDA 4.5.3 / Privacy Act s.6(3) compliance.
      - Hook for future Privacy Officer notification workflow
        (e.g. email Privacy Officer queue via Notification BB — NOT inline here,
        must be an async task to avoid blocking the commit callback).

    Privacy: document_pk and set_by_id (pk, not email) only.

    NOTE: Do NOT send emails or call external services directly in this receiver.
    All side-effects with I/O must be dispatched as Celery tasks to avoid:
      1. Blocking the on_commit callback thread.
      2. Losing the notification if the email server is temporarily unavailable.
    """
    action = "APPLIED" if legal_hold else "RELEASED"
    logger.info(
        "document_legal_hold_changed: document_pk=%s hold=%s action=%s set_by_pk=%s",
        document_pk,
        legal_hold,
        action,
        set_by_id,
    )
    # Future: dispatch notify_privacy_officer_legal_hold_changed.delay(
    #     document_pk=document_pk,
    #     legal_hold=legal_hold,
    #     set_by_id=set_by_id,
    # )
