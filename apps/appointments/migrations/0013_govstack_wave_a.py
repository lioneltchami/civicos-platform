"""
GovStack Scheduler BB — Wave A: model layer additions.

Adds GovStack-required fields to existing models and creates four new
supplementary models.

Changes to existing models:
  Organization  — phone, email, website
  Resource      — phone, email, alert_url, alert_preference, status_poll_url
  BookingAuditLog — actor_role

New models:
  GovStackSubscriberProfile
  GovStackMessage
  GovStackAffiliation
  GovStackAlertSchedule

Depends on: ("appointments", "0012_wave3_waitlist_queue")
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0012_wave3_waitlist_queue"),
        ("auth_extension", "0001_initial"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Organization — GovStack Entity contact fields
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="organization",
            name="phone",
            field=models.CharField(blank=True, max_length=30, verbose_name="Phone"),
        ),
        migrations.AddField(
            model_name="organization",
            name="email",
            field=models.EmailField(blank=True, verbose_name="Email"),
        ),
        migrations.AddField(
            model_name="organization",
            name="website",
            field=models.URLField(blank=True, verbose_name="Website"),
        ),
        # ------------------------------------------------------------------
        # Resource — GovStack Resource callback fields
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="resource",
            name="phone",
            field=models.CharField(blank=True, max_length=30, verbose_name="Phone"),
        ),
        migrations.AddField(
            model_name="resource",
            name="email",
            field=models.EmailField(blank=True, verbose_name="Email"),
        ),
        migrations.AddField(
            model_name="resource",
            name="alert_url",
            field=models.URLField(
                blank=True,
                help_text="GovStack Scheduler BB: URL where this resource receives push alerts.",
                verbose_name="Alert URL",
            ),
        ),
        migrations.AddField(
            model_name="resource",
            name="alert_preference",
            field=models.CharField(
                blank=True,
                choices=[
                    ("push", "Push (HTTP callback)"),
                    ("poll", "Poll (status_poll_url)"),
                    ("email", "Email"),
                    ("sms", "SMS"),
                    ("none", "None"),
                ],
                max_length=10,
                verbose_name="Alert preference",
            ),
        ),
        migrations.AddField(
            model_name="resource",
            name="status_poll_url",
            field=models.URLField(
                blank=True,
                help_text="GovStack Scheduler BB: URL that the scheduler polls for this resource's availability status.",
                verbose_name="Status poll URL",
            ),
        ),
        # ------------------------------------------------------------------
        # BookingAuditLog — GovStack actor_role field
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="bookingauditlog",
            name="actor_role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("admin", "Admin"),
                    ("organizer", "Organizer"),
                    ("resource", "Resource"),
                    ("subscriber", "Subscriber / Citizen"),
                    ("system", "System"),
                ],
                help_text="GovStack Scheduler BB log field: role of the actor who triggered this audit event.",
                max_length=20,
                verbose_name="Actor role",
            ),
        ),
        # ------------------------------------------------------------------
        # GovStackSubscriberProfile
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="GovStackSubscriberProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "category",
                    models.CharField(
                        blank=True,
                        help_text="GovStack subscriber category, e.g. 'individual', 'group', 'organization'.",
                        max_length=50,
                        verbose_name="Subscriber category",
                    ),
                ),
                (
                    "alert_url",
                    models.URLField(
                        blank=True,
                        help_text="URL where this subscriber receives push alerts from the scheduler.",
                        verbose_name="Alert URL",
                    ),
                ),
                (
                    "alert_preference",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("push", "Push (HTTP callback)"),
                            ("poll", "Poll"),
                            ("email", "Email"),
                            ("sms", "SMS"),
                            ("none", "None"),
                        ],
                        max_length=10,
                        verbose_name="Alert preference",
                    ),
                ),
                (
                    "status_poll_url",
                    models.URLField(
                        blank=True,
                        help_text="URL the scheduler polls to determine subscriber availability.",
                        verbose_name="Status poll URL",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="govstack_subscriber_profile",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Subscriber Profile",
                "verbose_name_plural": "GovStack Subscriber Profiles",
            },
        ),
        # ------------------------------------------------------------------
        # GovStackMessage
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="GovStackMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "category",
                    models.CharField(
                        db_index=True,
                        help_text="Message category, e.g. 'reminder', 'confirmation', 'cancellation'.",
                        max_length=50,
                        verbose_name="Category",
                    ),
                ),
                (
                    "message_body",
                    models.TextField(
                        help_text="Plain text or HTML notification body. May include template variables.",
                        verbose_name="Message body",
                    ),
                ),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="govstack_messages",
                        to="appointments.organization",
                        verbose_name="Entity (Organization)",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Message",
                "verbose_name_plural": "GovStack Messages",
                "ordering": ["entity", "category"],
            },
        ),
        migrations.AddIndex(
            model_name="govstackmessage",
            index=models.Index(
                fields=["entity", "category"],
                name="appt_gs_msg_entity_cat",
            ),
        ),
        # ------------------------------------------------------------------
        # GovStackAffiliation
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="GovStackAffiliation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "resource_category",
                    models.CharField(
                        blank=True,
                        max_length=50,
                        verbose_name="Resource category",
                    ),
                ),
                (
                    "work_days_hours",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            'GovStack days_hours structure: e.g. '
                            '{"monday": {"from": "09:00", "to": "17:00"}, "tuesday": {...}, ...}'
                        ),
                        verbose_name="Work days/hours",
                    ),
                ),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="govstack_affiliations",
                        to="appointments.resource",
                        verbose_name="Resource",
                    ),
                ),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="govstack_affiliations",
                        to="appointments.organization",
                        verbose_name="Entity (Organization)",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Affiliation",
                "verbose_name_plural": "GovStack Affiliations",
                "ordering": ["entity", "resource"],
                "unique_together": {("resource", "entity")},
            },
        ),
        migrations.AddIndex(
            model_name="govstackaffiliation",
            index=models.Index(
                fields=["entity", "resource_category"],
                name="appt_gs_aff_entity_cat",
            ),
        ),
        # ------------------------------------------------------------------
        # GovStackAlertSchedule
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="GovStackAlertSchedule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "target_category",
                    models.CharField(
                        blank=True,
                        help_text="Which participant category to notify: 'subscriber', 'resource', or blank for all.",
                        max_length=50,
                        verbose_name="Target category",
                    ),
                ),
                (
                    "alert_datetime",
                    models.DateTimeField(
                        db_index=True,
                        help_text="When to dispatch the alert. Must be in the future at creation time.",
                        verbose_name="Alert datetime",
                    ),
                ),
                (
                    "celery_task_id",
                    models.CharField(
                        blank=True,
                        help_text="ID of the scheduled Celery ETA task. Used to revoke the alert on DELETE.",
                        max_length=255,
                        verbose_name="Celery task ID",
                    ),
                ),
                (
                    "dispatched",
                    models.BooleanField(
                        default=False,
                        help_text="True once the Celery task has fired and delivered the alert.",
                        verbose_name="Dispatched",
                    ),
                ),
                (
                    "slot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="govstack_alert_schedules",
                        to="appointments.slot",
                        verbose_name="Event (Slot)",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="alert_schedules",
                        to="appointments.govstackmessage",
                        verbose_name="Message template",
                    ),
                ),
            ],
            options={
                "verbose_name": "GovStack Alert Schedule",
                "verbose_name_plural": "GovStack Alert Schedules",
                "ordering": ["alert_datetime"],
            },
        ),
        migrations.AddIndex(
            model_name="govstackalertschedule",
            index=models.Index(
                fields=["slot", "alert_datetime"],
                name="appt_gs_alert_slot_dt",
            ),
        ),
        migrations.AddIndex(
            model_name="govstackalertschedule",
            index=models.Index(
                fields=["alert_datetime", "dispatched"],
                name="appt_gs_alert_dt_disp",
            ),
        ),
    ]
