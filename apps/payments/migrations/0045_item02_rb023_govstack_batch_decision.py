from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("payments", "0044_item02_rb022_credit_instruction_payment_attempt_binding")]

    operations = [
        migrations.CreateModel(
            name="GovStackBatchDecision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Created at",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                ("fingerprint", models.CharField(max_length=64)),
                ("lease_owner_token", models.CharField(max_length=128)),
                ("lease_generation", models.PositiveIntegerField()),
                ("policy_state", models.CharField(max_length=20)),
                (
                    "outcome_action",
                    models.CharField(
                        choices=[
                            ("empty", "Empty"),
                            ("pause", "Pause"),
                            ("retry", "Retry"),
                            ("review", "Review"),
                            ("terminal", "Terminal"),
                            ("return_funds", "Return funds"),
                        ],
                        max_length=20,
                    ),
                ),
                ("total_count", models.PositiveIntegerField(default=0)),
                ("settled_count", models.PositiveIntegerField(default=0)),
                ("rejected_count", models.PositiveIntegerField(default=0)),
                ("non_final_count", models.PositiveIntegerField(default=0)),
                ("reason", models.CharField(max_length=100)),
                ("details", models.JSONField(default=dict)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="payments.bulkpaymentbatch",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="govstackbatchdecision",
            constraint=models.UniqueConstraint(
                fields=("batch", "fingerprint"),
                name="gs_batch_decision_fingerprint_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="govstackbatchdecision",
            index=models.Index(
                fields=["batch", "lease_generation"],
                name="gs_batch_decision_gen_idx",
            ),
        ),
        migrations.AlterField(
            model_name="creditinstruction",
            name="payment_attempt",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="credit_instruction",
                to="payments.paymentattempt",
                verbose_name="Payment Attempt",
            ),
        ),
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
                    ("batch_decision_recorded", "Batch Decision Recorded"),
                ],
                db_index=True,
                max_length=50,
                verbose_name="Action",
            ),
        ),
    ]
