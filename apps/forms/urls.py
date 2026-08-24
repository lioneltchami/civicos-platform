"""URL patterns for the Forms building block staff views."""

from django.urls import path

from . import views

app_name = "forms"

urlpatterns = [
    # Staff submission management (all require is_staff)
    path(
        "submissions/<int:page_id>/",
        views.SubmissionListView.as_view(),
        name="submission-list",
    ),
    path(
        "submissions/<int:page_id>/<int:submission_id>/",
        views.SubmissionDetailView.as_view(),
        name="submission-detail",
    ),
    path(
        "submissions/<int:page_id>/export/",
        views.SubmissionExportView.as_view(),
        name="submission-export",
    ),
    path(
        "submissions/<int:page_id>/<int:submission_id>/redact/",
        views.SubmissionRedactView.as_view(),
        name="submission-redact",
    ),
    path(
        "submissions/<int:page_id>/<int:submission_id>/delete/",
        views.SubmissionDeleteView.as_view(),
        name="submission-delete",
    ),
]
