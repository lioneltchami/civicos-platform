"""
Volunteer Management BB — Signal definitions.

All signals are defined here and dispatched via .send_robust() from service
functions to prevent a failing receiver from rolling back the originating
transaction.

Receivers are registered in apps/volunteers/receivers.py.
"""
from django.dispatch import Signal

# --- Application lifecycle ---
application_submitted = Signal()   # sender=VolunteerApplication; kwargs: opportunity, volunteer
application_approved  = Signal()   # sender=VolunteerApplication; kwargs: reviewed_by
application_rejected  = Signal()   # sender=VolunteerApplication; kwargs: reviewed_by
application_withdrawn = Signal()   # sender=VolunteerApplication

# --- Shift / booking lifecycle ---
shift_booked            = Signal()   # sender=ShiftBooking; kwargs: shift, volunteer
shift_booking_cancelled = Signal()   # sender=ShiftBooking; kwargs: shift, volunteer, reason
shift_cancelled         = Signal()   # sender=Shift; kwargs: opportunity, reason

# --- Hours lifecycle ---
hours_logged   = Signal()   # sender=HoursLog; kwargs: volunteer, opportunity
hours_approved = Signal()   # sender=HoursLog; kwargs: approved_by
hours_rejected = Signal()   # sender=HoursLog; kwargs: rejected_by, reason

# --- Screening / certification expiry ---
screening_expiring     = Signal()   # sender=ScreeningRecord; kwargs: volunteer, check_type
certification_expiring = Signal()   # sender=Certification; kwargs: volunteer, cert_type

# --- Recognition ---
milestone_achieved = Signal()   # sender=RecognitionMilestone; kwargs: volunteer, hours_threshold
