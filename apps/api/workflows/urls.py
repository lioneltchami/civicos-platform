"""
URL configuration for the Workflows API sub-package.

Mounted at /api/v1/workflows/ by apps.api.urls.
All routes require IsStaff — citizens are never permitted access.
"""

from django.urls import path

from apps.api.workflows import views

urlpatterns = [
    # Staff queue (list, filterable)
    path(
        "queue/",
        views.WorkItemQueueView.as_view(),
        name="workflow-queue",
    ),
    # WorkItem detail (read-only; includes history + comments)
    path(
        "<uuid:pk>/",
        views.WorkItemDetailAPIView.as_view(),
        name="workflow-detail",
    ),
    # Action endpoints — all POST
    path(
        "<uuid:pk>/claim/",
        views.ClaimWorkItemAPIView.as_view(),
        name="workflow-claim",
    ),
    path(
        "<uuid:pk>/assign/",
        views.AssignWorkItemAPIView.as_view(),
        name="workflow-assign",
    ),
    path(
        "<uuid:pk>/status/",
        views.AdvanceStatusAPIView.as_view(),
        name="workflow-status",
    ),
    path(
        "<uuid:pk>/escalate/",
        views.EscalateWorkItemAPIView.as_view(),
        name="workflow-escalate",
    ),
    path(
        "<uuid:pk>/comment/",
        views.AddCommentAPIView.as_view(),
        name="workflow-comment",
    ),
]
