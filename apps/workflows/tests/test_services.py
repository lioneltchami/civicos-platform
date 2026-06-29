"""Tests for the workflows service layer."""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.workflows.models import (
    WorkItem,
    WorkItemHistory,
    WorkItemPriority,
    WorkItemStatus,
)
from apps.workflows.services import (
    add_comment,
    assign_work_item,
    check_sla_breaches,
    claim_work_item,
    create_work_item,
    escalate_work_item,
    get_staff_queue,
    update_work_item_status,
)

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_staff(email=None):
    return User.objects.create_user(
        email=email or f"staff{uuid.uuid4().hex[:6]}@gov.ca",
        password=VALID_PASSWORD,
        is_staff=True,
    )


def make_citizen(email=None):
    return User.objects.create_user(
        email=email or f"citizen{uuid.uuid4().hex[:6]}@example.com",
        password=VALID_PASSWORD,
        is_staff=False,
    )


def make_work_item_direct(**kwargs):
    """Bypass service layer for test setup fixtures."""
    ct = ContentType.objects.get_for_model(User)
    user = make_staff()
    return WorkItem.objects.create(
        content_type=ct,
        object_id=str(user.pk),
        title=kwargs.get("title", "Fixture Item"),
        status=kwargs.get("status", WorkItemStatus.PENDING),
        priority=kwargs.get("priority", WorkItemPriority.NORMAL),
        due_at=kwargs.get("due_at", timezone.now() + timezone.timedelta(hours=120)),
        assigned_to=kwargs.get("assigned_to"),
    )


class CreateWorkItemTest(TestCase):

    def setUp(self):
        self.actor = make_staff()
        self.content_object = make_citizen()

    def test_creates_work_item(self):
        with self.captureOnCommitCallbacks(execute=True):
            item = create_work_item(self.content_object, title="New task", actor=self.actor)
        self.assertIsNotNone(item.pk)

    def test_status_is_pending(self):
        with self.captureOnCommitCallbacks(execute=True):
            item = create_work_item(self.content_object, title="New task", actor=self.actor)
        self.assertEqual(item.status, WorkItemStatus.PENDING)

    def test_due_at_computed_from_priority(self):
        with self.captureOnCommitCallbacks(execute=True):
            item = create_work_item(
                self.content_object, title="T", actor=self.actor,
                priority=WorkItemPriority.CRITICAL
            )
        self.assertIsNotNone(item.due_at)

    def test_due_at_can_be_overridden(self):
        explicit_due = timezone.now() + timezone.timedelta(days=7)
        with self.captureOnCommitCallbacks(execute=True):
            item = create_work_item(
                self.content_object, title="T", actor=self.actor, due_at=explicit_due
            )
        self.assertAlmostEqual(
            item.due_at.timestamp(), explicit_due.timestamp(), delta=1
        )

    def test_creates_history_record(self):
        with self.captureOnCommitCallbacks(execute=True):
            item = create_work_item(self.content_object, title="T", actor=self.actor)
        self.assertEqual(item.history.count(), 1)
        self.assertEqual(item.history.first().action, "created")

    def test_raises_if_title_empty(self):
        with self.assertRaises(ValueError):
            create_work_item(self.content_object, title="", actor=self.actor)

    def test_actor_none_allowed_for_system_created_items(self):
        """actor=None is permitted for system-initiated WorkItems (e.g. portal signal handler).
        History entry records actor=None, which is acceptable and auditable."""
        with self.captureOnCommitCallbacks(execute=True):
            item = create_work_item(self.content_object, title="System item", actor=None)
        self.assertIsNotNone(item.pk)
        self.assertIsNone(item.history.first().actor)

    def test_fires_work_item_created_signal(self):
        from apps.workflows.signals import work_item_created
        received = []
        work_item_created.connect(lambda **kw: received.append(kw), weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                create_work_item(self.content_object, title="T", actor=self.actor)
            self.assertEqual(len(received), 1)
        finally:
            work_item_created.disconnect()


class ClaimWorkItemTest(TestCase):

    def setUp(self):
        self.actor = make_staff()
        self.item = make_work_item_direct()

    def test_assigns_actor_to_work_item(self):
        with self.captureOnCommitCallbacks(execute=True):
            claim_work_item(self.item, self.actor)
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.actor)

    def test_status_changes_to_in_progress(self):
        with self.captureOnCommitCallbacks(execute=True):
            claim_work_item(self.item, self.actor)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, WorkItemStatus.IN_PROGRESS)

    def test_records_history(self):
        with self.captureOnCommitCallbacks(execute=True):
            claim_work_item(self.item, self.actor)
        self.assertTrue(self.item.history.filter(action="claimed").exists())

    def test_raises_if_already_assigned(self):
        other = make_staff("other@gov.ca")
        self.item.assigned_to = other
        self.item.save()
        with self.assertRaises(ValueError):
            claim_work_item(self.item, self.actor)

    def test_raises_if_terminal_status(self):
        self.item.status = WorkItemStatus.COMPLETED
        self.item.save()
        with self.assertRaises(ValueError):
            claim_work_item(self.item, self.actor)

    def test_raises_if_non_staff(self):
        citizen = make_citizen()
        with self.assertRaises(PermissionError):
            claim_work_item(self.item, citizen)


