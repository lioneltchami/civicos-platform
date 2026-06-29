"""URL patterns for the auth_extension building block."""
from django.urls import path

from .views import (
    AccountDashboardView,
    ChangeLanguageView,
    GenerateBackupCodesView,
    GuestSessionView,
    MFAStatusView,
    ProfileUpdateView,
)

app_name = "auth_extension"

urlpatterns = [
    path("dashboard/", AccountDashboardView.as_view(), name="dashboard"),
    path("profile/", ProfileUpdateView.as_view(), name="profile_edit"),
    path("language/", ChangeLanguageView.as_view(), name="change_language"),
    path("mfa/", MFAStatusView.as_view(), name="mfa_status"),
    path(
        "mfa/backup-codes/generate/",
        GenerateBackupCodesView.as_view(),
        name="generate_backup_codes",
    ),
    path("guest/", GuestSessionView.as_view(), name="guest_session"),
]
