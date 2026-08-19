from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("appointments", "0018_alter_bookingauditlog_action_and_more")]

    operations = [
        migrations.AddField(
            model_name="govstackalertschedule",
            name="delivery_generation",
            field=models.PositiveIntegerField(default=1, help_text="Incremented when an alert is re-armed or superseded to fence stale work.", verbose_name="Delivery generation"),
        ),
        migrations.CreateModel(
            name="SchedulerRecipientDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                ("dispatch_generation", models.PositiveIntegerField(default=1)),
                ("recipient_kind", models.CharField(max_length=30)),
                ("idempotency_key", models.CharField(max_length=180, unique=True)),
                ("correlation_id", models.CharField(db_index=True, max_length=180)),
                ("owner_key", models.CharField(db_index=True, max_length=180)),
                ("recipient_ref", models.CharField(max_length=180)),
                ("payload", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("in_flight", "In Flight"), ("retry", "Retry"), ("delivered", "Delivered"), ("acknowledged", "Acknowledged"), ("cancelled", "Cancelled"), ("dead_letter", "Dead Letter")], db_index=True, default="pending", max_length=20)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=3)),
                ("next_attempt_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("lease_token", models.CharField(blank=True, max_length=180, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=240)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("dead_lettered_at", models.DateTimeField(blank=True, null=True)),
                ("schedule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recipient_deliveries", to="appointments.govstackalertschedule")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("schedule", "dispatch_generation", "recipient_kind", "recipient_ref"), name="appt_sched_delivery_generation_recipient_uniq")],
                "indexes": [
                    models.Index(fields=["status", "next_attempt_at"], name="appt_sched_due_idx"),
                    models.Index(fields=["status", "lease_expires_at"], name="appt_sched_lease_idx"),
                    models.Index(fields=["owner_key", "status"], name="appt_sched_owner_status_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SchedulerOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated at")),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("available_at", models.DateTimeField(db_index=True)),
                ("publish_attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.CharField(blank=True, default="", max_length=240)),
                ("delivery", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="outbox", to="appointments.schedulerrecipientdelivery")),
            ],
            options={"indexes": [models.Index(fields=["published_at", "available_at"], name="appt_sched_outbox_idx")]},
        ),
    ]
