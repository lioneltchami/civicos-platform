"""Tests for workflow signal handlers."""

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase

from apps.workflows import handlers
from apps.workflows.models import WorkItemPriority


class WorkflowAuditHandlerTest(TestCase):
    def setUp(self):
        self.work_item = SimpleNamespace(
            pk=uuid4(),
            title="Road repair",
            priority=WorkItemPriority.HIGH,
        )
        self.actor = SimpleNamespace(
            pk=uuid4(),
            email="staff@gov.ca",
        )

    @patch("apps.workflows.handlers.record_event")
    def test_created_handler_uses_audit_service_contract(self, mock_record_event):
        handlers.audit_work_item_created(
            sender=object(),
            work_item=self.work_item,
            actor=self.actor,
        )

        mock_record_event.assert_called_once_with(
            event_type="workflows.work_item.created",
            outcome="success",
            actor_id=str(self.actor.pk),
            actor_email="",
            resource_type="workflows.WorkItem",
            resource_id=str(self.work_item.pk),
            event_detail={
                "title": self.work_item.title,
                "priority": self.work_item.priority,
            },
        )

    @patch("apps.workflows.handlers.record_event")
    def test_status_change_handler_trims_notes_and_serializes_detail(self, mock_record_event):
        handlers.audit_work_item_status_changed(
            sender=object(),
            work_item=self.work_item,
            old_status="pending",
            new_status="in_progress",
            actor=self.actor,
            notes="x" * 600,
        )

        kwargs = mock_record_event.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "workflows.work_item.status_changed")
        self.assertEqual(kwargs["actor_id"], str(self.actor.pk))
        self.assertEqual(kwargs["actor_email"], "")
        self.assertEqual(kwargs["resource_id"], str(self.work_item.pk))
        self.assertEqual(kwargs["event_detail"]["old_status"], "pending")
        self.assertEqual(kwargs["event_detail"]["new_status"], "in_progress")
        self.assertEqual(len(kwargs["event_detail"]["notes"]), 500)

