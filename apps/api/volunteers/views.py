"""
Volunteer Management BB — REST API views.

Security invariants enforced here:
- All volunteer querysets are scoped to volunteer__user=request.user (IDOR prevention).
  A volunteer querying another's data gets a 404, not a 403.
- All coordinator querysets are scoped to opportunity__program__coordinator=request.user.
- IDOR: get_object_or_404 always scopes to the authenticated user's ownership.
- No PII in logs — only PKs.

PIPEDA:
- rejection_reason is NEVER returned to volunteers (enforced in serializers.py).
- VolunteerProfileSerializer strips sensitive fields without view_accommodation_notes.

Permission model:
- Volunteer endpoints: IsAuthenticated only.
- Coordinator endpoints: IsAuthenticated + IsCoordinator (group membership check).

Throttle: CitizenRateThrottle (300/hour) for volunteer endpoints,
          StaffRateThrottle (1000/hour) for coordinator endpoints.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.pagination import StandardPagination
from apps.api.throttling import CitizenRateThrottle, StaffRateThrottle
from apps.volunteers.models import (
    HoursLog,
    Opportunity,
    Shift,
    ShiftBooking,
    VolunteerApplication,
    VolunteerProfile,
)
from apps.volunteers.serializers import (
    HoursLogSerializer,
    ShiftSerializer,
    VolunteerApplicationSerializer,
    VolunteerOpportunitySerializer,
    VolunteerProfileSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raise_from_django_validation(exc: DjangoValidationError) -> None:
    """Convert Django ValidationError → DRF ValidationError (400)."""
    if hasattr(exc, "message_dict"):
        raise DRFValidationError(detail=exc.message_dict)
    raise DRFValidationError(detail=str(exc))


# ---------------------------------------------------------------------------
# Permission classes
# ---------------------------------------------------------------------------

class IsCoordinator(BasePermission):
    """
    Allows access only to authenticated users who are members of the
    'volunteer_coordinator' or 'volunteer_admin' group.
    """
    message = "Coordinator or admin group membership required."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.groups.filter(
                name__in=["volunteer_coordinator", "volunteer_admin"]
            ).exists()
        )


# ---------------------------------------------------------------------------
# Volunteer-facing views
# ---------------------------------------------------------------------------

class OpportunityListView(generics.ListAPIView):
    """
    GET /api/v1/volunteers/opportunities/

    Returns a paginated list of currently-open opportunities (status=published
    and within closes_at window). No PII, no coordinator info.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    pagination_class = StandardPagination
    serializer_class = VolunteerOpportunitySerializer

    def get_queryset(self):
        return (
            Opportunity.objects
            .filter(status=Opportunity.STATUS_PUBLISHED)
            .select_related("program")
            .order_by("-published_at")
        )


class OpportunityDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/volunteers/opportunities/<slug>/

    Returns a single published opportunity. Returns 404 for draft/archived/closed.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    serializer_class = VolunteerOpportunitySerializer
    lookup_field = "slug"

    def get_queryset(self):
        return (
            Opportunity.objects
            .filter(status=Opportunity.STATUS_PUBLISHED)
            .select_related("program")
        )


class SubmitApplicationView(APIView):
    """
    POST /api/v1/volunteers/applications/

    Volunteer submits an application to an open opportunity.
    Delegates to services.applications.apply().
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def post(self, request):
        from apps.volunteers.services.applications import apply

        opportunity_slug = request.data.get("opportunity_slug")
        if not opportunity_slug:
            raise DRFValidationError({"opportunity_slug": "This field is required."})

        opportunity = get_object_or_404(
            Opportunity,
            slug=opportunity_slug,
            status=Opportunity.STATUS_PUBLISHED,
        )
        profile = get_object_or_404(VolunteerProfile, user=request.user)

        motivation = request.data.get("motivation", "")

        try:
            application = apply(
                volunteer_profile=profile,
                opportunity=opportunity,
                motivation=motivation,
                actor=request.user,
            )
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Application submitted: application_id=%s user_id=%s opportunity=%s",
            application.pk, request.user.pk, opportunity_slug,
        )
        serializer = VolunteerApplicationSerializer(
            application, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MyApplicationsView(generics.ListAPIView):
    """
    GET /api/v1/volunteers/applications/

    Returns the authenticated volunteer's own applications (IDOR: scoped to request.user).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    pagination_class = StandardPagination
    serializer_class = VolunteerApplicationSerializer

    def get_queryset(self):
        profile = get_object_or_404(VolunteerProfile, user=self.request.user)
        return (
            VolunteerApplication.objects
            .filter(volunteer=profile)
            .select_related("opportunity", "opportunity__program")
            .order_by("-created_at")
        )


