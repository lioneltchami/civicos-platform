"""
Wave A review fixes — GovStack Scheduler BB.

Addresses all critical and medium findings from the Wave A deep code review:

  C-2  StaffProfile missing GovStack Resource callback fields
       (gs_phone, gs_alert_url, gs_alert_preference, gs_status_poll_url)

  M-1  actor_role max_length 20 → 30 (spec §6.3 recommends 30 for extensibility)

  M-2  GovStackAffiliation unique_together (deprecated) → UniqueConstraint

  M-3  GovStackSubscriberProfile Meta missing ordering + created_at index

  M-6  GovStackMessage.entity on_delete CASCADE → PROTECT
       (gives clear ProtectedError on org delete; prevents silent cascade)

  M-7  BookingAuditLog.actor_role backfill for existing rows
       (derive "system"/"organizer"/"subscriber" from actor_id + user.is_staff)

Code-only fixes (no migration needed):
  C-1  GovStackAlertSchedule.delete() — Celery revoke (model method)
  infra-C-1  Stub views now apply GovStackSchedulerAuth + GovStackSchedulerPermission
  infra-C-2  request_token redacted from warning logs
  infra-M-3  work_days_hours DictField — child= removed
  infra-M-4  request_token removed from request.META
  infra-M-5  GovStackSchedulerRolePermission — DEBUG guard when _gs_actor_role absent
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import migrations, models

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RunPython: backfill actor_role on existing BookingAuditLog rows
# ---------------------------------------------------------------------------


def backfill_actor_role(apps, schema_editor) -> None:  # noqa: ANN001
    """
    Derive actor_role for every existing BookingAuditLog row.

    Logic (matches _write_audit_log() in services/booking.py):
      actor_id == "system"  →  "system"
      actor.is_staff        →  "organizer"
      else (citizen)        →  "subscriber"

    Rows where actor_id is blank or non-numeric are treated as "system".
    This is a best-effort backfill; all new rows will have the correct role
    set by the updated _write_audit_log() function.
    """
    BookingAuditLog = apps.get_model("appointments", "BookingAuditLog")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    # Build is_staff lookup for all user PKs referenced in audit logs.
    actor_ids_raw = (
        BookingAuditLog.objects.exclude(actor_id="system")
        .exclude(actor_id="")
        .values_list("actor_id", flat=True)
        .distinct()
    )

    # actor_id is stored as a string; try to coerce to int (Django PK).
    numeric_ids = set()
    for raw in actor_ids_raw:
        try:
            numeric_ids.add(int(raw))
        except (TypeError, ValueError):
            pass

    staff_ids = set(
        User.objects.filter(pk__in=numeric_ids, is_staff=True).values_list("pk", flat=True)
    )

    updated = 0
    # Batch update in chunks to avoid locking large tables.
    for log in BookingAuditLog.objects.only("pk", "actor_id", "actor_role").iterator(
        chunk_size=500
    ):
        if log.actor_role:
            # Already set (e.g. if migration is re-run on partial data).
            continue
        if log.actor_id in ("system", "", None):
            role = "system"
        else:
            try:
                role = "organizer" if int(log.actor_id) in staff_ids else "subscriber"
            except (TypeError, ValueError):
                role = "system"

        BookingAuditLog.objects.filter(pk=log.pk).update(actor_role=role)
        updated += 1

    logger.info("backfill_actor_role: updated %d BookingAuditLog rows", updated)


def noop(apps, schema_editor) -> None:  # noqa: ANN001
    pass


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("appointments", "0013_govstack_wave_a"),
    ]

    operations = [  # noqa: RUF012
        # ── M-1: actor_role max_length 20 → 30 ──────────────────────────────
        migrations.AlterField(
            model_name="bookingauditlog",
            name="actor_role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("admin", "Admin"),
                    ("organizer", "Organizer"),
                    ("resource", "Resource"),
                    ("subscriber", "Subscriber / Citizen"),
                    ("system", "System"),
                ],
                help_text="GovStack Scheduler BB log field: role of the actor who triggered this audit event.",
                max_length=30,
                verbose_name="Actor role",
            ),
        ),
        # ── M-7: backfill actor_role for existing BookingAuditLog rows ───────
        migrations.RunPython(backfill_actor_role, reverse_code=noop),
        # ── M-2: GovStackAffiliation — remove deprecated unique_together ─────
        migrations.AlterUniqueTogether(
            name="govstackaffiliation",
            unique_together=set(),
        ),
        # ── M-2: GovStackAffiliation — add UniqueConstraint (modern style) ───
        migrations.AddConstraint(
            model_name="govstackaffiliation",
            constraint=models.UniqueConstraint(
                fields=["resource", "entity"],
                name="appt_gs_aff_resource_entity_uniq",
            ),
        ),
        # ── M-3: GovStackSubscriberProfile — add ordering + created_at index ─
        migrations.AlterModelOptions(
            name="govstacksubscriberprofile",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "GovStack Subscriber Profile",
                "verbose_name_plural": "GovStack Subscriber Profiles",
            },
        ),
        migrations.AddIndex(
            model_name="govstacksubscriberprofile",
            index=models.Index(
                fields=["created_at"],
                name="appt_gs_subprofile_created",
            ),
        ),
        # ── M-6: GovStackMessage.entity — CASCADE → PROTECT ──────────────────
        migrations.AlterField(
            model_name="govstackmessage",
            name="entity",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="govstack_messages",
                to="appointments.organization",
                verbose_name="Entity (Organization)",
            ),
        ),
        # ── C-2: StaffProfile — add GovStack Resource callback fields ─────────
        migrations.AddField(
            model_name="staffprofile",
            name="gs_phone",
            field=models.CharField(
                blank=True,
                help_text="Phone number exposed via GovStack /resource/ API.",
                max_length=30,
                verbose_name="GovStack phone",
            ),
        ),
        migrations.AddField(
            model_name="staffprofile",
            name="gs_alert_url",
            field=models.URLField(
                blank=True,
                help_text="URL where this staff resource receives GovStack push alerts. HTTPS only.",
                verbose_name="GovStack alert URL",
            ),
        ),
        migrations.AddField(
            model_name="staffprofile",
            name="gs_alert_preference",
            field=models.CharField(
                blank=True,
                choices=[
                    ("push", "Push (HTTP callback)"),
                    ("poll", "Poll (status_poll_url)"),
                    ("email", "Email"),
                    ("sms", "SMS"),
                    ("none", "None"),
                ],
                max_length=10,
                verbose_name="GovStack alert preference",
            ),
        ),
        migrations.AddField(
            model_name="staffprofile",
            name="gs_status_poll_url",
            field=models.URLField(
                blank=True,
                help_text="URL the GovStack Scheduler polls for this staff member's availability. HTTPS only.",
                verbose_name="GovStack status poll URL",
            ),
        ),
    ]
