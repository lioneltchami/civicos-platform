"""
Combined non-profit impact report view — Reports BB view layer. Staff-only.

Views:
  CombinedImpactView — annual combined volunteer + donation impact summary
    for CRA T3010 Schedule 2 (volunteers) and line 4500 (eligible donations).

Requires staff login + reports.view_reportsnapshot permission.
PIPEDA: no volunteer or donor PII — economic aggregates only.
"""

from __future__ import annotations

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.views.generic import TemplateView

from apps.reports.services.combined import combined_nonprofit_impact

logger = logging.getLogger("apps.reports.views.combined")


def _current_toronto_year() -> int:
    """Return the current year in America/Toronto local time."""
    return timezone.localtime(timezone.now()).year


def _parse_year(request) -> int | None:  # noqa: ANN001
    """Parse ?year=YYYY from GET params. Returns int or None on error."""
    try:
        year = int(request.GET["year"])
        if not (2000 <= year <= 2100):
            return None
        return year
    except (KeyError, ValueError, TypeError):
        return None


class CombinedImpactView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Annual combined non-profit impact dashboard.

    Shows volunteer economic value alongside eligible donation totals for a
    chosen calendar year. Provides data for CRA T3010 Schedule 2 and line 4500.

    Permission: reports.view_reportsnapshot
    Template:   reports/combined/impact.html
    PIPEDA:     no volunteer or donor PII in any context value.
    """

    permission_required = "reports.view_reportsnapshot"
    raise_exception = True  # 403 for authenticated users without permission
    template_name = "reports/combined/impact.html"

    def dispatch(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        # H6: is_staff guard — PermissionRequiredMixin only checks the explicit permission;
        # a staff admin could grant reports.view_reportsnapshot to a non-staff user.
        if request.user.is_authenticated and not request.user.is_staff:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def handle_no_permission(self):  # noqa: ANN201
        """Redirect unauthenticated users to login; raise 403 for authenticated users."""
        from django.conf import settings
        from django.contrib.auth.views import redirect_to_login

        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(),
                settings.LOGIN_URL,
            )
        return super().handle_no_permission()

    def get_context_data(self, **kwargs):  # noqa: ANN003, ANN201
        ctx = super().get_context_data(**kwargs)

        year = _parse_year(self.request) or _current_toronto_year()
        impact = combined_nonprofit_impact(year)

        logger.info(
            "reports.views.combined.impact user_pk=%s year=%s combined_value_cad=%s",
            self.request.user.pk,
            year,
            impact.get("combined_value_cad"),
        )

        ctx.update(
            {
                "year": year,
                "impact": impact,
            }
        )
        return ctx
