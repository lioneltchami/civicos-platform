"""
Wave D review fix — FIX 9.

Adds AppointmentType.is_govstack_managed, an explicit boolean discriminator
for the GovStack Scheduler BB Event API boundary. Previously the boundary
was inferred purely from AppointmentType.slug.startswith("gs-"), which risks
silently sweeping in a native CivicOS AppointmentType whose name happens to
slugify to a "gs-" prefix (e.g. "GS Drivers License Renewal").

This field is set True only by the GovStack event_create() service function.
The gs- slug prefix convention is kept as a secondary/cosmetic naming style,
but the actual security/scoping boundary for event_list() is this field.
"""
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0014_govstack_wave_a_review_fixes"),
    ]

    operations = [
        migrations.AddField(
            model_name="appointmenttype",
            name="is_govstack_managed",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text=(
                    "True if this AppointmentType was created via the GovStack "
                    "Scheduler BB Event API. Used to scope /event/ endpoint "
                    "visibility — never set this manually via CivicOS admin."
                ),
                verbose_name="GovStack-managed",
            ),
        ),
    ]
