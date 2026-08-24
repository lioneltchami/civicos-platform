# Generated migration — H-4 and H-5 fixes for Volunteer Management BB.
# Covers:
#   H-4: VolunteerApplication.consent_record SET_NULL → PROTECT
#        ConsentRecords must never be hard-deleted; PIPEDA erasure anonymizes
#        their content in-place via the Consent BB. SET_NULL silently destroys
#        the legal record of which agreement version the volunteer signed.
#   H-5: Certification.volunteer CASCADE → PROTECT
#        Certifications are compliance records (First Aid, CPR, VSC) that must
#        survive profile deletion. Erasure workflows must explicitly archive or
#        redact certifications before deleting the profile.
#
# Note: H-2 (select_for_update in Honorarium.clean) is application-level only;
#       no migration is required.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("volunteers", "0003_wave1_review_fixes"),
        ("consent", "0001_initial"),
    ]

    operations = [  # noqa: RUF012
        # H-4: VolunteerApplication.consent_record SET_NULL → PROTECT
        # Reverts the M-2 change from 0002 that introduced SET_NULL for PIPEDA
        # right-to-erasure. The correct design is anonymization-in-place; hard
        # deletion of a ConsentRecord destroys proof of the signed agreement version.
        migrations.AlterField(
            model_name="volunteerapplication",
            name="consent_record",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="volunteer_applications",
                to="consent.consentrecord",
                verbose_name="Consent record",
            ),
        ),
        # H-5: Certification.volunteer CASCADE → PROTECT
        # Prevents silent destruction of VSC and other compliance certifications
        # when a volunteer profile is deleted during an erasure workflow.
        migrations.AlterField(
            model_name="certification",
            name="volunteer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="certifications",
                to="volunteers.volunteerprofile",
                verbose_name="Volunteer",
            ),
        ),
    ]
