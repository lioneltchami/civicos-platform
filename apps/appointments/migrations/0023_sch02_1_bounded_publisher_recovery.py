from django.db import migrations, models


PENDING = "pending"
CLAIMED = "claimed"
PUBLISHED = "published"
CANCELLED = "cancelled"


def backfill_publisher_states(apps, schema_editor):
    SchedulerOutbox = apps.get_model("appointments", "SchedulerOutbox")
    SchedulerOutbox.objects.filter(cancelled_at__isnull=False).update(publisher_state=CANCELLED)
    SchedulerOutbox.objects.filter(cancelled_at__isnull=True, published_at__isnull=False).update(
        publisher_state=PUBLISHED
    )
    SchedulerOutbox.objects.filter(
        cancelled_at__isnull=True, published_at__isnull=True, publisher_token__isnull=False
    ).update(publisher_state=CLAIMED)
    SchedulerOutbox.objects.filter(
        cancelled_at__isnull=True, published_at__isnull=True, publisher_token__isnull=True
    ).update(publisher_state=PENDING)


class Migration(migrations.Migration):
    dependencies = [("appointments", "0022_sch01_2a_cancel_only")]

    operations = [
        migrations.AddField(
            model_name="scheduleroutbox",
            name="publisher_state",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"), ("claimed", "Claimed"),
                    ("local_failure", "Local Failure"), ("unknown_handoff", "Unknown Handoff"),
                    ("published", "Published"), ("exhausted", "Exhausted"),
                    ("cancelled", "Cancelled"),
                ], default="pending", db_index=True, max_length=32,
            ),
        ),
        migrations.AddField(model_name="scheduleroutbox", name="publisher_failure_class", field=models.CharField(blank=True, default="", max_length=32)),
        migrations.AddField(model_name="scheduleroutbox", name="publisher_state_changed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="scheduleroutbox", name="exhausted_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddIndex(model_name="scheduleroutbox", index=models.Index(fields=["publisher_state", "available_at"], name="appt_sched_pub_state_due_idx")),
        migrations.RunPython(backfill_publisher_states, migrations.RunPython.noop),
    ]
