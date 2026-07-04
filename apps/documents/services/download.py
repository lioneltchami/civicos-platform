"""
Document Management Building Block — Download service.

Handles secure document download via single-use access tokens.

Download flow (spec §12.3):
  1. issue_access_token()   — create a DocumentAccessToken for an authorised user.
                               View: GET /api/v1/documents/{pk}/download/
                               Returns: { token_url, expires_at }
  2. consume_access_token() — validate and redeem the token; return the Document.
                               View: GET /api/v1/documents/dl/{token}/
                               Returns: HTTP 302 (prod) or HTTP 200 file stream (dev).

IDOR prevention (CRITICAL — must never be changed):
  - Non-owned document PKs return 404, not 403 (403 confirms existence).
  - Citizens may only download ACTIVE documents they uploaded or are authorised to view.
  - Token failures always return 404, never 403 (IDOR rule applies to token redemption too).

PIPEDA constraints (CRITICAL — must be preserved):
  - storage_key is NEVER returned to clients or written to logs.
  - original_filename is NEVER in audit event_detail.
  - Audit event_detail for downloads contains only: doc_pk, token_pk, ip_masked.
  - IP address is stored masked: IPv4 last octet zeroed; IPv6 last 80 bits zeroed.

Governing law: PIPEDA clause 4.7.5, Privacy Act s.6(1), OWASP IDOR guidance.
"""

from __future__ import annotations

import ipaddress
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document, DocumentAccessToken

    User = get_user_model()

logger = logging.getLogger(__name__)

# Default access token TTL in seconds (5 minutes).
# Override via CIVICOS['DOCUMENT_ACCESS_TOKEN_TTL_SECONDS'].
_DEFAULT_TOKEN_TTL: int = 300

# Files ≤ this size are proxied directly by Django (dev and small files).
# Files > this size trigger a fresh presigned URL redirect in production.
# Prevents Django memory pressure on large files.
_PROXY_SIZE_THRESHOLD_BYTES: int = 1 * 1024 * 1024  # 1 MB


# ─────────────────────────────────────────────────────────────────────────────
# Public service functions
# ─────────────────────────────────────────────────────────────────────────────


def issue_access_token(
    *,
    user: "User",
    document: "Document",
    ip_address: str | None = None,
) -> "DocumentAccessToken":
    """
    Issue a single-use DocumentAccessToken for an authorised download.

    Validates:
      - document.scan_status == ACTIVE (citizens cannot download non-ACTIVE docs).
      - document.deleted_at is None (soft-deleted docs are inaccessible).
      - User is authorised: either the uploader, or staff with view_document perm.

    IDOR prevention:
      - Non-owned PKs return Http404 (not 403). The view calling this function
        should already have confirmed ownership; this service enforces it again
        as a defence-in-depth layer.

    IP masking:
      - IPv4: last octet zeroed (e.g. 192.168.1.100 → 192.168.1.0)
      - IPv6: last 80 bits zeroed (equivalent to /48 prefix)

    Writes an audit log entry (data.viewed) for every token issuance.
    The token URL is returned to the client; the storage_key is NEVER exposed.

    Args:
        user:       The authenticated user requesting the download.
        document:   The Document to download (already fetched by the view).
        ip_address: Caller's IP address (extracted from request by the view).

    Returns:
        A fresh DocumentAccessToken with is_valid=True.

    Raises:
        Http404: Document is not ACTIVE, is soft-deleted, or user is not authorised.
                 404 is used for all failure cases (IDOR prevention).
    """
    from apps.audit.models import AuditEventType
    from apps.audit.services import record_event
    from apps.documents.models import Document, DocumentAccessToken

    # ── Scan status + soft-delete gate ────────────────────────────────────────
    # Citizens may ONLY download ACTIVE documents. PENDING_UPLOAD, SCANNING,
    # QUARANTINED, and DELETED documents are all inaccessible.
    # Return 404 in all failure cases (IDOR: 403 would confirm document existence).
    if document.scan_status != Document.ScanStatus.ACTIVE or document.deleted_at is not None:
        raise Http404

    # ── Ownership / permission check ──────────────────────────────────────────
    # Raises Http404 (not 403) for IDOR compliance.
    if not _user_may_download(user=user, document=document):
        raise Http404

    # ── Create token ──────────────────────────────────────────────────────────
    civicos: dict = getattr(settings, "CIVICOS", {})
    ttl_seconds: int = civicos.get("DOCUMENT_ACCESS_TOKEN_TTL_SECONDS", _DEFAULT_TOKEN_TTL)
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
    masked_ip = _mask_ip(ip_address)

    token = DocumentAccessToken.objects.create(
        document=document,
        issued_to=user,
        expires_at=expires_at,
        ip_address=masked_ip,
        # `token` field uses default=_generate_token — 64-char cryptographic hex.
        # `used_at` starts as None (unused).
    )

    # ── Audit log ─────────────────────────────────────────────────────────────
    # PIPEDA constraints on event_detail:
    #   - NO original_filename (may contain PII)
    #   - NO storage_key (internal S3 path)
    #   - NO uploader email or full name
    #   Only doc_pk, token_pk, and ip_masked are recorded.
    try:
        record_event(
            event_type=AuditEventType.RECORD_VIEWED,
            actor_id=str(user.pk),
            resource_type="documents.Document",
            resource_id=str(document.pk),
            event_detail={
                "document_pk": str(document.pk),
                "token_pk": str(token.pk),
                "ip_masked": masked_ip or "",
                "action": "token_issued",
            },
        )
    except Exception:
        # Audit failure must NEVER cause the citizen's download to fail.
        logger.exception(
            "issue_access_token: audit write failed for doc pk=%s token pk=%s; "
            "token issuance unaffected.",
            document.pk,
            token.pk,
        )

    return token