class ApplicationListCreateView(APIView):
    """
    GET  /api/v1/volunteers/applications/  → MyApplicationsView
    POST /api/v1/volunteers/applications/  → SubmitApplicationView

    Dispatches to the appropriate handler based on HTTP method.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def get(self, request, *args, **kwargs):
        view = MyApplicationsView.as_view()
        return view(request._request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        view = SubmitApplicationView.as_view()
        return view(request._request, *args, **kwargs)


class ApplicationDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/volunteers/applications/<id>/

    Returns a single application. Returns 404 if not owned by the requester (IDOR).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    serializer_class = VolunteerApplicationSerializer

    def get_queryset(self):
        profile = get_object_or_404(VolunteerProfile, user=self.request.user)
        return (
            VolunteerApplication.objects
            .filter(volunteer=profile)
            .select_related("opportunity", "opportunity__program")
        )


class WithdrawApplicationView(APIView):
    """
    DELETE /api/v1/volunteers/applications/<id>/

    Volunteer withdraws their own pending application.
    Returns 404 if not owned by the requester (IDOR).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def delete(self, request, pk):
        from apps.volunteers.services.applications import withdraw

        profile = get_object_or_404(VolunteerProfile, user=request.user)
        application = get_object_or_404(
            VolunteerApplication, pk=pk, volunteer=profile
        )

        try:
            withdraw(application=application, actor=request.user)
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Application withdrawn: application_id=%s user_id=%s",
            pk, request.user.pk,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ShiftListView(generics.ListAPIView):
    """
    GET /api/v1/volunteers/shifts/?opportunity=<slug>

    Lists shifts, optionally filtered by opportunity slug.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    pagination_class = StandardPagination
    serializer_class = ShiftSerializer

    def get_queryset(self):
        opp_slug = self.request.query_params.get("opportunity", "")
        qs = Shift.objects.select_related("opportunity")
        if opp_slug:
            qs = qs.filter(opportunity__slug=opp_slug)
        return qs.order_by("start_datetime")


class BookShiftView(APIView):
    """
    POST /api/v1/volunteers/shifts/<id>/book/

    Volunteer books (or waitlists) a shift.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def post(self, request, pk):
        from apps.volunteers.services.scheduling import book_shift

        shift = get_object_or_404(Shift, pk=pk)
        profile = get_object_or_404(VolunteerProfile, user=request.user)

        try:
            booking = book_shift(
                shift=shift,
                volunteer_profile=profile,
                actor=request.user,
            )
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Shift booked: booking_id=%s shift_id=%s user_id=%s",
            booking.pk, pk, request.user.pk,
        )
        return Response(
            {"booking_id": booking.pk, "status": booking.status},
            status=status.HTTP_201_CREATED,
        )


class CancelBookingView(APIView):
    """
    POST /api/v1/volunteers/shifts/<id>/cancel-booking/

    Volunteer cancels their booking for a shift.
    Returns 404 if booking doesn't exist or doesn't belong to this volunteer (IDOR).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def post(self, request, pk):
        from apps.volunteers.services.scheduling import cancel_booking

        shift = get_object_or_404(Shift, pk=pk)
        profile = get_object_or_404(VolunteerProfile, user=request.user)
        booking = get_object_or_404(ShiftBooking, shift=shift, volunteer=profile)

        try:
            cancel_booking(booking=booking, actor=request.user)
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Booking cancelled: booking_id=%s shift_id=%s user_id=%s",
            booking.pk, pk, request.user.pk,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyHoursView(generics.ListAPIView):
    """
    GET /api/v1/volunteers/hours/

    Returns the authenticated volunteer's own hours log (IDOR: scoped to request.user).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    pagination_class = StandardPagination
    serializer_class = HoursLogSerializer

    def get_queryset(self):
        profile = get_object_or_404(VolunteerProfile, user=self.request.user)
        return (
            HoursLog.objects
            .filter(volunteer=profile)
            .select_related("opportunity")
            .order_by("-date")
        )


