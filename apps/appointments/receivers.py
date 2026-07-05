"""
Appointments / Scheduling BB — Signal receivers.

Receivers are connected here when apps.AppointmentsConfig.ready() imports
this module.

Implementation deferred to Wave 3 (booking service layer) and Wave 4 (waitlist).
This stub prevents ImportError from apps.py when the file is discovered but
not yet populated.

All receivers must:
  - Use transaction.on_commit() for any side-effect work (email, Celery tasks)
    that must not run if the originating transaction rolls back.
  - Call record_event() inside the originating atomic() block (PIPEDA 4.5.3).
  - Never log or include PII (email, name) in signal kwargs or log messages.
"""
# Wave 3: connect appt_booking_created, appt_booking_confirmed, etc.
# Wave 4: connect appt_waitlist_joined, appt_waitlist_notified, etc.
# Wave 6: connect appt_reminder_sent.
