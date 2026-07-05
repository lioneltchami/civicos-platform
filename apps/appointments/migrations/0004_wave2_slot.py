import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0003_wave2_availability_template_staff_exception"),
    ]

    operations = [
        migrations.CreateModel(
            name="Slot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name="Slot ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("start_datetime", models.DateTimeField(db_index=True, verbose_name="Start (UTC)")),
                ("end_datetime", models.DateTimeField(db_index=True, verbose_name="End (UTC)")),
                ("effective_start", models.DateTimeField(verbose_name="Effective start (UTC)")),
                ("effective_end", models.DateTimeField(verbose_name="Effective end (UTC)")),
                ("capacity", models.PositiveSmallIntegerField(default=1, verbose_name="Capacity")),
                ("spaces_used", models.PositiveSmallIntegerField(default=0, verbose_name="Spaces used")),
                ("status", models.CharField(
                    choices=[("available","Available"),("partial","Partially Booked"),("full","Fully Booked"),("blocked","Blocked"),("cancelled","Cancelled"),("completed","Completed")],
                    default="available", db_index=True, max_length=20, verbose_name="Status",
                )),
                ("is_walk_in_slot", models.BooleanField(default=False, verbose_name="Walk-in slot")),
                ("video_join_url_citizen", models.URLField(blank=True, verbose_name="Citizen video join URL")),
                ("video_join_url_staff", models.URLField(blank=True, verbose_name="Staff video join URL")),
                ("video_meeting_id", models.CharField(blank=True, max_length=200, verbose_name="Video meeting ID")),
                ("video_provider", models.CharField(blank=True, max_length=20, verbose_name="Video provider")),
                ("internal_note", models.TextField(blank=True, verbose_name="Internal note")),
                ("appointment_type", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="slots",
                    to="appointments.appointmenttype",
                    verbose_name="Appointment type",
                )),
                ("location", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="slots",
                    to="appointments.location",
                    verbose_name="Location",
                )),
                ("resource", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="slots",
                    to="appointments.resource",
                    verbose_name="Resource",
                )),
                ("staff", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="slots",
                    to="appointments.staffprofile",
                    verbose_name="Staff member",
                )),
            ],
            options={
                "verbose_name": "Slot",
                "verbose_name_plural": "Slots",
                "ordering": ["start_datetime"],
            },
        ),
        migrations.AddIndex(
            model_name="slot",
            index=models.Index(fields=["appointment_type", "start_datetime", "status"], name="appt_slot_appttype_dt_status"),
        ),
        migrations.AddIndex(
            model_name="slot",
            index=models.Index(fields=["staff", "start_datetime"], name="appt_slot_staff_dt"),
        ),
        migrations.AddIndex(
            model_name="slot",
            index=models.Index(fields=["location", "start_datetime", "status"], name="appt_slot_loc_dt_status"),
        ),
        migrations.AddIndex(
            model_name="slot",
            index=models.Index(fields=["effective_start", "effective_end"], name="appt_slot_eff_start_end"),
        ),
        migrations.AddConstraint(
            model_name="slot",
            constraint=models.CheckConstraint(
                condition=models.Q(end_datetime__gt=models.F("start_datetime")),
                name="appt_slot_end_after_start",
            ),
        ),
        migrations.AddConstraint(
            model_name="slot",
            constraint=models.CheckConstraint(
                condition=models.Q(spaces_used__lte=models.F("capacity")),
                name="appt_slot_spaces_lte_capacity",
            ),
        ),
        migrations.AddConstraint(
            model_name="slot",
            constraint=models.CheckConstraint(
                condition=models.Q(effective_start__lte=models.F("start_datetime")),
                name="appt_slot_eff_start_lte_start",
            ),
        ),
        migrations.AddConstraint(
            model_name="slot",
            constraint=models.CheckConstraint(
                condition=models.Q(effective_end__gte=models.F("end_datetime")),
                name="appt_slot_eff_end_gte_end",
            ),
        ),
    ]