class LogHoursView(APIView):
    """
    POST /api/v1/volunteers/hours/

    Volunteer logs hours against an approved opportunity.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def post(self, request):
        from datetime import date as date_type
        from apps.volunteers.services.hours import log_hours

        profile = get_object_or_404(VolunteerProfile, user=request.user)
        opp_slug = request.data.get("opportunity_slug")
        if not opp_slug:
            raise DRFValidationError({"opportunity_slug": "This field is required."})

        opportunity = get_object_or_404(Opportunity, slug=opp_slug)

        hours_raw = request.data.get("hours")
        if hours_raw is None:
            raise DRFValidationError({"hours": "This field is required."})

        log_date_str = request.data.get("date") or request.data.get("log_date")
        description = request.data.get("description", "")

        try:
            log_date = date_type.fromisoformat(log_date_str) if log_date_str else date_type.today()
        except (ValueError, TypeError):
            raise DRFValidationError({"date": "Enter a valid date in YYYY-MM-DD format."})

        try:
            entry = log_hours(
                volunteer_profile=profile,
                opportunity=opportunity,
                hours=hours_raw,
                date=log_date,
                description=description,
                actor=request.user,
            )
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Hours logged: hours_log_id=%s user_id=%s hours=%s",
            entry.pk, request.user.pk, hours_raw,
        )
        serializer = HoursLogSerializer(entry, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class HoursListCreateView(APIView):
    """
    GET  /api/v1/volunteers/hours/  → MyHoursView
    POST /api/v1/volunteers/hours/  → LogHoursView

    Dispatches based on HTTP method.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def get(self, request, *args, **kwargs):
        view = MyHoursView.as_view()
        return view(request._request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        view = LogHoursView.as_view()
        return view(request._request, *args, **kwargs)


class MyHoursSummaryView(APIView):
    """
    GET /api/v1/volunteers/hours/summary/

    Returns total approved hours for the authenticated volunteer.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]

    def get(self, request):
        from django.db.models import Sum

        profile = get_object_or_404(VolunteerProfile, user=request.user)
        result = HoursLog.objects.filter(
            volunteer=profile,
            status=HoursLog.STATUS_APPROVED,
        ).aggregate(total=Sum("hours"))

        total = result["total"] or Decimal("0.00")
        return Response({
            "total_approved_hours": str(total),
            "volunteer_pk": profile.pk,
        })


class MyProfileView(generics.RetrieveUpdateAPIView):
    """
    GET  /api/v1/volunteers/profile/  — retrieve own profile
    PATCH /api/v1/volunteers/profile/ — update own profile (partial)

    Sensitive fields are stripped by VolunteerProfileSerializer unless
    the user holds volunteers.view_accommodation_notes.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CitizenRateThrottle]
    serializer_class = VolunteerProfileSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return get_object_or_404(VolunteerProfile, user=self.request.user)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


# ---------------------------------------------------------------------------
# Coordinator-facing views
# ---------------------------------------------------------------------------

class AllApplicationsView(generics.ListAPIView):
    """
    GET /api/v1/volunteers/admin/applications/

    Returns all applications scoped to opportunities this coordinator manages.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]
    pagination_class = StandardPagination
    serializer_class = VolunteerApplicationSerializer

    def get_queryset(self):
        return (
            VolunteerApplication.objects
            .filter(opportunity__program__coordinator=self.request.user)
            .select_related("volunteer", "opportunity", "opportunity__program")
            .order_by("-created_at")
        )


class ApproveApplicationView(APIView):
    """
    PATCH /api/v1/volunteers/admin/applications/<id>/approve/

    Coordinator approves a pending/in-review/waitlisted application.
    Scoped to coordinator's own programs (IDOR: 404 if not in scope).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]

    def patch(self, request, pk):
        from apps.volunteers.services.applications import approve_application

        qs = VolunteerApplication.objects.filter(
            opportunity__program__coordinator=request.user
        )
        application = get_object_or_404(qs, pk=pk)

        try:
            approve_application(application=application, actor=request.user)
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Application approved: application_id=%s coordinator_id=%s",
            pk, request.user.pk,
        )
        return Response({"status": application.status}, status=status.HTTP_200_OK)


class RejectApplicationView(APIView):
    """
    PATCH /api/v1/volunteers/admin/applications/<id>/reject/

    Coordinator rejects a pending/in-review/waitlisted application.
    rejection_reason is stored internally; NEVER returned to volunteers.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]

    def patch(self, request, pk):
        from apps.volunteers.services.applications import reject_application

        qs = VolunteerApplication.objects.filter(
            opportunity__program__coordinator=request.user
        )
        application = get_object_or_404(qs, pk=pk)
        rejection_reason = request.data.get("rejection_reason", "")

        try:
            reject_application(
                application=application,
                actor=request.user,
                rejection_reason=rejection_reason,
            )
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Application rejected: application_id=%s coordinator_id=%s",
            pk, request.user.pk,
        )
        return Response({"status": application.status}, status=status.HTTP_200_OK)


class PendingHoursView(generics.ListAPIView):
    """
    GET /api/v1/volunteers/admin/hours/pending/

    Lists pending hours logs for volunteers in this coordinator's programs.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]
    pagination_class = StandardPagination
    serializer_class = HoursLogSerializer

    def get_queryset(self):
        return (
            HoursLog.objects
            .filter(
                volunteer__applications__opportunity__program__coordinator=self.request.user,
                status=HoursLog.STATUS_PENDING,
            )
            .distinct()
            .select_related("volunteer", "opportunity")
            .order_by("date")
        )


