"""Tests for the workflows models."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.workflows.models import (
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    WorkItem,
    WorkItemComment,
    WorkItemHistory,
    WorkItemPriority,
    WorkItemStatus,
)

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_staff(email=None):
    u = User.objects.create_user(
        email=email or f"staff{uuid.uuid4().hex[:6]}@gov.ca",
        password=VALID_PASSWORD,
        is_staff=True,
    )
    return u


def make_work_item(**kwargs):
    """Create a minimal WorkItem directly (bypassing service layer)."""
    ct = ContentType.objects.get_for_model(User)
    user = make_staff()
    return WorkItem.objects.create(
        content_type=ct,
        object_id=str(user.pk),
        title=kwargs.get("title", "Test Work Item"),
        status=kwargs.get("status", WorkItemStatus.PENDING),
        priority=kwargs.get("priority", WorkItemPriority.NORMAL),
        due_at=kwargs.get("due_at", timezone.now() + timezone.timedelta(hours=120)),
    )


class WorkItemStatusChoicesTest(TestCase):
    def test_all_statuses_in_valid_transitions(self):
        for status in WorkItemStatus.values:
            self.assertIn(status, VALID_TRANSITIONS)

    def test_terminal_statuses_have_no_transitions(self):
        for status in TERMINAL_STATUSES:
            self.assertEqual(VALID_TRANSITIONS[status], set())


class WorkItemModelTest(TestCase):
    def test_str_returns_title(self):
        item = make_work_item(title="My Task")
        self.assertEqual(str(item), "My Task")

    def test_default_status_is_pending(self):
        item = make_work_item()
        self.assertEqual(item.status, WorkItemStatus.PENDING)

    def test_default_priority_is_normal(self):
        item = make_work_item()
        self.assertEqual(item.priority, WorkItemPriority.NORMAL)

    def test_default_escalation_level_is_zero(self):
        item = make_work_item()
        self.assertEqual(item.escalation_level, 0)

    def test_is_overdue_false_when_due_in_future(self):
        item = make_work_item(due_at=timezone.now() + timezone.timedelta(hours=10))
        self.assertFalse(item.is_overdue)

    def test_is_overdue_true_when_due_in_past_and_not_terminal(self):
        item = make_work_item(due_at=timezone.now() - timezone.timedelta(hours=1))
        self.assertTrue(item.is_overdue)

    def test_is_overdue_false_when_completed(self):
        item = make_work_item(
            status=WorkItemStatus.COMPLETED,
            due_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.assertFalse(item.is_overdue)

    def test_is_sla_breached_false_by_default(self):
        item = make_work_item()
        self.assertFalse(item.is_sla_breached)

    def test_is_sla_breached_true_when_breached_at_set(self):
        item = make_work_item()
        item.sla_breached_at = timezone.now()
        item.save()
        self.assertTrue(item.is_sla_breached)

    def test_is_escalated_false_by_default(self):
        item = make_work_item()
        self.assertFalse(item.is_escalated)

    def test_is_escalated_true_when_level_gt_zero(self):
        item = make_work_item()
        item.escalation_level = 1
        item.save()
        self.assertTrue(item.is_escalated)

    def test_can_transition_to_valid(self):
        item = make_work_item(status=WorkItemStatus.PENDING)
        self.assertTrue(item.can_transition_to(WorkItemStatus.IN_PROGRESS))

    def test_can_transition_to_invalid(self):
        item = make_work_item(status=WorkItemStatus.PENDING)
        self.assertFalse(item.can_transition_to(WorkItemStatus.COMPLETED))

    def test_can_transition_to_returns_false_from_terminal(self):
        item = make_work_item(status=WorkItemStatus.COMPLETED)
        self.assertFalse(item.can_transition_to(WorkItemStatus.IN_PROGRESS))

    def test_pk_is_uuid(self):
        item = make_work_item()
        self.assertIsInstance(item.pk, uuid.UUID)


class WorkItemHistoryImmutabilityTest(TestCase):
    def test_history_row_cannot_be_updated(self):
        item = make_work_item()
        actor = make_staff()
        entry = WorkItemHistory.objects.create(
            work_item=item,
            action="created",
            actor=actor,
        )
        entry.notes = "should not update"
        with self.assertRaises(ValueError):
            entry.save()

    def test_history_str(self):
        item = make_work_item(title="My Item")
        actor = make_staff()
        entry = WorkItemHistory.objects.create(
            work_item=item,
            action="test_action",
            actor=actor,
        )
        self.assertIn("My Item", str(entry))
        self.assertIn("test_action", str(entry))


class WorkItemCommentTest(TestCase):
    def test_comment_str(self):
        item = make_work_item()
        author = make_staff()
        comment = WorkItemComment.objects.create(
            work_item=item,
            author=author,
            body="Internal note here",
        )
        self.assertIn("Comment by", str(comment))

    def test_comment_body_stored(self):
        item = make_work_item()
        author = make_staff()
        comment = WorkItemComment.objects.create(work_item=item, author=author, body="Test body")
        self.assertEqual(comment.body, "Test body")
