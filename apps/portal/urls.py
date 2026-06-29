"""
URL patterns for the citizen portal.

All URLs are under /portal/ (or /fr/portal/ for French).
All views require authentication (enforced via LoginRequiredMixin or decorator).
"""

from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("requests/", views.ServiceRequestListView.as_view(), name="request-list"),
    path("requests/<uuid:pk>/", views.ServiceRequestDetailView.as_view(), name="request-detail"),
]