def consume_access_token(
    *,
    token_value: str,
    user: "User",
) -> "Document":
    """
    Validate and consume a DocumentAccessToken, returning the linked Document.

    Single-use enforcement: sets ``used_at=timezone.now()`` under
    ``select_for_update()`` inside ``atomic()`` to prevent concurrent redemption
    of the same token (TOCTOU race condition prevention).

    Writes an audit log entry (data.viewed) for every successful token redemption.

    Args:
        token_value: The opaque 64-char hex token string from the URL.
        user:        The authenticated user presenting the token.

    Returns:
        The Document linked to the token.

    Raises:
        Http404: Token not found, expired, already used, or belongs to a
                 different user. Always 404, never 403 (IDOR prevention).
    """
    from apps.audit.models import AuditEventType
    from apps.audit.services import record_event
    from apps.documents.models import DocumentAccessToken

    # ── Atomic single-use enforcement ─────────────────────────────────────────
    # select_for_update() prevents two concurrent requests from both seeing
    # used_at=None and both succeeding. The second request will block until the
    # first commits used_at, then see the token as already used.
    with transaction.atomic():
        try:
            token = (
                DocumentAccessToken.objects
                .select_for_update()
                .select_related("document", "issued_to")
                .get(token=token_value)
            )
        except DocumentAccessToken.DoesNotExist:
            # 404: token not found. IDOR: never reveal whether the token exists.
            raise Http404

        # ── Cross-user isolation ───────────────────────────────────────────────
        # A token issued to user A cannot be redeemed by user B.
        # Use 404 not 403 (IDOR: 403 confirms the token exists).
        if token.issued_to_id != user.pk:
            raise Http404

        # ── Validity check (under lock) ────────────────────────────────────────
        # Re-evaluate is_valid under the lock to guard against race conditions:
        # another request may have consumed the token between the initial
        # fetch and this check (though select_for_update serialises this).
        if not token.is_valid:
            # Expired or already used: return 404 (same as invalid token).
            raise Http404

        # ── Mark as used (single-use enforcement) ─────────────────────────────
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])

    # ── Audit log (after commit) ───────────────────────────────────────────────
    # PIPEDA: event_detail contains only doc_pk, token_pk — no PII.
    try:
        record_event(
            event_type=AuditEventType.RECORD_VIEWED,
            actor_id=str(user.pk),
            resource_type="documents.Document",
            resource_id=str(token.document_id),
            event_detail={
                "document_pk": str(token.document_id),
                "token_pk": str(token.pk),
                "version_number": token.document.version_number,
                "action": "token_redeemed",
            },
        )
    except Exception:
        logger.exception(
            "consume_access_token: audit write failed for doc pk=%s token pk=%s; "
            "download unaffected.",
            token.document_id,
            token.pk,
        )

    return token.document


