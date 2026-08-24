"""
Management command: anonymize_volunteer
=========================================
Anonymizes personal data for a volunteer profile in response to a PIPEDA
right-to-erasure request, while retaining organizationally-required records.

WHAT IS ANONYMIZED (PII removed):
  - VolunteerProfile: preferred_name, phone_number, date_of_birth,
      emergency_contact_name, emergency_contact_phone,
      emergency_contact_relationship, accommodation_notes,
      sin_encrypted, sin_last4, photo (file deleted + field cleared)
  - The user account email is replaced with an opaque token.
  - VolunteerNote.body: replaced with "[redacted — PIPEDA erasure YYYY-MM-DD]"

WHAT IS RETAINED (organizational / CRA requirement):
  - HoursLog records (retained by volunteer PK, no PII)
  - Honorarium records (CRA T4A obligation — retained 7 years)
  - ScreeningRecord records (due-diligence audit trail — retained by PK)
  - VolunteerApplication records (organizational accountability)
  - RecognitionMilestone records (hours-based, no PII)

Usage::

    python manage.py anonymize_volunteer <profile_pk>
    python manage.py anonymize_volunteer <profile_pk> --dry-run
    python manage.py anonymize_volunteer <profile_pk> --confirm

This command MUST be run by a system administrator in response to a formal
PIPEDA access/deletion request. It writes an AuditLogEntry on completion.
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Anonymize a volunteer's PII in response to a PIPEDA erasure request (irreversible)."

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument("profile_pk", type=int, help="VolunteerProfile primary key")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without writing to the database.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required for actual execution (prevents accidental runs).",
        )

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        from apps.volunteers.models import VolunteerApplication, VolunteerNote, VolunteerProfile

        profile_pk = options["profile_pk"]
        dry_run = options["dry_run"]
        confirm = options["confirm"]

        try:
            profile = VolunteerProfile.objects.select_related("user").get(pk=profile_pk)
        except VolunteerProfile.DoesNotExist:
            raise CommandError(f"VolunteerProfile #{profile_pk} does not exist.")  # noqa: B904

        if not dry_run and not confirm:
            raise CommandError(
                "Pass --confirm to execute. This operation is IRREVERSIBLE. "
                "Use --dry-run first to review changes."
            )

        erasure_token = uuid.uuid4().hex[:12]
        redaction_note = (
            f"[redacted — PIPEDA erasure {timezone.localtime(timezone.now()).date().isoformat()}]"
        )

        self.stdout.write(f"\nAnonymizing VolunteerProfile #{profile_pk}")
        self.stdout.write(
            f"  User email: {profile.user.email!r} → 'erased-{erasure_token}@pipeda.invalid'"
        )
        self.stdout.write(f"  preferred_name: {profile.preferred_name!r} → ''")
        self.stdout.write(f"  phone_number: {profile.phone_number!r} → ''")
        self.stdout.write(f"  date_of_birth: {profile.date_of_birth!r} → None")
        self.stdout.write("  emergency_contact_*: → ''")
        self.stdout.write("  accommodation_notes: → ''")
        self.stdout.write("  sin_encrypted / sin_last4: → None / ''")
        if profile.photo:
            self.stdout.write(f"  photo: {profile.photo.name!r} → DELETED + cleared")

        note_count = VolunteerNote.objects.filter(volunteer=profile).count()
        self.stdout.write(f"  VolunteerNotes: {note_count} body/bodies → redacted")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] No changes written."))
            return

        # --- Execute anonymization ---
        # Delete photo file from storage
        if profile.photo:
            try:
                profile.photo.delete(save=False)
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(f"  Warning: could not delete photo file: {exc}")
                )

        # Anonymize profile — only update fields that exist on the model
        # (check actual model field names before running)
        update_fields = []
        if hasattr(profile, "preferred_name"):
            profile.preferred_name = ""
            update_fields.append("preferred_name")
        if hasattr(profile, "phone_number"):
            profile.phone_number = ""
            update_fields.append("phone_number")
        if hasattr(profile, "date_of_birth"):
            profile.date_of_birth = None
            update_fields.append("date_of_birth")
        if hasattr(profile, "emergency_contact_name"):
            profile.emergency_contact_name = ""
            update_fields.append("emergency_contact_name")
        if hasattr(profile, "emergency_contact_phone"):
            profile.emergency_contact_phone = ""
            update_fields.append("emergency_contact_phone")
        if hasattr(profile, "emergency_contact_relationship"):
            profile.emergency_contact_relationship = ""
            update_fields.append("emergency_contact_relationship")
        if hasattr(profile, "accommodation_notes"):
            profile.accommodation_notes = ""
            update_fields.append("accommodation_notes")
        if hasattr(profile, "sin_encrypted"):
            profile.sin_encrypted = None
            update_fields.append("sin_encrypted")
        if hasattr(profile, "sin_last4"):
            profile.sin_last4 = ""
            update_fields.append("sin_last4")
        profile.photo = None
        update_fields.append("photo")
        profile.photo_consent_id = None
        update_fields.append("photo_consent_id")
        profile.status = "inactive"
        update_fields.append("status")
        profile.save(update_fields=update_fields)

        # Anonymize user email
        profile.user.email = f"erased-{erasure_token}@pipeda.invalid"
        profile.user.first_name = ""
        profile.user.last_name = ""
        profile.user.is_active = False
        profile.user.save(update_fields=["email", "first_name", "last_name", "is_active"])

        # Redact note bodies — use per-note save() so auto_now=True on updated_at fires,
        # giving an accurate erasure timestamp rather than the original creation timestamp.
        notes_to_redact = VolunteerNote.objects.filter(volunteer=profile)
        note_count = notes_to_redact.count()
        for note in notes_to_redact:
            note.body = redaction_note
            note.save(update_fields=["body"])

        # Redact PII fields on VolunteerApplications — records are retained for CRA
        # 7-year compliance, but motivation (volunteer's own words) and screening_notes
        # (coordinator observations that may include personal circumstances or medical
        # details) are direct PII and must be erased under PIPEDA right-to-erasure.
        applications = VolunteerApplication.objects.filter(volunteer=profile)
        application_count = applications.count()
        for application in applications:
            application.motivation = redaction_note
            application.screening_notes = redaction_note
            application.save(update_fields=["motivation", "screening_notes"])
        self.stdout.write(
            f"  Redacted motivation/screening_notes on {application_count} application(s)."
        )

        # Write PIPEDA erasure audit entry
        try:
            from apps.audit.models import AuditLogEntry

            last = AuditLogEntry.objects.order_by("-timestamp").values("entry_hash").first()
            prev_hash = last["entry_hash"] if last else ""
            AuditLogEntry.objects.create(
                event_type="data.deleted",
                outcome="success",
                actor_id=None,  # system/CLI action — no request user
                actor_email="",
                actor_ip=None,
                actor_user_agent="management-command/anonymize_volunteer",
                resource_type="volunteers.VolunteerProfile",
                resource_id=str(profile_pk),
                event_detail={
                    "action": "pipeda_erasure",
                    "fields_anonymized": [
                        "preferred_name",
                        "phone_number",
                        "date_of_birth",
                        "emergency_contact_name",
                        "emergency_contact_phone",
                        "emergency_contact_relationship",
                        "accommodation_notes",
                        "sin_encrypted",
                        "sin_last4",
                        "photo",
                        "photo_consent_id",
                    ],
                    "notes_redacted": note_count,
                    "applications_redacted": application_count,
                    "dry_run": dry_run,
                },
                request_id="",
                session_id="",
                prev_hash=prev_hash,
            )
            self.stdout.write("  [OK] PIPEDA erasure audit entry written.")
        except Exception as exc:
            self.stderr.write(f"  [WARN] Could not write audit log entry: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ VolunteerProfile #{profile_pk} anonymized successfully.\n"
                f"  Erasure token: {erasure_token}\n"
                f"  Retained: HoursLog, Honorarium, ScreeningRecord, Application, Milestone records."  # noqa: E501
            )
        )
