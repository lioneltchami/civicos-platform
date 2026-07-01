"""
Core signal definitions for CivicOS.

Other modules fire these signals; the audit app and notifications app listen
to them. This decouples modules: e.g., the portal app doesn't need to import
the audit app to trigger an audit log entry.

Usage (in another app):
    from apps.core.signals import service_request_submitted
    service_request_submitted.send(sender=ServiceRequest, instance=sr, request=request)
"""

from django.dispatch import Signal

# ---------------------------------------------------------------------------
# Authentication events
# ---------------------------------------------------------------------------

user_login_succeeded = Signal()   # Provides: request, user
user_login_failed = Signal()      # Provides: request, credentials
user_logged_out = Signal()        # Provides: request, user
user_password_changed = Signal()  # Provides: request, user

# ---------------------------------------------------------------------------
# Portal / service request events
# ---------------------------------------------------------------------------

service_request_submitted = Signal()   # Provides: instance, request
service_request_status_changed = Signal()  # Provides: instance, old_status, new_status, actor

# ---------------------------------------------------------------------------
# Form submission events
# ---------------------------------------------------------------------------

form_submission_received = Signal()   # Provides: form_page, submission, request

# ---------------------------------------------------------------------------
# Workflow events
# ---------------------------------------------------------------------------

workflow_item_assigned = Signal()    # Provides: work_item, assignee, actor
workflow_item_approved = Signal()    # Provides: work_item, actor, notes
workflow_item_rejected = Signal()    # Provides: work_item, actor, reason

# ---------------------------------------------------------------------------
# Admin / data events
# ---------------------------------------------------------------------------

pii_record_accessed = Signal()    # Provides: resource_type, resource_id, actor
pii_record_exported = Signal()    # Provides: resource_type, count, actor, request