def generate_presigned_download_url(
    *,
    storage_key: str,
    ttl_seconds: int = 300,
) -> str:
    """
    Generate a fresh short-lived presigned GET URL for an S3 object.

    Used by the download view for large files (> _PROXY_SIZE_THRESHOLD_BYTES)
    in production: returns an HTTP 302 redirect to this URL instead of proxying
    the bytes through Django, avoiding memory pressure.

    In development (FileSystemStorage): this function is not called — the view
    streams the file directly via Django's storage backend.

    PIPEDA: the returned URL contains the storage_key as a URL parameter in the
    signed URL. The URL is returned to the authenticated user who has already
    been validated by consume_access_token(). It is NOT stored in any DB field
    or log message. The URL expires in ``ttl_seconds`` seconds.

    Args:
        storage_key: Internal storage path of the document.
        ttl_seconds: Presigned URL TTL in seconds (default: 300 = 5 minutes).

    Returns:
        A presigned GET URL string.

    Raises:
        ImproperlyConfigured: S3 backend not configured correctly.
        Exception:            boto3/botocore error (propagated to caller).
    """
    import boto3
    from botocore.exceptions import ClientError
    from django.core.exceptions import ImproperlyConfigured

    storage_opts = settings.STORAGES.get("default", {}).get("OPTIONS", {})
    bucket_name: str | None = storage_opts.get("bucket_name") or getattr(
        settings, "AWS_STORAGE_BUCKET_NAME", None
    )
    if not bucket_name:
        raise ImproperlyConfigured(
            "generate_presigned_download_url: S3 bucket name not configured. "
            "Set STORAGES['default']['OPTIONS']['bucket_name'] or AWS_STORAGE_BUCKET_NAME."
        )
    region_name: str = storage_opts.get("region_name", "ca-central-1")

    s3_client = boto3.client("s3", region_name=region_name)
    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": storage_key},
            ExpiresIn=ttl_seconds,
        )
    except ClientError as exc:
        logger.error(
            "generate_presigned_download_url: failed to generate URL: %s",
            exc,
        )
        raise
    return url


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _mask_ip(ip_address: str | None) -> str | None:
    """
    Mask an IP address for privacy-compliant storage.

    IPv4: zero the last octet   (e.g. 192.168.1.100 → 192.168.1.0)
    IPv6: zero the last 80 bits (equivalent to retaining only the /48 prefix)

    This masking prevents identification of individual devices while retaining
    enough information for abuse/security correlation (identifying the network
    or ISP prefix rather than the individual device).

    Returns:
        Masked IP string, or None if ip_address is None or unparseable.
        Unparseable addresses return None rather than raising — a bad IP
        address must not prevent a legitimate download.
    """
    if ip_address is None:
        return None
    try:
        addr = ipaddress.ip_address(ip_address)
    except ValueError:
        logger.warning(
            "_mask_ip: unparseable IP address %r; storing None.",
            ip_address,
        )
        return None

    if isinstance(addr, ipaddress.IPv4Address):
        # IPv4: zero the last octet.
        # Example: 192.168.1.100 → 192.168.1.0
        parts = str(addr).split(".")
        return ".".join(parts[:-1] + ["0"])
    else:
        # IPv6: retain only the /48 prefix (zero last 80 bits).
        # Example: 2001:db8:85a3::8a2e:370:7334 → 2001:db8:85a3::
        # /48 keeps the first 48 bits (6 bytes = 3 groups of 16-bit hextets),
        # zeroing the remaining 80 bits. This is the standard
        # "network-prefix only" masking for IPv6 privacy.
        network = ipaddress.ip_network(f"{ip_address}/48", strict=False)
        return str(network.network_address)


def _user_may_download(
    *,
    user: "User",
    document: "Document",
) -> bool:
    """
    Return True if the user is authorised to download the given document.

    Permission hierarchy:
      1. Superusers: always allowed.
      2. Staff with documents.view_document permission: allowed (any document).
         This covers coordinators reviewing citizen submissions.
      3. Citizens: allowed only if document.uploaded_by == user.
         Citizens cannot view documents uploaded by other citizens.

    Note: The scan_status gate (ACTIVE only) is enforced in issue_access_token()
    BEFORE this function is called. This function only checks ownership/permission.

    IDOR:
      The caller (issue_access_token) converts False → Http404 (not 403).
      Returning False never reveals WHY access was denied.
    """
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    if user.has_perm("documents.view_document"):
        # Staff coordinator or admin — may view any document.
        return True

    # Citizens: only their own documents.
    return document.uploaded_by_id == user.pk
