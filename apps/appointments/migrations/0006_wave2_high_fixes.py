import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0005_wave2_constraint_fixes"),
    ]

    operations = [  # noqa: RUF012
        # H-1: PROTECT on AvailabilityTemplate.staff
        # PIPEDA 4.5.3: AvailabilityTemplates are audit records explaining when a staff
        # member was scheduled. Silent cascade-delete on StaffProfile removal violates
        # auditability. Staff must be explicitly decommissioned before deletion.
        migrations.AlterField(
            model_name="availabilitytemplate",
            name="staff",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="availability_templates",
                to="appointments.staffprofile",
                verbose_name="Staff profile",
            ),
        ),
        # H-1: PROTECT on StaffException.staff
        # PIPEDA 4.5.3: StaffExceptions explain WHY slots were not generated on a date.
        # Silent cascade-delete removes this audit trail. Staff must be explicitly
        # decommissioned before deletion.
        migrations.AlterField(
            model_name="staffexception",
            name="staff",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="exceptions",
                to="appointments.staffprofile",
                verbose_name="Staff profile",
            ),
        ),
        # H-2: DB-level CheckConstraint for override exception requiring both times.
        # Mirrors the Python-layer check in StaffException.clean(). Prevents direct SQL
        # from creating a broken override record that would crash the slot generator on
        # None access for override_start_time / override_end_time.
        migrations.AddConstraint(
            model_name="staffexception",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(exception_type="override")
                    | (
                        models.Q(override_start_time__isnull=False)
                        & models.Q(override_end_time__isnull=False)
                    )
                ),
                name="appt_staffexc_override_requires_times",
            ),
        ),
    ]
