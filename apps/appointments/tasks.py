"""
Appointments BB — Celery task stubs (Wave 1 placeholder).

Actual task implementations are added in later waves:
  Wave 2: send_appointment_reminder
  Wave 3: expire_unconfirmed_bookings, process_waitlist_slot
  Wave 4: send_waitlist_notification, check_no_show_thresholds
  Wave 5: send_citizen_booking_summary

This module must exist in Wave 1 because:
  - apps.py AppConfig.ready() may reference it for signal wiring
  - Celery autodiscover_tasks() scans for tasks.py at startup
  - Beat schedule entries referencing future tasks avoid ImportError
    by defining stubs here (raises NotImplementedError if called early)

Security invariants (apply to ALL appointment tasks implemented here):
  - NEVER include PII (citizen email, name) in task args — use PKs only.
  - record_event() MUST be called inside transaction.atomic().
  - Tasks MUST be idempotent (safe to retry on failure).
  - Use select_for_update() inside atomic() for capacity/status checks.
"""

from __future__ import annotations

# Celery app import deferred to avoid circular imports at module load time.
# All tasks defined here use @shared_task so the Celery app is looked up
# at call time, not at import time.
# from celery import shared_task  # uncomment when first task is implemented
