"""
Serializers for the Workflows API.

Design notes:
- WorkItemSerializer is the lightweight list representation.
- WorkItemDetailSerializer extends it with nested history and comments.
- Action serializers (Claim, Assign, AdvanceStatus, Escalate, AddComment)
  validate inputs only; the service layer performs all mutations.
- is_overdue and is_sla_breached are model properties exposed via
  SerializerMethodField so they appear in API responses.
- actor_display / author_display / assigned_to_name all use
  user.display_name (available on CustomUser) and gracefully fall back
  to "(deleted user)" when the FK is null.
- No PII (email, full name via display_name is acceptable as it is an
  explicit UI label, but we do NOT include email addresses in any field).
"""

from django.contrib.auth import get_user_model

from rest_framework import serializers

from apps.workflows.models import WorkItem, WorkItemComment, WorkItemHistory, WorkItemStatus

User = get_user_model()


class WorkItemHistorySerializer(serializers.ModelSerializer):
    """Immutable audit trail entry for a WorkItem."""

    actor_display = serializers.SerializerMethodField(
        help_text="Display name of the staff member who performed the action, or 'System'."
    )

    class Meta:
        model = WorkItemHistory
        fields = [
            "id",
            "action",
            "old_status",
            "new_status",
            "actor_display",
            "notes",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_display(self, obj: WorkItemHistory) -> str:
        if obj.actor is None:
            return "System"
        return getattr(obj.actor, "display_name", None) or f"User {obj.actor.pk}"


class WorkItemCommentSerializer(serializers.ModelSerializer):
    """Internal staff comment on a WorkItem."""

    author_display = serializers.SerializerMethodField(
        help_text="Display name of the comment author."
    )

    class Meta:
        model = WorkItemComment
        fields = [
            "id",
            "body",
            "author_display",
            "created_at",
        ]
        read_only_fields = fields

    def get_author_display(self, obj: WorkItemComment) -> str:
        if obj.author is None:
            return "(deleted user)"
        return getattr(obj.author, "display_name", None) or f"User {obj.author.pk}"


class WorkItemSerializer(serializers.ModelSerializer):
    """
    Lightweight list representation of a WorkItem.

    Excludes nested history and comments (use WorkItemDetailSerializer for those).
    """

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)

    # assigned_to FK — expose only the PK and display name, never the email.
    assigned_to_id = serializers.UUIDField(
        source="assigned_to.pk",
        read_only=True,
        allow_null=True,
        default=None,
    )
    assigned_to_name = serializers.SerializerMethodField(
        help_text="Display name of the assigned staff member, or null."
    )

    # Model properties exposed via SerializerMethodField.
    is_overdue = serializers.SerializerMethodField(
        help_text="True if due_at has passed and the item is not yet in a terminal status."
    )
    is_sla_breached = serializers.SerializerMethodField(
        help_text="True when sla_breached_at has been set by the SLA breach task."
    )

    class Meta:
        model = WorkItem
        fields = [
            "id",
            "title",
            "status",
            "status_display",
            "priority",
            "priority_display",
            "assigned_to_id",
            "assigned_to_name",
            "due_at",
            "sla_breached_at",
            "escalation_level",
            "escalated_at",
            "completed_at",
            "is_overdue",
            "is_sla_breached",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_assigned_to_name(self, obj: WorkItem) -> str | None:
        if obj.assigned_to is None:
            return None
        return getattr(obj.assigned_to, "display_name", None) or f"User {obj.assigned_to.pk}"

    def get_is_overdue(self, obj: WorkItem) -> bool:
        return obj.is_overdue

    def get_is_sla_breached(self, obj: WorkItem) -> bool:
        return obj.is_sla_breached


class WorkItemDetailSerializer(WorkItemSerializer):
    """
    Detail representation of a WorkItem including full audit history and comments.

    The view must prefetch_related("history__actor", "comments__author") before
    passing the queryset to this serializer to avoid N+1 queries.
    """

    history = WorkItemHistorySerializer(many=True, read_only=True)
    comments = WorkItemCommentSerializer(many=True, read_only=True)

    class Meta(WorkItemSerializer.Meta):
        fields = WorkItemSerializer.Meta.fields + ["description", "history", "comments"]


# ---------------------------------------------------------------------------
# Action serializers (input validation only — no model fields)
# ---------------------------------------------------------------------------

class ClaimWorkItemSerializer(serializers.Serializer):
    """
    No input required for claiming a work item.

    The actor is always request.user; validation is performed by the service layer.
    """


class AssignWorkItemSerializer(serializers.Serializer):
    """Assign a work item to a specific staff member."""

    assignee_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_staff=True, is_active=True),
        source="assignee",
        help_text="PK of the staff user to assign the work item to.",
    )


class AdvanceStatusSerializer(serializers.Serializer):
    """Advance (or regress) a WorkItem's status through the allowed state machine."""

    new_status = serializers.ChoiceField(
        choices=WorkItemStatus.choices,
        help_text="Target status. Must be a valid transition from the current status.",
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=5000,
        help_text="Optional notes explaining the status change (recorded in history).",
    )


class EscalateWorkItemSerializer(serializers.Serializer):
    """Escalate a WorkItem to the next level (max 2: supervisor → director)."""

    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=5000,
        help_text="Optional reason for escalation (recorded in history).",
    )


class AddCommentSerializer(serializers.Serializer):
    """Add an internal staff comment to a WorkItem."""

    body = serializers.CharField(
        min_length=1,
        max_length=10000,
        help_text="Comment body. Must not be empty.",
    )
