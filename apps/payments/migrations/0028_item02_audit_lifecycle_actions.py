from django.db import migrations, models


class Migration(migrations.Migration):
    """Record the Item 02 audit action vocabulary in migration state.

    The field remains a character column; this state migration introduces no
    sensitive data and no destructive schema operation.
    """

    dependencies = [  # noqa: RUF012
        ("payments", "0027_item02_failure_lifecycle"),
    ]

    operations = [  # noqa: RUF012
        migrations.AlterField(
            model_name="govstackpaymentauditentry",
            name="action",
            field=models.CharField(
                choices=[
                    ("beneficiary_registered", "Beneficiary Registered"),
                    ("beneficiary_updated", "Beneficiary Updated"),
                    ("batch_received", "Batch Received"),
                    ("batch_completed", "Batch Completed"),
                    ("batch_partial", "Batch Partially Completed"),
                    ("batch_failed", "Batch Failed"),
                    ("instruction_completed", "Instruction Completed"),
                    ("instruction_failed", "Instruction Failed"),
                    ("validation_requested", "Validation Requested"),
                    ("validation_completed", "Validation Completed"),
                    ("voucher_preactivated", "Voucher Preactivated"),
                    ("voucher_activated", "Voucher Activated"),
                    ("voucher_redeemed", "Voucher Redeemed"),
                    ("voucher_cancelled", "Voucher Cancelled"),
                    ("bill_payment_requested", "Bill Payment Requested"),
                    ("bill_paid", "Bill Paid"),
                    ("payment_attempt_created", "Payment Attempt Created"),
                    ("payment_outcome_recorded", "Payment Outcome Recorded"),
                    ("payment_retry_scheduled", "Payment Retry Scheduled"),
                    ("payment_uncertain", "Payment Outcome Uncertain"),
                    ("callback_queued", "Callback Queued"),
                    ("callback_delivered", "Callback Delivered"),
                    ("callback_dead_lettered", "Callback Dead-Lettered"),
                    ("reconciliation_recorded", "Reconciliation Recorded"),
                    ("payment_review_required", "Payment Review Required"),
                ],
                db_index=True,
                max_length=50,
                verbose_name="Action",
            ),
        ),
    ]
