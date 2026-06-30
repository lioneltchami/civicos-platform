"""
Donor portal views — Wave 5.
GovStack Payments Building Block — Canadian charitable donation system.

Provides authenticated donors with:
  - Dashboard summary of giving history
  - Paginated donation history with year filtering
  - Secure receipt PDF download (via Django storage, never raw paths)
  - Recurring gift plan list and detail
  - Receipt list with year filtering

Security invariants (enforced on every view):
- LoginRequiredMixin first base class on every view class.
- All ORM queries filter by donor=request.user or equivalent FK chain — IDOR
  protection is never opt-in; it is baked into get_queryset().
- pdf_path is NEVER in any HTTP response header, URL, log line, or template.
- FileResponse.filename uses receipt.serial_number, not pdf_path.
- No donor_email, donor_name, or donor_address in any log call.
- Receipt download returns 202 when pdf_path is empty (async generation).
- Pagination via Paginator, not queryset slicing.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.payments.models import (
    PLAN_STATUS_ACTIVE,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_PAUSED,
    Donation,
    OfficialDonationReceipt,
    RecurringGiftPlan,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 25
BILLING_HISTORY_LIMIT = 25


def _get_year_filter(raw_value: str | None) -> int | None:
    """
    Validate and return a 4-digit year filter value, or None.

    Accepts only years in the range 2000–current_year to prevent
    trivially invalid inputs such as '0000' or '9999'.
    """
    if not raw_value:
        return None
    raw = raw_value.strip()
    if not (raw.isdigit() and len(raw) == 4):
        return None
    year = int(raw)
    # Use localtime so a donor in PT/MT/AT on Dec 31 sees the correct year.
    # timezone.now().year would return UTC year, excluding gifts made after
    # 16:00–21:00 PST on New Year's Eve from the current-year filter.
    current_year = localtime(timezone.now()).year
    if not (2000 <= year <= current_year):
        return None
    return year


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DonorPortalDashboardView(LoginRequiredMixin, TemplateView):
    """
    Authenticated donor landing page.

    Shows aggregated giving stats, active recurring plans, and
    the last 5 donations. All queries are scoped to request.user.
    """

    template_name = "payments/portal_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        # Aggregate total eligible amount donated — no PII in this value.
        # Combine total + count into a single DB round-trip.
        donation_stats = Donation.objects.filter(donor=user).aggregate(
            total=Sum("eligible_amount"),
            count=Count("pk"),
        )
        total_donated = donation_stats["total"] or Decimal("0.00")
        donation_count = donation_stats["count"] or 0

        # Pending receipts = receipts without a generated PDF yet.
        pending_receipt_count = OfficialDonationReceipt.objects.filter(
            donation__donor=user,
            pdf_path="",
            status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
        ).count()

        active_plans = RecurringGiftPlan.objects.filter(
            donor=user,
            status=PLAN_STATUS_ACTIVE,
        ).select_related("campaign").order_by("-created_at")

        recent_donations = (
            Donation.objects.filter(donor=user)
            .select_related("campaign")
            .prefetch_related("receipts")
            .order_by("-created_at")[:5]
        )

        ctx.update(
            {
                "total_donated": total_donated,
                "donation_count": donation_count,
                "pending_receipt_count": pending_receipt_count,
                "active_plans": active_plans,
                "recent_donations": recent_donations,
                "current_year": localtime(timezone.now()).year,
            }
        )
        return ctx


# ---------------------------------------------------------------------------
# Donation history
# ---------------------------------------------------------------------------

class DonationHistoryView(LoginRequiredMixin, ListView):
    """
    Paginated, filterable list of the authenticated donor's donations.

    Supports optional GET ?year=YYYY filtering. Available years are
    derived from the donor's own donation records.
    """

    template_name = "payments/portal_donation_history.html"
    context_object_name = "donations"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = (
            Donation.objects.filter(donor=self.request.user)
            .select_related("campaign", "payment_intent")
            .prefetch_related("receipts")
            .order_by("-created_at")
        )
        year = _get_year_filter(self.request.GET.get("year"))
        if year:
            qs = qs.filter(created_at__year=year)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Available years for the filter dropdown — derived from donor's own data.
        available_years = (
            Donation.objects.filter(donor=self.request.user)
            .dates("created_at", "year")
            .order_by("-created_at")
        )
        ctx["tax_year_filter"] = _get_year_filter(self.request.GET.get("year"))
        ctx["available_years"] = [d.year for d in available_years]
        return ctx


# ---------------------------------------------------------------------------
# Receipt download — SECURITY-CRITICAL
# ---------------------------------------------------------------------------

class ReceiptDownloadView(LoginRequiredMixin, View):
    """
    Serve a donation receipt PDF via Django's default storage.

    IDOR guard: lookup filters donation__donor=request.user so a donor
    cannot download another donor's receipt by guessing a UUID.

    pdf_path is NEVER returned in any HTTP header, URL, or log line.
    FileResponse filename is derived from receipt.serial_number only.
    """

    def get(self, request, receipt_pk):
        # IDOR guard — the FK chain donation__donor ensures only the owning
        # donor can access this receipt.  Status filter prevents serving
        # cancelled or superseded PDFs.
        try:
            receipt = OfficialDonationReceipt.objects.get(
                pk=receipt_pk,
                donation__donor=request.user,
                status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            )
        except OfficialDonationReceipt.DoesNotExist:
            raise Http404

        # PDF not yet generated — return 202 Accepted; generation is async.
        if not receipt.pdf_path:
            return HttpResponse(
                "Receipt PDF is being prepared and will be emailed to you shortly.",
                status=202,
            )

        try:
            fileobj = default_storage.open(receipt.pdf_path, "rb")
        except (FileNotFoundError, OSError):
            # Log serial number only — never pdf_path, never user identity.
            logger.error(
                "payments.portal.receipt_file_missing serial=%s",
                receipt.serial_number,
            )
            raise Http404

        # Log download event — serial number only, no PII.
        # Wrap FileResponse construction: close the handle if an exception
        # occurs after open() to prevent file-descriptor leaks.
        try:
            logger.info(
                "payments.portal.receipt_download serial=%s",
                receipt.serial_number,
            )
            # filename uses serial_number — pdf_path is NEVER in any response header.
            return FileResponse(
                fileobj,
                content_type="application/pdf",
                as_attachment=True,
                filename=f"receipt-{receipt.serial_number}.pdf",
            )
        except Exception:
            fileobj.close()
            raise


# ---------------------------------------------------------------------------
# Receipt list
# ---------------------------------------------------------------------------

class ReceiptListView(LoginRequiredMixin, ListView):
    """
    Paginated list of donation receipts for the authenticated donor.

    Supports optional GET ?year=YYYY filtering by receipt_date year.
    pdf_path is never in this view's context — only serial_number and
    the download URL (which contains only the receipt UUID).
    """

    template_name = "payments/portal_receipt_list.html"
    context_object_name = "receipts"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        # Only show issued receipts — donors should not see cancelled/superseded ones.
        qs = (
            OfficialDonationReceipt.objects.filter(
                donation__donor=self.request.user,
                status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            )
            .select_related("donation", "donation__campaign")
            .order_by("-receipt_date")
        )
        year = _get_year_filter(self.request.GET.get("year"))
        if year:
            qs = qs.filter(receipt_date__year=year)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Available years from the donor's issued receipt dates.
        available_years = (
            OfficialDonationReceipt.objects.filter(
                donation__donor=self.request.user,
                status=OfficialDonationReceipt.RECEIPT_STATUS_ISSUED,
            )
            .dates("receipt_date", "year")
            .order_by("-receipt_date")
        )
        ctx["tax_year_filter"] = _get_year_filter(self.request.GET.get("year"))
        ctx["available_years"] = [d.year for d in available_years]
        # Inform the template whether any receipts have been cancelled so a
        # banner can prompt the donor to contact the charity.
        ctx["cancelled_receipt_count"] = OfficialDonationReceipt.objects.filter(
            donation__donor=self.request.user,
            status=OfficialDonationReceipt.RECEIPT_STATUS_CANCELLED,
        ).count()
        return ctx


# ---------------------------------------------------------------------------
# Recurring gift list
# ---------------------------------------------------------------------------

class RecurringGiftListView(LoginRequiredMixin, ListView):
    """
    Full list of the authenticated donor's recurring gift plans,
    with summary counts by status.
    """

    template_name = "payments/portal_recurring_list.html"
    context_object_name = "plans"

    def get_queryset(self):
        return (
            RecurringGiftPlan.objects.filter(donor=self.request.user)
            .select_related("campaign")
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Use self.object_list (already fetched by ListView) instead of
        # re-calling get_queryset() which would hit the DB three more times.
        plans = self.object_list
        ctx["active_count"] = sum(1 for p in plans if p.status == PLAN_STATUS_ACTIVE)
        ctx["paused_count"] = sum(1 for p in plans if p.status == PLAN_STATUS_PAUSED)
        ctx["cancelled_count"] = sum(1 for p in plans if p.status == PLAN_STATUS_CANCELLED)
        return ctx


# ---------------------------------------------------------------------------
# Recurring gift detail
# ---------------------------------------------------------------------------

class RecurringGiftDetailView(LoginRequiredMixin, DetailView):
    """
    Detail view for a single recurring gift plan.

    IDOR guard: get_queryset() filters by donor=request.user so donors
    cannot view each other's plans.

    Context includes donation history linked to the plan, associated
    receipts, and a cancel URL for active/paused plans.
    """

    template_name = "payments/portal_recurring_detail.html"
    context_object_name = "plan"

    def get_queryset(self):
        # IDOR: only the owning donor can see their plan.
        return RecurringGiftPlan.objects.filter(
            donor=self.request.user
        ).select_related("campaign")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        plan = self.object

        # Donations linked to this recurring plan — capped to avoid unbounded
        # result sets for long-running plans.
        plan_donations_qs = Donation.objects.filter(
            recurring_plan=plan,
            donor=self.request.user,  # belt-and-suspenders IDOR guard
        ).select_related("payment_intent").prefetch_related("receipts").order_by("-created_at")

        plan_donations = plan_donations_qs[:BILLING_HISTORY_LIMIT]

        # Receipts across all donations on this plan — also capped.
        receipts = OfficialDonationReceipt.objects.filter(
            donation__recurring_plan=plan,
            donation__donor=self.request.user,  # belt-and-suspenders IDOR guard
        ).select_related("donation").order_by("-receipt_date")[:BILLING_HISTORY_LIMIT]

        # Inform the template when the history has been truncated.
        billing_history_truncated = (
            Donation.objects.filter(recurring_plan=plan).count() > BILLING_HISTORY_LIMIT
        )

        can_cancel = plan.status in (PLAN_STATUS_ACTIVE, PLAN_STATUS_PAUSED)
        cancel_url = reverse(
            "donate:recurring_cancel",
            kwargs={"plan_pk": plan.pk},
        )

        ctx.update(
            {
                "plan_donations": plan_donations,
                "receipts": receipts,
                "billing_history_truncated": billing_history_truncated,
                "can_cancel": can_cancel,
                "cancel_url": cancel_url,
                "PLAN_STATUS_ACTIVE": PLAN_STATUS_ACTIVE,
                "PLAN_STATUS_PAUSED": PLAN_STATUS_PAUSED,
                "PLAN_STATUS_CANCELLED": PLAN_STATUS_CANCELLED,
            }
        )
        return ctx
