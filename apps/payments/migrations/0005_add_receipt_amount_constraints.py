# Generated 2026-06-30 — M6 fix: add advantage_lte_eligible DB constraint

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    CRA IT-110R3 compliance: add a database-level CheckConstraint ensuring
    advantage_amount never exceeds eligible_amount on OfficialDonationReceipt.

    Python-level validation alone cannot prevent invalid rows created via
    direct DB inserts, management commands, or ORM bypass (e.g. _base_manager).
    This constraint makes the invariant enforceable at the database layer.

    The two non-negativity constraints (eligible_amount__gte=0 and
    advantage_amount__gte=0) already exist from 0001_initial; only the
    cross-field constraint is new here.
    """

    dependencies = [
        ("payments", "0004_wave9_fixes"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="officialdonationreceipt",
            constraint=models.CheckConstraint(
                check=models.Q(advantage_amount__lte=models.F("eligible_amount")),
                name="payments_receipt_advantage_lte_eligible",
            ),
        ),
    ]
