# Generated 2026-06-30 — M-D and M-E constraint fixes.
#
# M-D: ServiceFeePayment gets non-negative CheckConstraints on base_amount
#      and tax_amount to prevent corrupted financial reporting.
#      (Refund.amount > 0 constraint already exists from a previous migration;
#      the new clean() method enforces the cross-row invariant at the ORM level.)
#
# M-E: No new DB-level migration needed for the Refund.clean() addition —
#      it is pure Python validation and carries no schema change.

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0008_add_payment_financial_constraints"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="servicefeepayment",
            constraint=models.CheckConstraint(
                check=models.Q(base_amount__gte=Decimal("0.00")),
                name="payments_servicefeepayment_base_amount_nonneg",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicefeepayment",
            constraint=models.CheckConstraint(
                check=models.Q(tax_amount__gte=Decimal("0.00")),
                name="payments_servicefeepayment_tax_amount_nonneg",
            ),
        ),
    ]
