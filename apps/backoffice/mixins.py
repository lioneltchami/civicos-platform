"""
Back-office access control mixins.

Provides StaffRequiredMixin for all back-office views and a helper for
logging staff actions without leaking PII.

Security notes:
- Unauthenticated requests are redirected to the login page.
- Authenticated non-staff users receive HTTP 403 Forbidden.
- Log messages use pk only — never email or other PII.
"""

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

logger = logging.getLogger(__name__)


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Mixin that restricts a view to authenticated staff users only.

    Behaviour:
    - Unauthenticated → redirect to login (standard Django redirect_to_login).
    - Authenticated + not staff → HTTP 403 PermissionDenied.
    - Authenticated + is_staff → view proceeds normally.
    """

    raise_exception = True

    def test_func(self) -> bool:
        """Return True only for authenticated staff members."""
        return self.request.user.is_authenticated and self.request.user.is_staff

    def handle_no_permission(self):  # noqa: ANN201
        """
        Differentiate between unauthenticated and unauthorised requests.

        Unauthenticated users are redirected to the login page so they can
        authenticate and return.  Authenticated non-staff users receive a
        hard 403 — they are in the wrong part of the application.
        """
        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(),
                self.get_login_url(),
                self.get_redirect_field_name(),
            )
        # Authenticated but not staff — deny access outright
        raise PermissionDenied


def log_staff_action(request, message: str) -> None:  # noqa: ANN001
    """
    Log a staff action at INFO level.

    Uses only the staff member's primary key — never their email or any other
    PII — to comply with the project's no-PII-in-logs policy.

    Args:
        request: The current HTTP request.
        message: A brief description of the action performed.
    """
    logger.info("backoffice: %s by staff pk=%s", message, request.user.pk)
