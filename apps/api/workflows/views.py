"""
Workflow queue and action API views.

Security contract:
- ALL views require IsStaff — citizens are NEVER permitted access.
- WorkItems are not scoped to the requesting user; all staff see all items.
  Filtering by "mine" is an optional convenience, not a security boundary.
- All mutations delegate to apps.workflows.services — views never call
  model.save() directly.
- ValueError from the service layer → 400 Bad Request.
- PermissionError from the service layer → 403 Forbidden.
- No PII is written to logs — only PKs.
"""

import logging

from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.authentication import GovstackTokenAuthentication
from apps.api.pagination import StandardPagination
from apps.api.permissions import IsStaff
from apps.api.throttling import StaffRateThrottle
from apps.api.workflows.serializers import (
    AddCommentSerializer,
    AdvanceStatusSerializer,
    AssignWorkItemSerializer,
    ClaimWorkItemSerializer,
    EscalateWorkItemSerializer,
    WorkItemCommentSerializer,
    WorkItemDetailSerializer,
    WorkItemSerializer,
)
from apps.workflows.models import WorkItem
from apps.workflows.services import (
    add_comment,
    assign_work_item,
    claim_work_item,
    escalate_work_item,
    get_staff_queue,
    update_work_item_status,
)

logger = logging.getLogger(__name__)

# Shared constants — avoids repeating the same lists on every class.
_AUTH = [GovstackTokenAuthentication, JWTAuthentication]
_STAFF_PERMS = [IsStaff]
_STAFF_THROTTLE = [StaffRateThrottle]


# ---------------------------------------------------------------------------
# Queue / list view
# ---------------------------------------------------------------------------

class WorkItemQueueView(generics.ListAPIView):
    """
    GET /api/v1/workflows/queue/

    Returns the filtered staff work-item queue (excludes terminal statuses
    by default — see get_staff_queue in services.py).

    Query parameters:
    - ``status``      — filter by WorkItemStatus value (pending, in_progress, waiting)
    - ``mine=1``      — return only items assigned to the requesting user
    - ``unassigned=1`` — return only unassigned items (mine and unassigned are mutually exclusive)
    - ``priority``    — filter by priority integer (1=Critical … 4=Low)
    """

    authentication_classes = _AUTH
    permission_classes = _STAFF_PERMS
    throttle_classes = _STAFF_THROTTLE
    pagination_class = StandardPagination
    serializer_class = WorkItemSerializer

    def get_queryset(self):
        params = self.request.query_params

        status_filter: str | None = params.get("status") or None
        assigned_to_me: bool = params.get("mine") == "1"
        unassigned_only: bool = params.get("unassigned") == "1"

        priority_raw = params.get("priority")
        priority_filter: int | None = None
        if priority_raw is not None:
            try:
                priority_filter = int(priority_raw)
            except (TypeError, ValueError):
                # Invalid value — ignore the filter rather than raising a 400 here;
                # service layer will simply not apply it.
                priority_filter = None

        return get_staff_queue(
            self.request.user,
            status_filter=status_filter,
            assigned_to_me=assigned_to_me,
            unassigned_only=unassigned_only,
            priority_filter=priority_filter,
        )


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------

class WorkItemDetailAPIView(generics.RetrieveAPIView):
    """
    GET /api/v1/workflows/<uuid:pk>/

    Returns full detail of a single WorkItem including audit history and comments.
    Prefetches related objects to avoid N+1 queries.
    """

    authentication_classes = _AUTH
    permission_classes = _STAFF_PERMS
    throttle_classes = _STAFF_THROTTLE
    serializer_class = WorkItemDetailSerializer

    def get_queryset(self):
        return WorkItem.objects.prefetch_related(
            "history__actor",
            "comments__author",
        ).select_related("assigned_to", "content_type")


# ---------------------------------------------------------------------------
# Action views — all operate on a specific WorkItem identified by <uuid:pk>
# ---------------------------------------------------------------------------

class _WorkItemActionView(APIView):
    """
    Base class for single-WorkItem action views.

    Subclasses must implement ``perform_action(work_item, request)``.
    All responses follow the same envelope shape.
    """

    authentication_classes = _AUTH
    permission_classes = _STAFF_PERMS
    throttle_classes = _STAFF_THROTTLE

    def _get_work_item(self, pk):
        return get_object_or_404(WorkItem, pk=pk)

    def _ok(self, work_item, request, status_code=status.HTTP_200_OK):
        serializer = WorkItemSerializer(work_item, context={"request": request})
        return Response(serializer.data, status=status_code)

    def _service_error(self, exc: Exception) -> Response:
        """Translate service-layer exceptions to HTTP responses."""
        if isinstance(exc, PermissionError):
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ValueError — invalid state transition, business rule violation, etc.
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )


