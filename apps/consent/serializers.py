"""
Serializers for the Consent & Privacy API.
"""
from rest_framework import serializers

from .models import ConsentAuditEntry, ConsentCategory, ConsentRecord, DataExportRequest


class ConsentCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentCategory
        fields = [
            "id", "slug", "name_en", "name_fr", "purpose_en", "purpose_fr",
            "lawful_basis", "is_required", "sort_order",
        ]
        read_only_fields = fields


class ConsentRecordSerializer(serializers.ModelSerializer):
    category = ConsentCategorySerializer(read_only=True)
    category_slug = serializers.SlugRelatedField(
        source="category", slug_field="slug", read_only=True
    )

    class Meta:
        model = ConsentRecord
        fields = [
            "id", "category", "category_slug", "status", "granted_at",
            "withdrawn_at", "source",
        ]
        read_only_fields = [
            "id", "category", "category_slug", "granted_at",
            "withdrawn_at", "source",
        ]


class ConsentUpdateSerializer(serializers.Serializer):
    """Used for PATCH /api/v1/consent/records/<category_slug>/"""
    action = serializers.ChoiceField(choices=["grant", "withdraw"])


class DataExportRequestSerializer(serializers.ModelSerializer):
    # download_token is the bearer credential for downloading the export.
    # Only expose it once the export is ready — never for pending/processing/failed.
    download_token = serializers.SerializerMethodField()

    class Meta:
        model = DataExportRequest
        fields = [
            "id", "status", "format", "requested_at", "processed_at",
            "expires_at", "download_token",
        ]
        read_only_fields = [
            "id", "status", "format", "requested_at", "processed_at", "expires_at",
        ]

    def get_download_token(self, obj):
        if obj.status == DataExportRequest.STATUS_READY:
            return str(obj.download_token)
        return None


class ConsentAuditEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentAuditEntry
        fields = ["id", "action", "timestamp", "details"]
        read_only_fields = fields
