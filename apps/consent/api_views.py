"""
Consent & Privacy REST API endpoints.

GET  /api/v1/consent/categories/           — list active categories
GET  /api/v1/consent/records/              — citizen's consent records
PATCH /api/v1/consent/records/<slug>/      — grant or withdraw a consent
POST /api/v1/consent/export-requests/      — create export request
GET  /api/v1/consent/export-requests/      — list citizen's export requests
GET  /api/v1/consent/export-requests/<pk>/ — single export request status

Security invariants:
- All views require IsAuthenticated — no anonymous access.
- All querysets are scoped to request.user — IDOR impossible.
- ValueError from service layer -> 400 via ValidationError.
- No PII written to logs — only PKs.
"""
import logging

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.authentication import CivicOSTokenAuthentication

from .models import DataExportRequest
from .serializers import (
    ConsentCategorySerializer,
    ConsentRecordSerializer,
    ConsentUpdateSerializer,
    DataExportRequestSerializer,
)
from .services import ConsentService

logger = logging.getLogger(__name__)

_AUTH = [CivicOSTokenAuthentication, JWTAuthentication]


class ConsentCategoryListView(ListAPIView):
    """GET /api/v1/consent/categories/ -- list all active consent categories."""

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]
    serializer_class = ConsentCategorySerializer
    # No pagination -- citizens see a small, complete list of categories
    pagination_class = None

    def get_queryset(self):
        return ConsentService.get_active_categories()


class ConsentRecordListView(ListAPIView):
    """GET /api/v1/consent/records/ -- list citizen's consent records."""

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]
    serializer_class = ConsentRecordSerializer
    # No pagination -- citizens see their own small set of records
    pagination_class = None

    def get_queryset(self):
        return ConsentService.get_citizen_consents(self.request.user)


class ConsentRecordUpdateView(APIView):
    """PATCH /api/v1/consent/records/<category_slug>/ -- grant or withdraw."""

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def patch(self, request, category_slug):
        serializer = ConsentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        try:
            if action == "grant":
                record = ConsentService.grant(request.user, category_slug, request=request)
            else:
                record = ConsentService.withdraw(request.user, category_slug, request=request)
        except ValueError as exc:
            logger.info(
                "Consent %s rejected for user_id=%s category=%s: %s",
                action, request.user.pk, category_slug, exc,
            )
            raise ValidationError(str(exc))

        return Response(ConsentRecordSerializer(record).data)


class DataExportRequestListCreateView(APIView):
    """GET/POST /api/v1/consent/export-requests/"""

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ConsentService.get_citizen_exports(request.user)
        return Response(DataExportRequestSerializer(qs, many=True).data)

    def post(self, request):
        try:
            export_req = ConsentService.request_export(request.user, request=request)
        except ValueError as exc:
            logger.info(
                "Export request rejected for user_id=%s: %s",
                request.user.pk, exc,
            )
            raise ValidationError(str(exc))
        return Response(
            DataExportRequestSerializer(export_req).data,
            status=status.HTTP_201_CREATED,
        )


class DataExportRequestDetailView(RetrieveAPIView):
    """GET /api/v1/consent/export-requests/<pk>/"""

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]
    serializer_class = DataExportRequestSerializer

    def get_queryset(self):
        # Scope to the authenticated citizen -- IDOR protection.
        return DataExportRequest.objects.filter(citizen=self.request.user)
