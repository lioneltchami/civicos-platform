"""
Wave 3 — Waitlist & Queue: WaitlistEntry, QueueEntry, ClientNoShowRecord tables.

Handwritten migration. Depends only on 0011_wave3_bookings so that
WaitlistEntry and QueueEntry can reference Booking and Slot which now exist.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0011_wave3_bookings"),
    ]

    operations = [  # noqa: RUF012
        # ------------------------------------------------------------------
        # WaitlistEntry
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="WaitlistEntry",
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
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="Created at"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "priority_class",
                    models.CharField(
                        choices=[
                            ("emergency", "Emergency / Crisis"),
                            ("bumped", "Bumped by Provider"),
                            ("high_need", "High Need / Vulnerable"),
                            ("recurring", "Recurring Regular"),
                            ("standard", "Standard"),
                        ],
                        default="standard",
                        max_length=20,
                        verbose_name="Priority class",
                    ),
                ),
                (
                    "position",
                    models.PositiveIntegerField(db_index=True, verbose_name="Position"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("waiting", "Waiting"),
                            ("notified", "Notified — Awaiting Response"),
                            ("accepted", "Accepted — Booking Created"),
                            ("expired", "Notification Expired"),
                            ("withdrawn", "Withdrawn by Client"),
                        ],
                        default="waiting",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "notification_sent_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Notification sent at",
                    ),
                ),
                (
                    "acceptance_deadline",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Acceptance deadline",
                    ),
                ),
                (
                    "notification_channel",
                    models.CharField(
                        choices=[
                            ("email", "Email"),
                            ("sms", "SMS"),
                            ("phone", "Phone Call"),
                        ],
                        default="email",
                        max_length=10,
                        verbose_name="Notification channel",
                    ),
                ),
                (
                    "joined_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Joined at"),
                ),
                (
                    "slot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="waitlist",
                        to="appointments.slot",
                        verbose_name="Slot",
                    ),
                ),
                (
                    "citizen",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="waitlist_entries",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Citizen",
                    ),
                ),
            ],
            options={
                "verbose_name": "Waitlist Entry",
                "verbose_name_plural": "Waitlist Entries",
                "ordering": ["position"],
            },
        ),
        migrations.AddConstraint(
            model_name="waitlistentry",
            constraint=models.UniqueConstraint(
                fields=["slot", "citizen"],
                name="appt_waitlist_slot_citizen_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="waitlistentry",
            index=models.Index(
                fields=["slot", "status", "position"],
                name="appt_waitlist_slot_status_pos",
            ),
        ),
        migrations.AddIndex(
            model_name="waitlistentry",
            index=models.Index(
                fields=["citizen", "status"],
                name="appt_waitlist_citizen_status",
            ),
        ),
        # ------------------------------------------------------------------
        # QueueEntry
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="QueueEntry",
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
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="Created at"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "queue_date",
                    models.DateField(db_index=True, verbose_name="Queue date"),
                ),
                (
                    "queue_number",
                    models.CharField(
                        db_index=True,
                        max_length=10,
                        verbose_name="Queue number",
                    ),
                ),
                (
                    "citizen_display_name",
                    models.CharField(
                        blank=True,
                        help_text="Display name for queue board. Not stored for anonymous bookings.",
                        max_length=200,
                        verbose_name="Citizen display name",
                    ),
                ),
                (
                    "queue_type",
                    models.CharField(
                        choices=[
                            ("emergency", "Emergency"),
                            ("booked", "Pre-Booked"),
                            ("walk_in", "Walk-In"),
                        ],
                        default="booked",
                        max_length=20,
                        verbose_name="Queue type",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("waiting", "Waiting"),
                            ("called", "Called"),
                            ("in_service", "In Service"),
                            ("completed", "Completed"),
                            ("no_show", "No Show"),
                            ("cancelled", "Left / Cancelled"),
                        ],
                        db_index=True,
                        default="waiting",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "called_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Called at"),
                ),
                (
                    "service_started_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Service started at",
                    ),
                ),
                (
                    "service_completed_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Service completed at",
                    ),
                ),
                (
                    "wait_time_minutes",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="Wait time (minutes)",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="queue_entries",
                        to="appointments.location",
                        verbose_name="Location",
                    ),
                ),
                (
                    "booking",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="queue_entry",
                        to="appointments.booking",
                        verbose_name="Booking",
                    ),
                ),
                (
                    "assigned_staff",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="queue_assignments",
                        to="appointments.staffprofile",
                        verbose_name="Assigned staff",
                    ),
                ),
            ],
            options={
                "verbose_name": "Queue Entry",
                "verbose_name_plural": "Queue Entries",
                "ordering": ["queue_type", "created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="queueentry",
            constraint=models.UniqueConstraint(
                fields=["location", "queue_date", "queue_number"],
                name="appt_queue_location_date_number_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="queueentry",
            index=models.Index(
                fields=["location", "queue_date", "status"],
                name="appt_queue_loc_date_status",
            ),
        ),
        # ------------------------------------------------------------------
        # ClientNoShowRecord
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ClientNoShowRecord",
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
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="Created at"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated at"),
                ),
                (
                    "no_show_count",
                    models.PositiveIntegerField(default=0, verbose_name="No-show count"),
                ),
                (
                    "late_cancellation_count",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Late cancellation count",
                    ),
                ),
                (
                    "total_appointments",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Total appointments",
                    ),
                ),
                (
                    "last_no_show_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Last no-show at",
                    ),
                ),
                (
                    "is_flagged",
                    models.BooleanField(default=False, verbose_name="Is flagged"),
                ),
                (
                    "flagged_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Flagged at",
                    ),
                ),
                (
                    "is_suspended",
                    models.BooleanField(
                        default=False,
                        help_text="If True, citizen cannot self-book until staff reviews and clears.",
                        verbose_name="Is suspended",
                    ),
                ),
                (
                    "suspension_note",
                    models.TextField(blank=True, verbose_name="Suspension note"),
                ),
                (
                    "citizen",
                    models.OneToOneField(
                        limit_choices_to={"is_staff": False},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="no_show_record",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Citizen",
                    ),
                ),
                (
                    "flagged_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="flagged_no_show_records",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Flagged by",
                    ),
                ),
            ],
            options={
                "verbose_name": "Client No-Show Record",
                "verbose_name_plural": "Client No-Show Records",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="clientnoshowrecord",
            index=models.Index(
                fields=["is_flagged"],
                name="appt_noshowrec_flagged",
            ),
        ),
        migrations.AddIndex(
            model_name="clientnoshowrecord",
            index=models.Index(
                fields=["is_suspended"],
                name="appt_noshowrec_suspended",
            ),
        ),
    ]
