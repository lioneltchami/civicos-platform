"""URL configuration for the workflows building block."""

from django.urls import path

from apps.workflows import views

app_name = "workflows"

urlpatterns = [
    # Staff queue
    path("", views.WorkItemQueueView.as_view(), name="queue"),
    # Work item detail
    path("<uuid:pk>/", views.WorkItemDetailView.as_view(), name="detail"),
    # Actions (all POST)
    path("<uuid:pk>/claim/", views.ClaimWorkItemView.as_view(), name="claim"),
    path("<uuid:pk>/assign/", views.AssignWorkItemView.as_view(), name="assign"),
    path("<uuid:pk>/status/", views.AdvanceStatusView.as_view(), name="advance-status"),
    path("<uuid:pk>/escalate/", views.EscalateWorkItemView.as_view(), name="escalate"),
    path("<uuid:pk>/comment/", views.AddCommentView.as_view(), name="add-comment"),
]