class AssignWorkItemTest(TestCase):

    def setUp(self):
        self.supervisor = make_staff("super@gov.ca")
        self.assignee = make_staff("worker@gov.ca")
        self.item = make_work_item_direct()

    def test_sets_assigned_to(self):
        with self.captureOnCommitCallbacks(execute=True):
            assign_work_item(self.item, self.assignee, self.supervisor)
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.assignee)

    def test_records_history(self):
        with self.captureOnCommitCallbacks(execute=True):
            assign_work_item(self.item, self.assignee, self.supervisor)
        self.assertTrue(self.item.history.filter(action="assigned").exists())

    def test_raises_if_actor_not_staff(self):
        citizen = make_citizen()
        with self.assertRaises(PermissionError):
            assign_work_item(self.item, self.assignee, citizen)

    def test_raises_if_assignee_not_staff(self):
        citizen = make_citizen()
        with self.assertRaises(ValueError):
            assign_work_item(self.item, citizen, self.supervisor)

    def test_raises_if_terminal(self):
        self.item.status = WorkItemStatus.CANCELLED
        self.item.save()
        with self.assertRaises(ValueError):
            assign_work_item(self.item, self.assignee, self.supervisor)


class UpdateWorkItemStatusTest(TestCase):

    def setUp(self):
        self.actor = make_staff()
        self.item = make_work_item_direct(status=WorkItemStatus.PENDING)

    def test_valid_transition_updates_status(self):
        with self.captureOnCommitCallbacks(execute=True):
            update_work_item_status(self.item, WorkItemStatus.IN_PROGRESS, self.actor)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, WorkItemStatus.IN_PROGRESS)

    def test_sets_completed_at_on_completion(self):
        self.item.status = WorkItemStatus.IN_PROGRESS
        self.item.save()
        with self.captureOnCommitCallbacks(execute=True):
            update_work_item_status(self.item, WorkItemStatus.COMPLETED, self.actor)
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.completed_at)

    def test_records_history(self):
        with self.captureOnCommitCallbacks(execute=True):
            update_work_item_status(self.item, WorkItemStatus.IN_PROGRESS, self.actor)
        self.assertTrue(
            self.item.history.filter(
                action__startswith="status_changed_to_"
            ).exists()
        )

    def test_raises_on_invalid_transition(self):
        # PENDING → COMPLETED is not valid
        with self.assertRaises(ValueError):
            update_work_item_status(self.item, WorkItemStatus.COMPLETED, self.actor)

    def test_raises_on_unknown_status(self):
        with self.assertRaises(ValueError):
            update_work_item_status(self.item, "nonexistent", self.actor)

    def test_raises_if_non_staff(self):
        citizen = make_citizen()
        with self.assertRaises(PermissionError):
            update_work_item_status(self.item, WorkItemStatus.IN_PROGRESS, citizen)

    def test_fires_status_changed_signal(self):
        from apps.workflows.signals import work_item_status_changed
        received = []
        work_item_status_changed.connect(lambda **kw: received.append(kw), weak=False)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                update_work_item_status(self.item, WorkItemStatus.IN_PROGRESS, self.actor)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["old_status"], WorkItemStatus.PENDING)
            self.assertEqual(received[0]["new_status"], WorkItemStatus.IN_PROGRESS)
        finally:
            work_item_status_changed.disconnect()


class EscalateWorkItemTest(TestCase):

    def setUp(self):
        self.actor = make_staff()
        self.item = make_work_item_direct()

    def test_increments_escalation_level(self):
        with self.captureOnCommitCallbacks(execute=True):
            escalate_work_item(self.item, self.actor)
        self.item.refresh_from_db()
        self.assertEqual(self.item.escalation_level, 1)

    def test_sets_escalated_at(self):
        with self.captureOnCommitCallbacks(execute=True):
            escalate_work_item(self.item, self.actor)
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.escalated_at)

    def test_escalated_at_not_overwritten_on_second_escalation(self):
        with self.captureOnCommitCallbacks(execute=True):
            escalate_work_item(self.item, self.actor)
        self.item.refresh_from_db()
        first_escalated_at = self.item.escalated_at
        with self.captureOnCommitCallbacks(execute=True):
            escalate_work_item(self.item, self.actor)
        self.item.refresh_from_db()
        self.assertEqual(self.item.escalated_at, first_escalated_at)

    def test_records_history(self):
        with self.captureOnCommitCallbacks(execute=True):
            escalate_work_item(self.item, self.actor)
        self.assertTrue(self.item.history.filter(action="escalated").exists())

    def test_raises_if_max_escalation(self):
        self.item.escalation_level = 2
        self.item.save()
        with self.assertRaises(ValueError):
            escalate_work_item(self.item, self.actor)

    def test_raises_if_terminal(self):
        self.item.status = WorkItemStatus.COMPLETED
        self.item.save()
        with self.assertRaises(ValueError):
            escalate_work_item(self.item, self.actor)

    def test_raises_if_non_staff(self):
        citizen = make_citizen()
        with self.assertRaises(PermissionError):
            escalate_work_item(self.item, citizen)


