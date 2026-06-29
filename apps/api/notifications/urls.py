"""
URL configuration for the Notification inbox API sub-package.

Mounted at /api/v1/notifications/ by apps.api.urls.
"""

from django.urls import path

from apps.api.notifications import views

urlpatterns = [
    path(
        "",
        views.NotificationListView.as_view(),
        name="notification-list",
    ),
    path(
        "<uuid:pk>/",
        views.NotificationDetailView.as_view(),
        name="notification-detail",
    ),
    path(
        "<uuid:pk>/read/",
        views.MarkNotificationReadView.as_view(),
        name="notification-mark-read",
    ),
]
