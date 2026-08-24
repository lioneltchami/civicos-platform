"""
Notification inbox API views.

Security contract:
- All queryset operations filter by recipient=request.user to prevent IDOR.
- No PII (email, name) is written to logs — only PKs.
- Notifications are filtered to channel=IN_APP so citizens only see their
  in-app notifications (email/SMS records exist for audit but are not exposed here).

Read-state model note:
- The Notification model uses read_at (DateTimeField, nullable) — NOT a boolean
  is_read field.  Setting is_read=True means setting read_at=now().
  Setting is_read=False clears read_at to None.
"""

import logging

from django.http import Http404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.authentication import CivicOSTokenAuthentication
from apps.api.notifications.serializers import MarkReadSerializer, NotificationSerializer
from apps.api.pagination import StandardPagination
from apps.api.throttling import CitizenRateThrottle
from apps.notifications.models import Notification, NotificationChannel

logger = logging.getLogger(__name__)

# Shared authentication stack for all notification views
_AUTH = [CivicOSTokenAuthentication, JWTAuthentication]


class NotificationListView(generics.ListAPIView):
    """
    GET /api/v1/notifications/

    Returns the authenticated citizen's in-app notification inbox,
    ordered newest-first.

    Query parameters:
    - ``unread=1``  — return only unread notifications (read_at is null).
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]  # noqa: RUF012
    throttle_classes = [CitizenRateThrottle]  # noqa: RUF012
    pagination_class = StandardPagination
    serializer_class = NotificationSerializer

    def get_queryset(self):  # noqa: ANN201
        qs = Notification.objects.filter(
            recipient=self.request.user,
            channel=NotificationChannel.IN_APP,
        ).order_by("-created_at")
        if self.request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return qs


class NotificationDetailView(generics.RetrieveAPIView):
    """
    GET  /api/v1/notifications/<uuid:pk>/   — retrieve a single notification.
    PATCH /api/v1/notifications/<uuid:pk>/  — update is_read (mark read/unread).

    RetrieveAPIView is used (not RetrieveUpdateAPIView) to avoid enabling a PUT
    dead-code path.  The PATCH handler is added explicitly via partial_update().

    IDOR guard: queryset is always scoped to recipient=request.user.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]  # noqa: RUF012
    throttle_classes = [CitizenRateThrottle]  # noqa: RUF012
    serializer_class = NotificationSerializer
    http_method_names = ["get", "patch", "head", "options"]  # noqa: RUF012

    def get_queryset(self):  # noqa: ANN201
        return Notification.objects.filter(
            recipient=self.request.user,
            channel=NotificationChannel.IN_APP,
        )

    def patch(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        """Route PATCH requests to partial_update (RetrieveAPIView doesn't do this)."""
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        """PATCH with ``{"is_read": true|false}`` to mark read/unread."""
        notification = self.get_object()
        serializer = MarkReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        is_read = serializer.validated_data["is_read"]
        if is_read and notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
            logger.info(
                "Notification pk=%s marked read by user pk=%s",
                notification.pk,
                request.user.pk,
            )
        elif not is_read and notification.read_at is not None:
            notification.read_at = None
            notification.save(update_fields=["read_at"])
            logger.info(
                "Notification pk=%s marked unread by user pk=%s",
                notification.pk,
                request.user.pk,
            )

        output = NotificationSerializer(notification, context={"request": request})
        return Response(output.data)


class MarkNotificationReadView(APIView):
    """
    POST /api/v1/notifications/<uuid:pk>/read/

    Idempotent convenience endpoint — sets read_at=now() regardless of
    current state.  Returns 200 with the updated notification.

    IDOR guard: notification is fetched scoped to recipient=request.user.
    """

    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]  # noqa: RUF012
    throttle_classes = [CitizenRateThrottle]  # noqa: RUF012

    def post(self, request, pk, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        try:
            notification = Notification.objects.get(
                pk=pk,
                recipient=request.user,
                channel=NotificationChannel.IN_APP,
            )
        except Notification.DoesNotExist:
            # Raise Http404 so civicos_exception_handler wraps it in the standard
            # error envelope {"error": {"code": "not_found", ...}}.
            raise Http404  # noqa: B904

        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
            logger.info(
                "Notification pk=%s marked read via /read/ by user pk=%s",
                notification.pk,
                request.user.pk,
            )

        serializer = NotificationSerializer(notification, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
