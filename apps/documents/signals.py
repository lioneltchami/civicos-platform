"""
Document Management Building Block — Signals.

Declaration-only module: defines all signals emitted by the documents BB.
Signal handlers (receivers) live in receivers.py to keep this file
declaration-only and importable without side effects.

Signal naming convention: document_<past_tense_verb>

Privacy constraints (enforced in kwargs — receivers must honour these):
  - kwargs must NEVER include original_filename (may contain PII).
  - kwargs must NEVER include storage_key (internal S3 path).
  - Quarantine signals must NOT include uploader identity in kwargs
    that flow into external notifications.
  - Always include document_pk (str) so receivers can audit-log efficiently.
"""

from django.dispatch import Signal

# ── Upload lifecycle ──────────────────────────────────────────────────────────

# Fired after Document is created and presigned POST URL generated.
# Provides: document_pk (str), category_slug (str), uploaded_by_id (int)
document_upload_initiated = Signal()

# Fired after confirm-upload succeeds and scan task is dispatched.
# Provides: document_pk (str), size_bytes (int), mime_type (str)
# PIPEDA: original_filename and uploader PII are NOT in kwargs.
document_confirmed = Signal()

# ── Scan results ──────────────────────────────────────────────────────────────

# Fired when ClamAV marks document as clean and it moves to active storage.
# Provides: document_pk (str)
# Receivers may notify the citizen uploader that their file is ready.
document_scan_clean = Signal()

# Fired when ClamAV detects a threat.
# Provides: document_pk (str), scan_engine_result (str)
# PIPEDA: MUST NOT include original_filename or uploaded_by identity.
# Receivers notify the system admin only (not the citizen).
document_quarantined = Signal()

# ── Versioning ────────────────────────────────────────────────────────────────

# Fired after DocumentService.create_new_version() completes.
# Provides: root_document_pk (str), new_version_pk (str), version_number (int)
document_version_created = Signal()

# ── Retention and disposal ────────────────────────────────────────────────────

# Fired when a document is soft-deleted.
# Provides: document_pk (str), deleted_by_id (int or None for system deletion)
document_soft_deleted = Signal()

# Fired when a document is hard-deleted from storage and the DB row is purged.
# Provides: document_pk (str), category_slug (str)
# NOTE: After this signal fires, the Document row no longer exists in the DB.
# Receivers must not attempt to fetch the Document; use kwargs only.
document_hard_deleted = Signal()

# ── Legal hold ────────────────────────────────────────────────────────────────

# Fired when a legal hold is applied or released on a document.
# Provides: document_pk (str), legal_hold (bool), set_by_id (int)
document_legal_hold_changed = Signal()
