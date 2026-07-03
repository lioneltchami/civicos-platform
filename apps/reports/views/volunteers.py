"""
Volunteer impact reports — Reports BB view layer. Staff-only.

Views:
  VolunteerImpactDashboardView — monthly programme-level volunteer summary.
    Current month: live queries via get_monthly_volunteer_summary + impact_value.
    Prior months:  served from ReportSnapshot (pre-computed by Celery Beat);
                   falls back to live query if no snapshot exists.

All views require staff login + explicit permission (reports.view_reportsnapshot).
PIPEDA: no volunteer names, emails, or addresses appear in any template context.
"""
from __future__ import annotations

import calendar
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView

from apps.reports.services.volunteers import get_monthly_volunteer_summary

logger = logging.getLogger("apps.reports.views.volunteers")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_toronto_ym() -> tuple[int, int]:
    """Return (year, month) in America/Toronto local time."""
    now_local = timezone.localtime(timezone.now())
    return now_local.year, now_local.month


def _parse_year_month(request) -> tuple[int, int] | None:
    """Parse ?year=YYYY&month=M from GET params. Returns (year, month) or None."""
    try:
        year = int(request.GET["year"])
        month = int(request.GET["month"])
        if not (2000 <= year <= 2100) or not (1 <= month <= 12):
            return None
        return year, month
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

class VolunteerImpactDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Monthly volunteer impact dashboard.

    Current month: real-time queries via get_monthly_volunteer_summary and impact_value.
    Prior months:  served from ReportSnapshot (pre-computed by Celery Beat);
                   falls back to real-time if no snapshot exists.

    Permission: reports.view_reportsnapshot
    Template:   reports/volunteers/dashboard.html
    PIPEDA:     programme-level aggregates only — no volunteer PII in context.
    """

    permission_required = "reports.view_reportsnapshot"
    raise_exception = True  # 403 for authenticated users without permission
    template_name = "reports/volunteers/dashboard.html"

    def handle_no_permission(self):
        """Redirect unauthenticated users to login; raise 403 for authenticated users."""
        from django.conf import settings
        from django.contrib.auth.views import redirect_to_login
        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(),
                settings.LOGIN_URL,
            )
        return super().handle_no_permission()

    def get_context_data(self, **kwargs):
        from decimal import Decimal as _Dec
        from apps.reports.models import ReportSnapshot

        ctx = super().get_context_data(**kwargs)

        current_year, current_month = _current_toronto_ym()
        ym = _parse_year_month(self.request)
        year, month = ym if ym else (current_year, current_month)

        is_current_month = (year == current_year and month == current_month)

        data_source = "live"
        volunteer_summary = None
        impact = None

        # ── Snapshot for prior months ─────────────────────────────────────────
        if not is_current_month:
            try:
                snapshot = ReportSnapshot.objects.get(
                    report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
                    period_year=year,
                    period_month=month,
                )

                def _d(v):
                    try:
                        return _Dec(str(v))
                    except Exception:
                        return _Dec("0.00")

                snap_hours = snapshot.data.get("hours", {})
                snap_impact = snapshot.data.get("impact", {})

                raw_programs = snap_hours.get("by_program", [])
                by_program = [
                    {
                        "program_slug": row.get("program_slug", ""),
                        "program_title_en": row.get("program_title_en", ""),
                        "opportunity_slug": row.get("opportunity_slug", ""),
                        "opportunity_title_en": row.get("opportunity_title_en", ""),
                        "approved_hours": _d(row.get("approved_hours", "0")),
                        "volunteer_count": row.get("volunteer_count", 0),
                        "shift_count": row.get("shift_count", 0),
                    }
                    for row in raw_programs
                ]

                volunteer_summary = {
                    "total_approved_hours": _d(snap_hours.get("total_approved_hours", "0")),
                    "volunteer_count": snap_hours.get("volunteer_count", 0),
                    "opportunity_count": snap_hours.get("opportunity_count", 0),
                    "program_count": snap_hours.get("program_count", 0),
                    "by_program": by_program,
                }
                impact = {
                    "total_approved_hours": _d(snap_impact.get("total_approved_hours", "0")),
                    "estimated_value_cad": _d(snap_impact.get("estimated_value_cad", "0")),
                    "volunteer_count": snap_impact.get("volunteer_count", 0),
                    "hourly_rate": _d(snap_impact.get("hourly_rate", "0")),
                    "province": snap_impact.get("province", "ON"),
                }
                data_source = "snapshot"
            except ReportSnapshot.DoesNotExist:
                pass  # fall through to live query

        # ── Live query ────────────────────────────────────────────────────────
        if volunteer_summary is None:
            volunteer_summary = get_monthly_volunteer_summary(year, month)

        if impact is None:
            from apps.volunteers.services.reporting import impact_value
            impact = impact_value(year)

        # Merge impact into volunteer_summary for template convenience
        volunteer_summary["estimated_value_cad"] = impact.get(
            "estimated_value_cad", _Dec("0.00")
        )
        volunteer_summary["hourly_rate"] = impact.get("hourly_rate", _Dec("0.00"))
        volunteer_summary["province"] = impact.get("province", "ON")

        # ── Recent snapshots sidebar ──────────────────────────────────────────
        recent_snapshots = (
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_VOLUNTEERS,
            )
            .order_by("-period_year", "-period_month")[:12]
        )

        month_label = f"{calendar.month_name[month]} {year}"

        logger.info(
            "reports.views.volunteers.dashboard user_pk=%s year=%s month=%s source=%s",
            self.request.user.pk, year, month, data_source,
        )

        ctx.update({
            "year": year,
            "month": month,
            "month_label": month_label,
            "is_current_month": is_current_month,
            "data_source": data_source,
            "volunteer_summary": volunteer_summary,
            "recent_snapshots": recent_snapshots,
        })
        return ctx
