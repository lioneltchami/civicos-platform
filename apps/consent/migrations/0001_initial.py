# Generated migration for apps/consent — PIPEDA-compliant schema

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ConsentCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated at')),
                ('slug', models.CharField(max_length=100, unique=True)),
                ('name_en', models.CharField(max_length=255)),
                ('name_fr', models.CharField(max_length=255)),
                ('purpose_en', models.TextField(help_text='Plain-language explanation of why data is collected (PIPEDA 4.2).')),
                ('purpose_fr', models.TextField()),
                ('lawful_basis', models.CharField(
                    choices=[('consent', 'Consent'), ('legal_obligation', 'Legal Obligation'), ('vital_interests', 'Vital Interests')],
                    default='consent',
                    max_length=30,
                )),
                ('is_required', models.BooleanField(
                    default=False,
                    help_text='If True, consent cannot be withdrawn — required for essential service delivery.',
                )),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Consent Category',
                'verbose_name_plural': 'Consent Categories',
                'ordering': ['sort_order', 'slug'],
            },
        ),
        migrations.CreateModel(
            name='DataExportRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'), ('processing', 'Processing'), ('ready', 'Ready'),
                        ('delivered', 'Delivered'), ('failed', 'Failed'), ('expired', 'Expired'),
                    ],
                    db_index=True,
                    default='pending',
                    max_length=20,
                )),
                ('format', models.CharField(
                    choices=[('json', 'JSON')],
                    default='json',
                    max_length=10,
                )),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, help_text='7 days after processed_at.', null=True)),
                ('download_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('storage_path', models.CharField(
                    blank=True,
                    help_text='Internal file path — do not expose to citizens.',
                    max_length=500,
                )),
                ('notes', models.TextField(blank=True, help_text='Staff notes.')),
                ('citizen', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='export_requests',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Data Export Request',
                'verbose_name_plural': 'Data Export Requests',
                'ordering': ['-requested_at'],
            },
        ),
        migrations.CreateModel(
            name='ConsentRecord',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated at')),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('granted', 'Granted'), ('withdrawn', 'Withdrawn')],
                    db_index=True,
                    default='pending',
                    max_length=20,
                )),
                ('granted_at', models.DateTimeField(blank=True, null=True)),
                ('withdrawn_at', models.DateTimeField(blank=True, null=True)),
                ('actor_ip', models.GenericIPAddressField(
                    blank=True,
                    help_text='Always masked before storage.',
                    null=True,
                )),
                ('source', models.CharField(
                    choices=[('web', 'Web Portal'), ('api', 'API'), ('admin', 'Admin')],
                    default='web',
                    max_length=20,
                )),
                ('consent_version', models.CharField(
                    blank=True,
                    help_text='Identifies which version of the consent text was shown.',
                    max_length=50,
                )),
                ('citizen', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='consent_records',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('category', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='records',
                    to='consent.consentcategory',
                )),
            ],
            options={
                'verbose_name': 'Consent Record',
                'verbose_name_plural': 'Consent Records',
                'ordering': ['-created_at'],
                'unique_together': {('citizen', 'category')},
            },
        ),
        migrations.CreateModel(
            name='ConsentAuditEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(
                    choices=[
                        ('granted', 'Granted'), ('withdrawn', 'Withdrawn'),
                        ('export_requested', 'Export Requested'), ('export_ready', 'Export Ready'),
                        ('export_delivered', 'Export Delivered'), ('export_expired', 'Export Expired'),
                        ('export_failed', 'Export Failed'),
                    ],
                    db_index=True,
                    max_length=30,
                )),
                ('actor_ip', models.GenericIPAddressField(
                    blank=True,
                    help_text='Always masked before storage.',
                    null=True,
                )),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('citizen', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='consent_audit_entries',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('actor', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='consent_audit_actions',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('category', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    to='consent.consentcategory',
                )),
                ('export_request', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    to='consent.dataexportrequest',
                )),
            ],
            options={
                'verbose_name': 'Consent Audit Entry',
                'verbose_name_plural': 'Consent Audit Entries',
                'ordering': ['-timestamp'],
            },
        ),
    ]
