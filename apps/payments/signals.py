"""
Payment BB signals.

All signals carry keyword arguments only — callers must use kwargs.
Receivers are registered in apps.payments.receivers via PaymentsConfig.ready().
"""

from django.dispatch import Signal

# Fired when a PaymentIntent transitions to STATUS_COMPLETED and a Payment is created.
# kwargs: payment_intent (PaymentIntent), payment (Payment)
payment_completed = Signal()

# Fired when a PaymentIntent transitions to STATUS_FAILED.
# kwargs: payment_intent (PaymentIntent), failure_reason (str)
payment_failed = Signal()

# Fired when a Donation reaches DONATION_STATUS_COMPLETED.
# kwargs: donation (Donation), payment (Payment)
donation_completed = Signal()

# Fired when an OfficialDonationReceipt is issued.
# kwargs: receipt (OfficialDonationReceipt), donation (Donation)
receipt_issued = Signal()

# Fired when a RecurringGiftPlan is created.
# kwargs: plan (RecurringGiftPlan), donor (User)
recurring_plan_created = Signal()