class ClaimWorkItemAPIView(_WorkItemActionView):
    """
    POST /api/v1/workflows/<uuid:pk>/claim/

    Staff member self-assigns an unassigned WorkItem and advances it to IN_PROGRESS.
    No request body required.
    """

    def post(self, request, pk, *args, **kwargs):
        work_item = self._get_work_item(pk)
        # Validate (no-op serializer — kept for consistency and future extensibility)
        ClaimWorkItemSerializer(data=request.data).is_valid(raise_exception=True)
        try:
            work_item = claim_work_item(work_item, request.user)
        except (ValueError, PermissionError) as exc:
            logger.info(
                "ClaimWorkItem failed for work_item pk=%s by user pk=%s: %s",
                pk,
                request.user.pk,
                type(exc).__name__,
            )
            return self._service_error(exc)
        return self._ok(work_item, request)


class AssignWorkItemAPIView(_WorkItemActionView):
    """
    POST /api/v1/workflows/<uuid:pk>/assign/

    Supervisor assigns a WorkItem to a specific staff member.
    Request body: ``{"assignee_id": "<uuid>"}``
    """

    def post(self, request, pk, *args, **kwargs):
        work_item = self._get_work_item(pk)
        serializer = AssignWorkItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignee = serializer.validated_data["assignee"]
        try:
            work_item = assign_work_item(work_item, assignee, request.user)
        except (ValueError, PermissionError) as exc:
            logger.info(
                "AssignWorkItem failed for work_item pk=%s by user pk=%s: %s",
                pk,
                request.user.pk,
                type(exc).__name__,
            )
            return self._service_error(exc)
        return self._ok(work_item, request)


class AdvanceStatusAPIView(_WorkItemActionView):
    """
    POST /api/v1/workflows/<uuid:pk>/status/

    Advance (or regress) a WorkItem through the state machine.
    Request body: ``{"new_status": "<value>", "notes": "<optional>"}``
    """

    def post(self, request, pk, *args, **kwargs):
        work_item = self._get_work_item(pk)
        serializer = AdvanceStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["new_status"]
        notes = serializer.validated_data.get("notes", "")
        try:
            work_item = update_work_item_status(
                work_item, new_status, request.user, notes=notes
            )
        except (ValueError, PermissionError) as exc:
            logger.info(
                "AdvanceStatus failed for work_item pk=%s by user pk=%s: %s",
                pk,
                request.user.pk,
                type(exc).__name__,
            )
            return self._service_error(exc)
        return self._ok(work_item, request)


class EscalateWorkItemAPIView(_WorkItemActionView):
    """
    POST /api/v1/workflows/<uuid:pk>/escalate/

    Escalate a WorkItem to the next level (max level 2).
    Request body: ``{"reason": "<optional>"}``
    """

    def post(self, request, pk, *args, **kwargs):
        work_item = self._get_work_item(pk)
        serializer = EscalateWorkItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        try:
            work_item = escalate_work_item(work_item, request.user, reason=reason)
        except (ValueError, PermissionError) as exc:
            logger.info(
                "EscalateWorkItem failed for work_item pk=%s by user pk=%s: %s",
                pk,
                request.user.pk,
                type(exc).__name__,
            )
            return self._service_error(exc)
        return self._ok(work_item, request)


class AddCommentAPIView(_WorkItemActionView):
    """
    POST /api/v1/workflows/<uuid:pk>/comment/

    Add an internal staff comment to a WorkItem.
    Request body: ``{"body": "<comment text>"}``
    Returns 201 Created with the new comment.
    """

    def post(self, request, pk, *args, **kwargs):
        work_item = self._get_work_item(pk)
        serializer = AddCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data["body"]
        try:
            comment = add_comment(work_item, request.user, body)
        except (ValueError, PermissionError) as exc:
            logger.info(
                "AddComment failed for work_item pk=%s by user pk=%s: %s",
                pk,
                request.user.pk,
                type(exc).__name__,
            )
            return self._service_error(exc)
        output = WorkItemCommentSerializer(comment, context={"request": request})
        return Response(output.data, status=status.HTTP_201_CREATED)
