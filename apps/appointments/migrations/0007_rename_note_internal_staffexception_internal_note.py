from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0006_wave2_high_fixes"),
    ]

    operations = [  # noqa: RUF012
        migrations.RenameField(
            model_name="staffexception",
            old_name="note_internal",
            new_name="internal_note",
        ),
    ]
