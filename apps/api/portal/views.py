"""
API views for the Portal building block.

Security invariants enforced here:
- All querysets are scoped to request.user — IDOR is impossible because
  a citizen can only ever see/cancel their own service requests.
- cancel_service_request() in the service layer does a second ownership
  check (citizen_id == citizen.pk), providing defence in depth.
- ValueError from the service layer → 400; PermissionError → 403.
- No PII is written to logs — only PKs and reference numbers.
"""

import logging

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.authentication import CivicOSTokenAuthentication
from apps.api.pagination import StandardPagination
from apps.api.throttling import CitizenRateThrottle
from apps.portal.models import ServiceRequest
from apps.portal.services import (
    cancel_service_request,
    create_service_request,
    get_citizen_requests,
)

from .serializers import (
    CancelServiceRequestSerializer,
    CreateServiceRequestSerializer,
    ServiceRequestDetailSerializer,
    ServiceRequestSerializer,
)

logger = logging.getLogger(__name__)


class ServiceRequestListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/portal/requests/       — Paginated list of the citizen's requests.
    POST /api/v1/portal/requests/       — Submit a new service request.

    The list is already scoped to the requesting citizen by the service layer
    (get_citizen_requests), so no additional filtering is required here.
    """

    authentication_classes = [CivicOSTokenAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    throttle_classes = [CitizenRateThrottle]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return CreateServiceRequestSerializer
        return ServiceRequestSerializer

    def get_queryset(self):
        """
        Return requests scoped to the authenticated citizen.
        Supports optional ?status= query parameter (handled by service layer).
        """
        status_filter = self.request.query_params.get("status")
        return get_citizen_requests(self.request.user, status_filter=status_filter)

    def create(self, request, *args, **kwargs):
        """
        Validate the payload, delegate creation to the service layer, and
        return the full detail representation with HTTP 201.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data
        try:
            service_request = create_service_request(
                citizen=request.user,
                service_name=validated["service_name"],
                submission_data=validated["submission_data"],
            )
        except ValueError as exc:
            logger.warning(
                "create_service_request failed for user_id=%s: %s",
                request.user.pk,
                exc,
            )
            return Response(
                {"error": {"code": "validation_error", "message": str(exc), "details": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            "Service request created: reference=%s user_id=%s",
            service_request.reference_number,
            request.user.pk,
        )
        output = ServiceRequestDetailSerializer(
            service_request, context=self.get_serializer_context()
        )
        return Response(output.data, status=status.HTTP_201_CREATED)


class ServiceRequestDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/portal/requests/<reference_number>/

    Returns the full detail (including submission_data) for a single request.
    The queryset is scoped to request.user to prevent IDOR.
    """

    authentication_classes = [CivicOSTokenAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    serializer_class = ServiceRequestDetailSerializer
    lookup_field = "reference_number"

    def get_queryset(self):
        """
        Restrict to the authenticated citizen's own requests.
        A lookup against a reference_number that belongs to another citizen
        will naturally result in a 404 rather than a 403, preventing
        enumeration of valid reference numbers.
        """
        # Filter by citizen gives IDOR protection; no select_related needed because
        # no serializer field traverses the citizen FK from the detail response.
        return ServiceRequest.objects.filter(citizen=self.request.user)


class CancelServiceRequestView(APIView):
    """
    POST /api/v1/portal/requests/<reference_number>/cancel/

    Citizen-initiated cancellation. The service layer enforces ownership and
    business rules (e.g. terminal state cannot be cancelled).

    Response codes:
        200 — cancelled successfully
        400 — request cannot be cancelled (e.g. already approved)
        403 — caller does not own this request (defence-in-depth check)
        404 — reference number not found or belongs to another citizen
    """

    authentication_classes = [CivicOSTokenAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def post(self, request, reference_number: str):
        # Scope lookup to the authenticated citizen — prevents IDOR and
        # returns 404 (not 403) for requests belonging to other citizens,
        # avoiding disclosure of valid reference numbers.
        try:
            service_request = ServiceRequest.objects.get(
                reference_number=reference_number,
                citizen=request.user,
            )
        except ServiceRequest.DoesNotExist:
            return Response(
                {"error": {"code": "not_found", "message": "Service request not found.", "details": {}}},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CancelServiceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        try:
            cancel_service_request(service_request, request.user, reason=reason)
        except PermissionError as exc:
            logger.warning(
                "Cancel permission denied: reference=%s user_id=%s",
                reference_number,
                request.user.pk,
            )
            return Response(
                {"error": {"code": "permission_denied", "message": str(exc), "details": {}}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except ValueError as exc:
            logger.info(
                "Cancel rejected (business rule): reference=%s user_id=%s exc=%s",
                reference_number,
                request.user.pk,
                type(exc).__name__,
            )
            return Response(
                {"error": {"code": "validation_error", "message": str(exc), "details": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            "Service request cancelled: reference=%s user_id=%s",
            reference_number,
            request.user.pk,
        )
        return Response({"detail": "Request cancelled."}, status=status.HTTP_200_OK)
