app_name = "consent"

"""
URL configuration for the Consent & Privacy API.

Mounted at /api/v1/consent/ by apps.api.urls.
"""
from django.urls import path

from . import api_views

urlpatterns = [
    path(
        "categories/",
        api_views.ConsentCategoryListView.as_view(),
        name="api-category-list",
    ),
    path(
        "records/",
        api_views.ConsentRecordListView.as_view(),
        name="api-record-list",
    ),
    path(
        "records/<slug:category_slug>/",
        api_views.ConsentRecordUpdateView.as_view(),
        name="api-record-update",
    ),
    path(
        "export-requests/",
        api_views.DataExportRequestListCreateView.as_view(),
        name="api-export-list-create",
    ),
    path(
        "export-requests/<uuid:pk>/",
        api_views.DataExportRequestDetailView.as_view(),
        name="api-export-detail",
    ),
]
