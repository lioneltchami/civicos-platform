"""
Test factories and shared helpers for payments test suite.

Provides reusable helpers that were previously duplicated across
test_integration_donation_flow.py, test_receipt_tasks.py, and
test_portal_views.py.
"""
from django.db.models import Model as DjangoModel


def make_fake_save(counter, year=2026):
    """
    Return a patch target for OfficialDonationReceipt.save() in unit tests.

    Bypasses the PostgreSQL nextval() sequence for serial_number by assigning
    a deterministic value via a shared counter list, then delegates to
    Django's base Model.save() to persist to the SQLite test database.

    Args:
        counter: a one-element list (e.g. [0]) used as a mutable counter so
                 the closure can increment it across calls within the same
                 test context.  Reset the list between tests for isolation.
        year:    four-digit year prefix for the serial number (default 2026).

    Usage::

        counter = [0]
        with patch.object(OfficialDonationReceipt, "save", make_fake_save(counter)):
            ...
    """
    def fake_save(receipt_instance, *args, **kwargs):
        """Returns True if rate limit is exceeded."""
        if not receipt_instance.serial_number:
            counter[0] += 1
            receipt_instance.serial_number = f"{year}-{str(counter[0]).zfill(6)}"
        DjangoModel.save(receipt_instance, *args, **kwargs)

    return fake_save


def make_fixed_serial_fake_save(serial):
    """
    Return a patch target for OfficialDonationReceipt.save() that assigns a
    specific pre-computed serial number instead of using a counter.

    Useful when the serial number is generated externally (e.g. via a
    _next_serial() helper) before entering the patch context.

    Args:
        serial: the serial number string to assign if none is set yet.

    Usage::

        serial = _next_serial()
        with patch.object(OfficialDonationReceipt, "save",
                          make_fixed_serial_fake_save(serial)):
            ...
    """
    def fake_save(receipt_instance, *args, **kwargs):
        """Returns True if rate limit is exceeded."""
        if not receipt_instance.serial_number:
            receipt_instance.serial_number = serial
        DjangoModel.save(receipt_instance, *args, **kwargs)

    return fake_save
