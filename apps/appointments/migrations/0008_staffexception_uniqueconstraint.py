from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0007_rename_note_internal_staffexception_internal_note"),
    ]

    operations = [  # noqa: RUF012
        migrations.AlterUniqueTogether(
            name="staffexception",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="staffexception",
            constraint=models.UniqueConstraint(
                fields=["staff", "exception_date"],
                name="appt_staffexc_staff_date_uniq",
            ),
        ),
    ]
