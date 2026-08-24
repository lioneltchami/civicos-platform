"""
Consent signal handlers.

Signals defined here (using Django's Signal):
  consent_granted    — fires after ConsentRecord status → granted
  consent_withdrawn  — fires after ConsentRecord status → withdrawn
  export_requested   — fires after DataExportRequest is created
  export_ready       — fires after DataExportRequest status → ready
"""

from django.dispatch import Signal

# Custom signals
consent_granted = Signal()  # kwargs: consent_record, request
consent_withdrawn = Signal()  # kwargs: consent_record, request
export_requested = Signal()  # kwargs: export_request, request
export_ready = Signal()  # kwargs: export_request
