"""
Reports Building Block — Signals.

Privacy: kwargs must NEVER include recipient name, SIN, or email (PII).
Always use pk references only.

PIPEDA compliance: only non-identifying integer/Decimal values are passed
through signal kwargs. Callers must not add name, address, or SIN fields.
"""
from django.dispatch import Signal

# ---------------------------------------------------------------------------
# T4A / Honorarium signal
# ---------------------------------------------------------------------------

# Fired when a T4A slip is generated for a honorarium recipient.
#
# Provides:
#   report_pk   (str)     — primary key of the generated ExportRecord or
#                           report document (UUID string).
#   recipient_pk (int)    — integer PK of the recipient User/Volunteer row.
#                           Never a name or SIN.
#   tax_year    (int)     — four-digit calendar tax year (e.g. 2025).
#   ytd_total   (Decimal) — year-to-date honorarium total in CAD, used by
#                           CRA T4A Box 28.  Decimal, not float, to avoid
#                           rounding artefacts on CRA submissions.
#
# PIPEDA: original_filename, recipient name, and SIN are NOT in kwargs.
# Receivers must look up additional data via recipient_pk if required,
# and must not log or persist the Decimal amount alongside any PII.
t4a_generated = Signal()
