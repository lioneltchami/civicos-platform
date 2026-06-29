"""
URL configuration for the Portal API building block.

Mounted at /api/v1/portal/ by apps.api.urls.
"""

from django.urls import path

from apps.api.portal.views import (
    CancelServiceRequestView,
    ServiceRequestDetailView,
    ServiceRequestListCreateView,
)

urlpatterns = [
    path(
        "requests/",
        ServiceRequestListCreateView.as_view(),
        name="request-list-create",
    ),
    path(
        "requests/<str:reference_number>/",
        ServiceRequestDetailView.as_view(),
        name="request-detail",
    ),
    path(
        "requests/<str:reference_number>/cancel/",
        CancelServiceRequestView.as_view(),
        name="request-cancel",
    ),
]
