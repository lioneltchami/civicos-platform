from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0008_staffexception_uniqueconstraint"),
    ]

    operations = [  # noqa: RUF012
        migrations.AddIndex(
            model_name="availabilitytemplate",
            index=models.Index(
                fields=["created_at"],
                name="appt_availtpl_created_at_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="staffexception",
            index=models.Index(
                fields=["created_at"],
                name="appt_staffexc_created_at_idx",
            ),
        ),
    ]
