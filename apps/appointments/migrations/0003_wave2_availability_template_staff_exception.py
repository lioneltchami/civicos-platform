import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0002_add_allow_anonymous_booking_resource_db_index_buffer_validators"),
    ]

    operations = [  # noqa: RUF012
        migrations.CreateModel(
            name="AvailabilityTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "day_of_week",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (1, "Monday"),
                            (2, "Tuesday"),
                            (3, "Wednesday"),
                            (4, "Thursday"),
                            (5, "Friday"),
                            (6, "Saturday"),
                            (7, "Sunday"),
                        ],
                        verbose_name="Day of week",
                        help_text="ISO 8601 weekday: 1=Monday, 7=Sunday.",
                    ),
                ),
                ("start_time", models.TimeField(verbose_name="Start time")),
                ("end_time", models.TimeField(verbose_name="End time")),
                ("valid_from", models.DateField(verbose_name="Valid from")),
                (
                    "valid_until",
                    models.DateField(blank=True, null=True, verbose_name="Valid until"),
                ),
                (
                    "staff",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="availability_templates",
                        to="appointments.staffprofile",
                        verbose_name="Staff profile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Availability template",
                "verbose_name_plural": "Availability templates",
                "ordering": ["day_of_week", "start_time"],
            },
        ),
        migrations.CreateModel(
            name="StaffException",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("exception_date", models.DateField(db_index=True, verbose_name="Exception date")),
                (
                    "exception_type",
                    models.CharField(
                        max_length=20,
                        choices=[
                            ("holiday", "Public Holiday / Day Off"),
                            ("leave", "Sick / Personal Leave"),
                            ("override", "Override Hours"),
                            ("training", "Training / Conference"),
                        ],
                        verbose_name="Exception type",
                    ),
                ),
                (
                    "override_start_time",
                    models.TimeField(blank=True, null=True, verbose_name="Override start time"),
                ),
                (
                    "override_end_time",
                    models.TimeField(blank=True, null=True, verbose_name="Override end time"),
                ),
                (
                    "note_internal",
                    models.CharField(blank=True, max_length=200, verbose_name="Internal note"),
                ),
                (
                    "staff",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="exceptions",
                        to="appointments.staffprofile",
                        verbose_name="Staff profile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Staff exception",
                "verbose_name_plural": "Staff exceptions",
                "ordering": ["exception_date"],
            },
        ),
        migrations.AddIndex(
            model_name="availabilitytemplate",
            index=models.Index(
                fields=["staff", "day_of_week", "valid_from"], name="appt_avail_staff_dow_from"
            ),
        ),
        migrations.AddConstraint(
            model_name="availabilitytemplate",
            constraint=models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="appt_avail_end_after_start",
            ),
        ),
        migrations.AddConstraint(
            model_name="availabilitytemplate",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_until__gte=models.F("valid_from"))
                ),
                name="appt_avail_until_gte_from",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="staffexception",
            unique_together={("staff", "exception_date")},
        ),
    ]
