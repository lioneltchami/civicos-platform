# Generated 2026-07-02 — Wave 5 Phase A: volunteer impact reporting types.
#
# Adds REPORT_TYPE_VOLUNTEERS to ReportSnapshot.report_type choices and
# EXPORT_TYPE_VOLUNTEER_HOURS / EXPORT_TYPE_VOLUNTEER_T3010 to
# ExportRecord.export_type choices.
#
# Both fields use CharField with a choices list; Django stores choices only in
# Python (not in the DB column definition), so this migration is effectively a
# no-op at the DB level for most backends. It is included for correctness and
# to satisfy --check during CI.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0003_exportrecord_actor_pk_bigint'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reportsnapshot',
            name='report_type',
            field=models.CharField(
                choices=[
                    ('financial', _('Financial')),
                    ('donations', _('Donations & CRA')),
                    ('operational', _('Operational')),
                    ('volunteers', _('Volunteer Impact')),
                ],
                max_length=20,
                verbose_name=_('Report type'),
            ),
        ),
        migrations.AlterField(
            model_name='exportrecord',
            name='export_type',
            field=models.CharField(
                choices=[
                    ('reconciliation', _('Payment reconciliation')),
                    ('t3010_prep', _('T3010 preparatory data')),
                    ('receipts', _('Donation receipts list')),
                    ('revenue', _('Monthly revenue')),
                    ('refunds', _('Refund summary')),
                    ('volunteer_hours', _('Volunteer hours (PIPEDA)')),
                    ('volunteer_t3010', _('Volunteer T3010 data')),
                ],
                max_length=30,
                verbose_name=_('Export type'),
            ),
        ),
    ]
