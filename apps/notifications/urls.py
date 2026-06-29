"""URL patterns for the notifications building block."""
from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="inbox"),
    path("<uuid:pk>/read/", views.MarkNotificationReadView.as_view(), name="mark-read"),
    path("mark-all-read/", views.MarkAllReadView.as_view(), name="mark-all-read"),
    path("unread-count/", views.UnreadCountView.as_view(), name="unread-count"),
]