class AddCommentTest(TestCase):

    def setUp(self):
        self.actor = make_staff()
        self.item = make_work_item_direct()

    def test_creates_comment(self):
        with self.captureOnCommitCallbacks(execute=True):
            comment = add_comment(self.item, self.actor, "Test note")
        self.assertEqual(comment.body, "Test note")
        self.assertEqual(comment.author, self.actor)

    def test_records_history(self):
        with self.captureOnCommitCallbacks(execute=True):
            add_comment(self.item, self.actor, "Some note")
        self.assertTrue(self.item.history.filter(action="commented").exists())

    def test_raises_if_body_empty(self):
        with self.assertRaises(ValueError):
            add_comment(self.item, self.actor, "   ")

    def test_raises_if_non_staff(self):
        citizen = make_citizen()
        with self.assertRaises(PermissionError):
            add_comment(self.item, citizen, "Note")


class GetStaffQueueTest(TestCase):

    def setUp(self):
        self.actor = make_staff()
        self.ct = ContentType.objects.get_for_model(User)

    def _make_item(self, **kwargs):
        return WorkItem.objects.create(
            content_type=self.ct,
            object_id=str(uuid.uuid4()),
            title=kwargs.get("title", "Item"),
            status=kwargs.get("status", WorkItemStatus.PENDING),
            priority=kwargs.get("priority", WorkItemPriority.NORMAL),
            due_at=timezone.now() + timezone.timedelta(hours=120),
            assigned_to=kwargs.get("assigned_to"),
        )

    def test_returns_open_items(self):
        self._make_item(title="Open")
        self._make_item(title="Completed", status=WorkItemStatus.COMPLETED)
        qs = get_staff_queue(self.actor)
        self.assertEqual(qs.count(), 1)

    def test_excludes_terminal_statuses(self):
        self._make_item(status=WorkItemStatus.COMPLETED)
        self._make_item(status=WorkItemStatus.CANCELLED)
        qs = get_staff_queue(self.actor)
        self.assertEqual(qs.count(), 0)

    def test_filter_by_status(self):
        self._make_item(status=WorkItemStatus.PENDING)
        self._make_item(status=WorkItemStatus.IN_PROGRESS)
        qs = get_staff_queue(self.actor, status_filter=WorkItemStatus.PENDING)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().status, WorkItemStatus.PENDING)

    def test_filter_mine_only(self):
        self._make_item(assigned_to=self.actor)
        self._make_item()  # unassigned
        qs = get_staff_queue(self.actor, assigned_to_me=True)
        self.assertEqual(qs.count(), 1)

    def test_filter_unassigned_only(self):
        self._make_item(assigned_to=self.actor)
        self._make_item()  # unassigned
        qs = get_staff_queue(self.actor, unassigned_only=True)
        self.assertEqual(qs.count(), 1)
        self.assertIsNone(qs.first().assigned_to)

    def test_filter_by_priority(self):
        self._make_item(priority=WorkItemPriority.CRITICAL)
        self._make_item(priority=WorkItemPriority.NORMAL)
        qs = get_staff_queue(self.actor, priority_filter=WorkItemPriority.CRITICAL)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().priority, WorkItemPriority.CRITICAL)


class CheckSlaBreachesTest(TestCase):

    def setUp(self):
        self.ct = ContentType.objects.get_for_model(User)

    def _make_item(self, **kwargs):
        return WorkItem.objects.create(
            content_type=self.ct,
            object_id=str(uuid.uuid4()),
            title=kwargs.get("title", "Item"),
            status=kwargs.get("status", WorkItemStatus.PENDING),
            priority=WorkItemPriority.NORMAL,
            due_at=kwargs.get("due_at", timezone.now() - timezone.timedelta(hours=1)),
        )

    def test_marks_overdue_items(self):
        item = self._make_item()
        count = check_sla_breaches()
        self.assertEqual(count, 1)
        item.refresh_from_db()
        self.assertIsNotNone(item.sla_breached_at)

    def test_does_not_mark_future_items(self):
        self._make_item(due_at=timezone.now() + timezone.timedelta(hours=10))
        count = check_sla_breaches()
        self.assertEqual(count, 0)

    def test_idempotent_second_run(self):
        self._make_item()
        check_sla_breaches()
        count2 = check_sla_breaches()
        self.assertEqual(count2, 0)

    def test_skips_terminal_items(self):
        self._make_item(status=WorkItemStatus.COMPLETED)
        count = check_sla_breaches()
        self.assertEqual(count, 0)

    def test_creates_history_entry(self):
        item = self._make_item()
        check_sla_breaches()
        self.assertTrue(item.history.filter(action="sla_breached").exists())
