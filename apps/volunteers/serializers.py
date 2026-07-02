"""
Volunteer Management BB — DRF serializers.

PIPEDA rules enforced here:
- VolunteerProfileSerializer strips sensitive fields unless the requesting user
  holds volunteers.view_accommodation_notes permission.
- VolunteerApplicationSerializer NEVER exposes rejection_reason.
- HoursLog rejection_reason is NEVER exposed via this module.
- All Decimal fields serialized as strings (DecimalField).
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    Shift,
    ShiftBooking,
    VolunteerApplication,
    VolunteerProfile,
)


# ---------------------------------------------------------------------------
# VolunteerOpportunitySerializer
# ---------------------------------------------------------------------------

class VolunteerOpportunitySerializer(serializers.ModelSerializer):
    """
    Public-facing opportunity listing — no PII, bilingual title fields.
    """
    program_slug = serializers.CharField(source="program.slug", read_only=True)
    program_title = serializers.CharField(source="program.title_en", read_only=True)
    title = serializers.CharField(source="title_en", read_only=True)
    title_fr = serializers.CharField(read_only=True)
    description = serializers.CharField(source="description_en", read_only=True)
    is_open = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = [
            "slug",
            "title",
            "title_fr",
            "description",
            "is_open",
            "status",
            "published_at",
            "closes_at",
            "location_name",
            "is_remote",
            "volunteer_capacity",
            "requires_vulnerable_sector_check",
            "program_slug",
            "program_title",
        ]
        read_only_fields = fields

    def get_is_open(self, obj) -> bool:
        return obj.is_accepting_applications


# ---------------------------------------------------------------------------
# ShiftSerializer
# ---------------------------------------------------------------------------

class ShiftSerializer(serializers.ModelSerializer):
    """
    Shift data — capacity vs booking status computed server-side.
    """
    opportunity_slug = serializers.CharField(source="opportunity.slug", read_only=True)
    booked_count = serializers.SerializerMethodField()
    is_bookable = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = [
            "id",
            "opportunity_slug",
            "start_datetime",
            "end_datetime",
            "location_override",
            "is_remote",
            "capacity",
            "booked_count",
            "is_bookable",
            "is_cancelled",
        ]
        read_only_fields = fields

    def get_booked_count(self, obj) -> int:
        return ShiftBooking.objects.filter(
            shift=obj,
            status=ShiftBooking.STATUS_CONFIRMED,
        ).count()

    def get_is_bookable(self, obj) -> bool:
        if obj.is_cancelled:
            return False
        if obj.start_datetime <= timezone.now():
            return False
        effective_cap = obj.effective_capacity
        if effective_cap is None:
            # Unlimited capacity
            return True
        booked = self.get_booked_count(obj)
        return booked < effective_cap


# ---------------------------------------------------------------------------
# VolunteerApplicationSerializer
# ---------------------------------------------------------------------------

class VolunteerApplicationSerializer(serializers.ModelSerializer):
    """
    Application data — NEVER exposes rejection_reason (PIPEDA).
    """
    opportunity_slug = serializers.CharField(source="opportunity.slug", read_only=True)
    opportunity_title = serializers.CharField(source="opportunity.title_en", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    # created_at serves as applied_at (VolunteerApplication has no separate applied_at field)
    applied_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = VolunteerApplication
        fields = [
            "id",
            "opportunity_slug",
            "opportunity_title",
            "status",
            "status_display",
            "applied_at",
            "motivation",
        ]
        read_only_fields = [
            "id",
            "opportunity_slug",
            "opportunity_title",
            "status",
            "status_display",
            "applied_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Belt-and-suspenders: strip internal fields
        data.pop("rejection_reason", None)
        data.pop("screening_notes", None)
        return data


# ---------------------------------------------------------------------------
# HoursLogSerializer
# ---------------------------------------------------------------------------

class HoursLogSerializer(serializers.ModelSerializer):
    """
    Hours log — rejection_reason is NEVER exposed via this serializer.
    """
    opportunity_title = serializers.CharField(source="opportunity.title_en", read_only=True)
    hours = serializers.DecimalField(max_digits=6, decimal_places=2)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = HoursLog
        fields = [
            "id",
            "opportunity_title",
            "date",
            "hours",
            "description",
            "status",
            "status_display",
            "approved_at",
        ]
        read_only_fields = [
            "id",
            "opportunity_title",
            "status",
            "status_display",
            "approved_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # PIPEDA: rejection_reason never exposed to volunteers or via API
        data.pop("rejection_reason", None)
        return data


# ---------------------------------------------------------------------------
# VolunteerProfileSerializer
# ---------------------------------------------------------------------------

class VolunteerProfileSerializer(serializers.ModelSerializer):
    """
    PIPEDA-aware profile serializer.

    Sensitive fields (accommodation_notes, emergency_contact_*, sin_last4)
    are included in the response ONLY when the requesting user holds the
    'volunteers.view_accommodation_notes' permission.

    sin_encrypted is NEVER returned under any circumstances.
    """
    display_name = serializers.CharField(read_only=True)
    skills = serializers.StringRelatedField(many=True, read_only=True)
    total_hours_approved = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = VolunteerProfile
        fields = [
            "id",
            "display_name",
            "preferred_name",
            "preferred_language",
            "phone_number",
            "skills",
            "status",
            "total_hours_approved",
            "available_weekdays",
            "available_weekends",
            "available_evenings",
            "availability_notes",
            "created_at",
            # Sensitive — stripped in to_representation without permission
            "accommodation_notes",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "sin_last4",
        ]
        read_only_fields = [
            "id",
            "display_name",
            "skills",
            "status",
            "total_hours_approved",
            "created_at",
            "sin_last4",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)

        request = self.context.get("request")
        has_perm = (
            request is not None
            and request.user.is_authenticated
            and request.user.has_perm("volunteers.view_accommodation_notes")
        )

        if not has_perm:
            data.pop("accommodation_notes", None)
            data.pop("emergency_contact_name", None)
            data.pop("emergency_contact_phone", None)
            data.pop("emergency_contact_relationship", None)
            data.pop("sin_last4", None)

        # NEVER expose sin_encrypted regardless of permissions
        data.pop("sin_encrypted", None)

        return data