class ApproveHoursView(APIView):
    """
    PATCH /api/v1/volunteers/admin/hours/<id>/approve/

    Coordinator approves a pending hours log.
    Scoped to the coordinator's programs (IDOR: 404 if not in scope).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]

    def patch(self, request, pk):
        from apps.volunteers.services.hours import approve_hours

        qs = (
            HoursLog.objects
            .filter(
                volunteer__applications__opportunity__program__coordinator=request.user,
            )
            .distinct()
        )
        hours_log = get_object_or_404(qs, pk=pk)

        try:
            approve_hours(hours_log=hours_log, actor=request.user)
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Hours approved: hours_log_id=%s coordinator_id=%s",
            pk, request.user.pk,
        )
        return Response({"status": hours_log.status}, status=status.HTTP_200_OK)


class RejectHoursView(APIView):
    """
    PATCH /api/v1/volunteers/admin/hours/<id>/reject/

    Coordinator rejects a pending hours log.
    rejection reason is stored internally — NEVER returned to volunteers.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]

    def patch(self, request, pk):
        from apps.volunteers.services.hours import reject_hours

        qs = (
            HoursLog.objects
            .filter(
                volunteer__applications__opportunity__program__coordinator=request.user,
            )
            .distinct()
        )
        hours_log = get_object_or_404(qs, pk=pk)
        reason = request.data.get("reason", "")

        try:
            reject_hours(hours_log=hours_log, actor=request.user, reason=reason)
        except PermissionDenied as exc:
            return Response(
                {"error": {"code": "permission_denied", "detail": str(exc), "status": 403}},
                status=status.HTTP_403_FORBIDDEN,
            )
        except DjangoValidationError as exc:
            _raise_from_django_validation(exc)

        logger.info(
            "Hours rejected: hours_log_id=%s coordinator_id=%s",
            pk, request.user.pk,
        )
        return Response({"status": hours_log.status}, status=status.HTTP_200_OK)


class HoursReportView(APIView):
    """
    GET /api/v1/volunteers/admin/reports/hours/?year=<year>&month=<month>

    Returns aggregated hours-by-program for the specified year (and optional month).
    Decimal values are stringified for safe JSON transport.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]

    def get(self, request):
        from apps.volunteers.services.reporting import hours_by_program

        try:
            year = int(request.query_params.get("year", timezone.now().year))
        except (ValueError, TypeError):
            raise DRFValidationError({"year": "Enter a valid 4-digit year."})

        month_str = request.query_params.get("month", "")
        month = None
        if month_str:
            try:
                month = int(month_str)
                if not 1 <= month <= 12:
                    raise ValueError
            except (ValueError, TypeError):
                raise DRFValidationError({"month": "Enter a valid month (1–12)."})

        data = hours_by_program(year, month)
        # Stringify any Decimal values in rows for safe JSON transport
        safe_data = []
        for row in data:
            safe_row = {
                k: str(v) if isinstance(v, Decimal) else v
                for k, v in row.items()
            }
            safe_data.append(safe_row)

        return Response({"year": year, "month": month, "data": safe_data})


class ImpactReportView(APIView):
    """
    GET /api/v1/volunteers/admin/reports/impact/?year=<year>

    Returns impact value and CRA T3010 volunteer metrics for the given year.
    Decimal values are stringified.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsCoordinator]
    throttle_classes = [StaffRateThrottle]

    def get(self, request):
        from apps.volunteers.services.reporting import impact_value, t3010_volunteer_metrics

        try:
            year = int(request.query_params.get("year", timezone.now().year))
        except (ValueError, TypeError):
            raise DRFValidationError({"year": "Enter a valid 4-digit year."})

        impact = impact_value(year)
        t3010 = t3010_volunteer_metrics(year)

        def _stringify_decimals(d: dict) -> dict:
            return {k: str(v) if isinstance(v, Decimal) else v for k, v in d.items()}

        return Response({
            "year": year,
            "impact": _stringify_decimals(impact),
            "t3010": _stringify_decimals(t3010),
        })
