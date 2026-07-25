# Generated manually — GovStack Scheduler BB role-based access control.
#
# Adds a `role` field to GovStackRegisteredBB so that a registered BB's
# maximum GovStack Scheduler actor role (resource / organizer / admin) can
# be resolved from the whitelist row itself, instead of being (incorrectly)
# self-declared by the calling view.  Consulted by
# apps.appointments.govstack_auth.GovStackSchedulerAuth when
# GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True.
#
# Default value "organizer" is intentional: a safe, conservative default for
# existing/new whitelist rows that does NOT silently grant admin-tier access.
#
# This migration does not modify any other model or column.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0021_govstack_registered_bb"),
    ]

    operations = [
        migrations.AddField(
            model_name="govstackregisteredbb",
            name="role",
            field=models.CharField(
                choices=[
                    ("resource", "Resource"),
                    ("organizer", "Organizer"),
                    ("admin", "Admin"),
                ],
                db_index=True,
                default="organizer",
                help_text=(
                    "Maximum GovStack Scheduler BB actor role this registered BB may act "
                    "as (subscriber → resource → organizer → admin). Consulted only by "
                    "the Scheduler BB's GovStackSchedulerAuth when "
                    "GOVSTACK_SCHEDULER_REQUIRE_TOKEN=True. Does not apply to "
                    "citizen/subscriber-tier calls, which require a CivicOS citizen "
                    "access token (JWT) instead of a bb_id role."
                ),
                max_length=20,
                verbose_name="Scheduler Role",
            ),
        ),
    ]
