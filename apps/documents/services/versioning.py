"""
Document Management Building Block — Versioning service.

Manages the document version chain: creating new versions of existing documents,
querying version history, and ensuring version chain integrity.

Version chain invariants (enforced by select_for_update in create_new_version):
  1. Exactly one Document per root chain has is_latest_version=True.
  2. root_document points to the version-1 Document (root_document=None).
  3. version_number is monotonically increasing within a chain.
  4. Versions are immutable once created; only the latest version is actionable.

PIPEDA constraints:
  - Previous versions are not deleted when a new version is created.
    They are retained per the category retention policy (older versions
    share the same expires_at as the root; legal_hold is inherited).
  - original_filename NEVER in audit event_detail.
  - storage_key NEVER logged or returned.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth import get_user_model

    from apps.documents.models import Document

    User = get_user_model()

logger = logging.getLogger(__name__)


def create_new_version(
    *,
    user: "User",
    root_document: "Document",
    original_filename: str,
    mime_type: str,
    size_bytes: int,
    description: str = "",
) -> dict:
    """
    Create a new version of an existing document chain.

    The caller must have `documents.upload_document` permission and be either
    the original uploader or a staff member.

    Flow:
      1. Validate user permission against root_document.
      2. Under select_for_update(): mark current latest version as not-latest.
      3. Create new Document with version_number = latest + 1,
         root_document = root (or root.root_document if root is already a chain head),
         scan_status = PENDING_UPLOAD.
      4. Generate a new storage_key for the new version.
      5. Return presigned upload data (same as validate_upload_request).
      6. Fire document_version_created signal.

    Args:
        user:              Authenticated user performing the upload.
        root_document:     The existing document to version (any version in the chain
                           is accepted; service resolves to the chain root).
        original_filename: Client-supplied filename (stored; never used as path).
        mime_type:         Client-supplied MIME type (validated against category).
        size_bytes:        Client-supplied file size (validated against cap).
        description:       Optional description for this version.

    Returns:
        Same dict as validate_upload_request():
        {
            "doc_id":        str,   — UUID of the new Document version,
            "upload_url":    str,   — presigned POST URL,
            "upload_fields": dict,  — fields for the multipart POST,
            "expires_at":    str,   — ISO-8601 expiry timestamp,
        }

    Raises:
        PermissionDenied:      User is not authorised to version this document.
        Document.DoesNotExist: root_document not found.
        ValidationError:       File fails validation.

    STUB: Full implementation in Wave 4. This signature is fixed.
    """
    raise NotImplementedError("versioning.create_new_version — implemented in Wave 4")


def get_version_history(
    *,
    user: "User",
    root_document_pk: str,
) -> list["Document"]:
    """
    Return the full version chain for a document, ordered by version_number ascending.

    Only ACTIVE and SCANNING versions are included (QUARANTINED and DELETED
    are excluded from citizen views; staff with documents.view_document see all).

    Args:
        user:             The authenticated user making the request.
        root_document_pk: The PK of the root document (version 1).

    Returns:
        List of Document instances, ordered by version_number ascending.

    Raises:
        PermissionDenied:      User is not authorised to view this document.
        Document.DoesNotExist: Not found (caller converts to 404).

    STUB: Full implementation in Wave 4. This signature is fixed.
    """
    raise NotImplementedError("versioning.get_version_history — implemented in Wave 4")
