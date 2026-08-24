"""
Migration 0007: GovStack Consent BB v1.3.0 alignment.

Adds:
  - ConsentPolicy model (GovStack Policy)
  - ConsentRevision model (GovStack Revision — tamper-proof snapshots)
  - ConsentWebhook model (GovStack Webhook)

Extends ConsentCategory (GovStack DataAgreement) with:
  - policy (FK to ConsentPolicy)
  - version
  - data_use
  - dpia
  - forgettable
  - controller_name
  - controller_url
  - attributes (JSON)

Extends ConsentRecord (GovStack ConsentRecord) with:
  - state (unsigned / pending_signatures / signed)
  - data_agreement_revision (FK to ConsentRevision)
  - data_agreement_revision_hash

Updates ConsentAuditEntry CheckConstraint to add rtbf action types.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("consent", "0006_alter_dataexportrequest_document"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [  # noqa: RUF012
        # ------------------------------------------------------------------
        # 1. ConsentPolicy
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ConsentPolicy",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("version", models.CharField(max_length=50)),
                (
                    "url",
                    models.URLField(
                        help_text="Permanent URL at which this version of the Policy can be read."
                    ),
                ),
                ("jurisdiction", models.CharField(blank=True, max_length=100)),
                ("industry_sector", models.CharField(blank=True, max_length=100)),
                (
                    "data_retention_period_days",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="How long personal data is retained (days).",
                        null=True,
                    ),
                ),
                ("geographic_restriction", models.CharField(blank=True, max_length=100)),
                ("storage_location", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Consent Policy",
                "verbose_name_plural": "Consent Policies",
                "ordering": ["-created_at"],
            },
        ),
        # ------------------------------------------------------------------
        # 2. ConsentRevision
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ConsentRevision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "schema_name",
                    models.CharField(
                        db_index=True,
                        help_text='E.g. "DataAgreement", "Policy", "ConsentRecord"',
                        max_length=100,
                    ),
                ),
                (
                    "object_id",
                    models.CharField(
                        db_index=True,
                        help_text="PK of the object that was serialized.",
                        max_length=64,
                    ),
                ),
                (
                    "serialized_snapshot",
                    models.JSONField(
                        help_text="Full serialized content of the object at revision time."
                    ),
                ),
                (
                    "serialized_hash",
                    models.CharField(
                        help_text="SHA-256 hash of serialized_snapshot (JSON, sort_keys=True).",
                        max_length=64,
                    ),
                ),
                ("timestamp", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "authorized_by_other",
                    models.CharField(
                        blank=True,
                        help_text="Reference to an admin/system that created this revision.",
                        max_length=255,
                    ),
                ),
                (
                    "predecessor_hash",
                    models.CharField(
                        blank=True,
                        help_text="serializedHash of the previous revision for this object.",
                        max_length=64,
                    ),
                ),
                (
                    "authorized_by_individual",
                    models.ForeignKey(
                        blank=True,
                        help_text="The individual who authorized this revision.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="consent_revisions_individual",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "successor",
                    models.OneToOneField(
                        blank=True,
                        help_text="This revision is no longer the latest — refer to successor.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="predecessor",
                        to="consent.consentrevision",
                    ),
                ),
            ],
            options={
                "verbose_name": "Consent Revision",
                "verbose_name_plural": "Consent Revisions",
                "ordering": ["-timestamp"],
            },
        ),
        migrations.AddIndex(
            model_name="consentrevision",
            index=models.Index(
                fields=["schema_name", "object_id", "timestamp"],
                name="consent_rev_schema__f5b8a2_idx",
            ),
        ),
        # ------------------------------------------------------------------
        # 3. ConsentWebhook
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ConsentWebhook",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "payload_url",
                    models.URLField(
                        help_text="The URL to which the webhook payload will be POSTed."
                    ),
                ),
                (
                    "content_type",
                    models.CharField(
                        choices=[
                            ("application/json", "application/json"),
                            (
                                "application/x-www-form-urlencoded",
                                "application/x-www-form-urlencoded",
                            ),
                        ],
                        default="application/json",
                        max_length=50,
                    ),
                ),
                (
                    "is_disabled",
                    models.BooleanField(
                        default=False, help_text="If True, this webhook will not receive events."
                    ),
                ),
                (
                    "secret_key",
                    models.CharField(
                        help_text="HMAC secret key used to sign payloads (SHA-256).", max_length=255
                    ),
                ),
                (
                    "subscribed_events",
                    models.JSONField(
                        default=list,
                        help_text="List of event type strings this webhook is subscribed to.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Consent Webhook",
                "verbose_name_plural": "Consent Webhooks",
                "ordering": ["-created_at"],
            },
        ),
        # ------------------------------------------------------------------
        # 4. Extend ConsentCategory with GovStack DataAgreement fields
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="consentcategory",
            name="policy",
            field=models.ForeignKey(
                blank=True,
                help_text="GovStack Policy that governs this Data Agreement.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="data_agreements",
                to="consent.consentpolicy",
            ),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="version",
            field=models.CharField(
                blank=True,
                default="1.0.0",
                help_text="Data Agreement version string.",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="data_use",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not specified"),
                    ("data_source", "Data Source"),
                    ("data_using_service", "Data Using Service"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="dpia",
            field=models.TextField(
                blank=True, help_text="Data Protection Impact Assessment (DPIA) summary."
            ),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="forgettable",
            field=models.BooleanField(
                default=False,
                help_text="If True, Consent Records may be deleted on withdrawal (RTBF / GovStack forgettable flag).",
            ),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="controller_name",
            field=models.CharField(
                blank=True, help_text="Name of the data controller organisation.", max_length=255
            ),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="controller_url",
            field=models.URLField(blank=True, help_text="URL of the data controller organisation."),
        ),
        migrations.AddField(
            model_name="consentcategory",
            name="attributes",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of DataAgreementAttribute objects: [{name, sensitivity, category}, ...]",
            ),
        ),
        # Extend lawful_basis choices to match GovStack spec
        migrations.AlterField(
            model_name="consentcategory",
            name="lawful_basis",
            field=models.CharField(
                choices=[
                    ("consent", "Consent"),
                    ("legal_obligation", "Legal Obligation"),
                    ("vital_interests", "Vital Interests"),
                    ("contract", "Contract"),
                    ("public_task", "Public Task"),
                    ("legitimate_interest", "Legitimate Interest"),
                ],
                default="consent",
                max_length=30,
            ),
        ),
        # ------------------------------------------------------------------
        # 5. Extend ConsentRecord with GovStack fields
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="consentrecord",
            name="state",
            field=models.CharField(
                choices=[
                    ("unsigned", "Unsigned"),
                    ("pending_signatures", "Pending Signatures"),
                    ("signed", "Signed"),
                ],
                db_index=True,
                default="unsigned",
                help_text="GovStack signing state: unsigned / pending_signatures / signed.",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="consentrecord",
            name="data_agreement_revision",
            field=models.ForeignKey(
                blank=True,
                help_text="Revision of the DataAgreement that was in effect when consent was given.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="consent_records",
                to="consent.consentrevision",
            ),
        ),
        migrations.AddField(
            model_name="consentrecord",
            name="data_agreement_revision_hash",
            field=models.CharField(
                blank=True,
                help_text="Copy of the Revision hash at the time of consent. Ensures against tampering with the original Data Agreement.",
                max_length=64,
            ),
        ),
        # ------------------------------------------------------------------
        # 6. Update ConsentAuditEntry CheckConstraint to add RTBF actions
        # ------------------------------------------------------------------
        migrations.RemoveConstraint(
            model_name="consentauditentry",
            name="consent_audit_valid_action",
        ),
        migrations.AddConstraint(
            model_name="consentauditentry",
            constraint=models.CheckConstraint(
                check=models.Q(
                    action__in=[
                        "granted",
                        "withdrawn",
                        "export_requested",
                        "export_ready",
                        "export_delivered",
                        "export_expired",
                        "export_failed",
                        "export_downloaded",
                        "export_marked_delivered",
                        "rtbf_requested",
                        "rtbf_completed",
                    ]
                ),
                name="consent_audit_valid_action",
            ),
        ),
    ]
