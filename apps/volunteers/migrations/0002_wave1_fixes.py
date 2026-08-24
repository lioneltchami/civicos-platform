# Generated migration — Wave 1 fixes for Volunteer Management BB.
# Covers: on_delete changes, GeneratedField for calendar_year,
#         currency choices, and new DB-level constraints.
import django.db.models
import django.db.models.deletion
import django.db.models.functions
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("volunteers", "0001_initial"),
    ]

    operations = [  # noqa: RUF012
        # H1: Shift.opportunity CASCADE → PROTECT (audit trail must survive archiving)
        migrations.AlterField(
            model_name="shift",
            name="opportunity",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="shifts",
                to="volunteers.opportunity",
                verbose_name="Opportunity",
            ),
        ),
        # H2: ShiftBooking.volunteer CASCADE → PROTECT (booking history survives deactivation)
        migrations.AlterField(
            model_name="shiftbooking",
            name="volunteer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bookings",
                to="volunteers.volunteerprofile",
                verbose_name="Volunteer",
            ),
        ),
        # M2: VolunteerApplication.consent_record PROTECT → SET_NULL (PIPEDA right-to-erasure)
        migrations.AlterField(
            model_name="volunteerapplication",
            name="consent_record",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="volunteer_applications",
                to="consent.consentrecord",
                verbose_name="Consent record",
            ),
        ),
        # C1: Honorarium.calendar_year — DB-enforced GeneratedField (STORED)
        # SQLite does not support AlterField for GeneratedFields; use Remove + Add.
        # The vol_hon_vol_yr_type_idx index (from 0001) references this column;
        # drop it first, then remove the column, add as GeneratedField, rebuild index.
        migrations.RemoveIndex(
            model_name="honorarium",
            name="vol_hon_vol_yr_type_idx",
        ),
        migrations.RemoveField(
            model_name="honorarium",
            name="calendar_year",
        ),
        migrations.AddField(
            model_name="honorarium",
            name="calendar_year",
            field=django.db.models.GeneratedField(
                db_persist=True,
                expression=django.db.models.functions.ExtractYear("payment_date"),
                output_field=django.db.models.PositiveSmallIntegerField(),
                verbose_name="Calendar year",
            ),
        ),
        migrations.AddIndex(
            model_name="honorarium",
            index=models.Index(
                fields=["volunteer", "calendar_year", "payment_type"],
                name="vol_hon_vol_yr_type_idx",
            ),
        ),
        # Fix 12: Honorarium.currency — add choices
        migrations.AlterField(
            model_name="honorarium",
            name="currency",
            field=models.CharField(
                choices=[("CAD", "Canadian Dollar")],
                default="CAD",
                help_text="Only CAD supported. CRA thresholds are denominated in CAD.",
                max_length=3,
                verbose_name="Currency",
            ),
        ),
        # C2: VolunteerProfile.total_hours_approved — non-negative DB constraint
        migrations.AddConstraint(
            model_name="volunteerprofile",
            constraint=models.CheckConstraint(
                check=models.Q(total_hours_approved__gte=0),
                name="vol_profile_total_hours_non_negative",
            ),
        ),
        # C3: ScreeningRecord — partial unique on org-wide checks (opportunity IS NULL)
        migrations.AddConstraint(
            model_name="screeningrecord",
            constraint=models.UniqueConstraint(
                fields=["volunteer", "check_type"],
                condition=models.Q(opportunity__isnull=True),
                name="vol_screen_unique_vol_type_no_opp",
            ),
        ),
        # M1: HoursLog — partial unique on (volunteer, shift) when shift is not null
        migrations.AddConstraint(
            model_name="hourslog",
            constraint=models.UniqueConstraint(
                fields=["volunteer", "shift"],
                condition=models.Q(shift__isnull=False),
                name="vol_hourslog_unique_vol_shift",
            ),
        ),
        # Fix 11: RecognitionMilestone.hours_threshold — minimum 1 at DB level
        migrations.AddConstraint(
            model_name="recognitionmilestone",
            constraint=models.CheckConstraint(
                check=models.Q(hours_threshold__gte=1),
                name="vol_milestone_threshold_min",
            ),
        ),
    ]
