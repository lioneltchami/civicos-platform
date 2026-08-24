"""
Serializers for the Notification inbox API.

Notes on model field mapping:
- Notification.recipient  → the owning user (FK to AUTH_USER_MODEL)
- Notification.read_at    → DateTimeField (nullable); there is NO is_read BooleanField.
                            is_read is therefore derived: read_at is not None.
- Notification.body       → the message content
- Notification.subject    → optional subject line
- Notification.channel    → NotificationChannel choices (email / sms / in_app)
- Notification.status     → NotificationStatus (pending / sent / delivered / failed / bounced)
"""

from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Read-only representation of a Notification for the citizen inbox."""

    # Derived: the model stores read_at (datetime) not a boolean is_read field.
    is_read = serializers.SerializerMethodField(
        help_text="True when the notification has been read (read_at is set)."
    )
    channel_display = serializers.CharField(source="get_channel_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Notification
        fields = [  # noqa: RUF012
            "id",
            "subject",
            "body",
            "channel",
            "channel_display",
            "status",
            "status_display",
            "is_read",
            "read_at",
            "sent_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_read(self, obj: Notification) -> bool:
        return obj.read_at is not None


class MarkReadSerializer(serializers.Serializer):
    """
    Input serializer for marking a notification as read.

    Accepts an optional ``is_read`` boolean (defaults True).  The view
    translates this into setting or clearing ``read_at`` on the model.
    """

    is_read = serializers.BooleanField(default=True)
