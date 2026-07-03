"""
Document Management Building Block — Download service.

Handles secure document download via single-use access tokens.

Download flow:
  1. issue_access_token() — create a DocumentAccessToken for an authorised user
  2. DocumentDownloadView  — validate token, write audit, proxy or redirect
  3. _generate_presigned_url() — fresh short-lived S3 presigned URL (Wave 3 detail)

IDOR prevention:
  - Non-owned document PKs return 404, not 403 (403 confirms existence).
  - Citizens may only download ACTIVE documents they own or are authorised to view.
  - Staff require documents.view_document permission to download any document.

PIPEDA constraints:
  - storage_key is NEVER returned to clients. The access token is the
    only thing returned; the download view uses it to look up the storage key.
  - original_filename is NEVER in audit event_detail.
  - Audit event_detail contains only: doc_pk, token_pk, ip_masked.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document, DocumentAccessToken

    User = get_user_model()

logger = logging.getLogger(__name__)

# Default access token TTL in seconds (5 minutes).
# Overridden by CIVICOS['DOCUMENT_ACCESS_TOKEN_TTL_SECONDS'].
_DEFAULT_TOKEN_TTL: int = 300

# Threshold for proxy vs redirect: files ≤ this are proxied directly.
# Files > this get a fresh presigned URL redirect to avoid Django memory pressure.
_PROXY_SIZE_THRESHOLD_BYTES: int = 1 * 1024 * 1024  # 1 MB


def issue_access_token(
    *,
    user: "User",
    document: "Document",
    ip_address: str | None = None,
) -> "DocumentAccessToken":
    """
    Issue a single-use DocumentAccessToken for an authorised download.

    Validates that:
      - The document is ACTIVE (not quarantined, deleted, or pending).
      - The user is authorised to download this document.

    IP address is masked (IPv4: last octet zeroed; IPv6: last 80 bits) before
    storage for privacy compliance.

    Args:
        user:       The authenticated user requesting the download.
        document:   The Document to download.
        ip_address: Caller's IP address (from request, already extracted by view).

    Returns:
        A fresh DocumentAccessToken with is_valid=True.

    Raises:
        PermissionDenied:  User is not authorised to download this document.
        ValidationError:   Document is not in ACTIVE state.

    STUB: Full implementation in Wave 3. This signature is fixed.
    """
    # [Wave 3] Implementation:
    # 1. Verify document.scan_status == ACTIVE (raise ValidationError if not)
    # 2. Verify document.deleted_at is None (raise 404 if soft-deleted)
    # 3. Check _user_may_download(user, document) — raise PermissionDenied if not
    # 4. Mask ip_address per CivicOS policy
    # 5. TTL = CIVICOS.get('DOCUMENT_ACCESS_TOKEN_TTL_SECONDS', _DEFAULT_TOKEN_TTL)
    # 6. Create and return DocumentAccessToken(
    #        document=document, issued_to=user,
    #        expires_at=now + timedelta(seconds=TTL),
    #        ip_address=masked_ip
    #    )
    # 7. Write audit: AuditEventType.RECORD_VIEWED, resource_type="documents.Document"
    raise NotImplementedError("download.issue_access_token — implemented in Wave 3")


def consume_access_token(
    *,
    token_value: str,
    user: "User",
) -> "Document":
    """
    Validate and consume a DocumentAccessToken, returning the associated Document.

    Single-use: sets used_at=now() and saves the token. Subsequent calls
    with the same token value raise PermissionDenied.

    Args:
        token_value: The opaque 64-char hex token string.
        user:        The authenticated user presenting the token.

    Returns:
        The Document linked to the token.

    Raises:
        PermissionDenied: Token not found, expired, already used,
                          or belongs to a different user.

    STUB: Full implementation in Wave 3. This signature is fixed.
    """
    # [Wave 3] Implementation:
    # 1. Fetch DocumentAccessToken by token value (404 → PermissionDenied for IDOR)
    # 2. Verify token.issued_to == user (PermissionDenied if not)
    # 3. Verify token.is_valid (used_at is None and expires_at > now)
    # 4. Inside atomic(): re-check is_valid under select_for_update(), set used_at=now
    # 5. Return token.document
    raise NotImplementedError("download.consume_access_token — implemented in Wave 3")


def _mask_ip(ip_address: str | None) -> str | None:
    """
    Mask an IP address for privacy-compliant storage.

    IPv4: zero the last octet   (e.g. 192.168.1.100 → 192.168.1.0)
    IPv6: zero the last 80 bits (last 5 groups of the 8-group notation)

    Returns None if ip_address is None or unparseable.

    STUB: Full implementation in Wave 3.
    """
    # [Wave 3] Implementation using Python ipaddress module.
    raise NotImplementedError("download._mask_ip — implemented in Wave 3")


def _user_may_download(
    user: "User",
    document: "Document",
) -> bool:
    """
    Return True if user is authorised to download the given document.

    Business rules:
      - Superusers: always allowed.
      - Staff with documents.view_document: allowed.
      - Citizens: allowed only if document.uploaded_by == user AND document is ACTIVE.
      - IDOR: a citizen checking another citizen's doc gets 404 (enforced in view).

    STUB: Full implementation in Wave 3.
    """
    raise NotImplementedError("download._user_may_download — implemented in Wave 3")
