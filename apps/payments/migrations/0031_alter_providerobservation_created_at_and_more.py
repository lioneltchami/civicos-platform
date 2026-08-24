from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("payments", "0030_provider_observation"),
    ]

    operations = [  # noqa: RUF012
        migrations.AlterField(
            model_name="providerobservation",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Created at"),
        ),
        migrations.AlterField(
            model_name="providerobservation",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="Updated at"),
        ),
    ]
