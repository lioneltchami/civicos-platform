"""Tests for the workflows staff queue views."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.workflows.models import (
    WorkItem,
    WorkItemPriority,
    WorkItemStatus,
)

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"

QUEUE_URL = "/workflows/"


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


def make_work_item(**kwargs):
    ct = ContentType.objects.get_for_model(User)
    return WorkItem.objects.create(
        content_type=ct,
        object_id=str(uuid.uuid4()),
        title=kwargs.get("title", "Test Item"),
        status=kwargs.get("status", WorkItemStatus.PENDING),
        priority=kwargs.get("priority", WorkItemPriority.NORMAL),
        due_at=kwargs.get("due_at", timezone.now() + timezone.timedelta(hours=120)),
        assigned_to=kwargs.get("assigned_to"),
    )


# ---------------------------------------------------------------------------
# Queue view
# ---------------------------------------------------------------------------


class WorkItemQueueViewTest(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)

    def test_queue_loads_for_staff(self):
        response = self.client.get(QUEUE_URL)
        self.assertEqual(response.status_code, 200)

    def test_queue_requires_login(self):
        self.client.logout()
        response = self.client.get(QUEUE_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_queue_returns_403_for_citizen(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.get(QUEUE_URL)
        self.assertEqual(response.status_code, 403)

    def test_queue_uses_correct_template(self):
        response = self.client.get(QUEUE_URL)
        self.assertTemplateUsed(response, "workflows/queue.html")

    def test_queue_shows_open_items(self):
        make_work_item(title="Open Item")
        response = self.client.get(QUEUE_URL)
        self.assertContains(response, "Open Item")

    def test_queue_excludes_completed_items(self):
        make_work_item(title="Done", status=WorkItemStatus.COMPLETED)
        response = self.client.get(QUEUE_URL)
        self.assertNotContains(response, "Done")

    def test_queue_filters_by_status(self):
        make_work_item(title="Pending Item", status=WorkItemStatus.PENDING)
        make_work_item(title="In Progress Item", status=WorkItemStatus.IN_PROGRESS)
        response = self.client.get(f"{QUEUE_URL}?status=pending")
        self.assertContains(response, "Pending Item")
        self.assertNotContains(response, "In Progress Item")

    def test_context_contains_total_open(self):
        make_work_item()
        make_work_item()
        response = self.client.get(QUEUE_URL)
        self.assertIn("total_open", response.context)
        self.assertEqual(response.context["total_open"], 2)

    def test_context_my_items_count_reflects_assigned_items(self):
        """my_items_count must count only items assigned to the current user."""
        other = make_staff("other@gov.ca")
        make_work_item(assigned_to=self.staff)  # mine
        make_work_item(assigned_to=other)  # not mine
        make_work_item()  # unassigned
        response = self.client.get(QUEUE_URL)
        self.assertEqual(response.context["my_items_count"], 1)


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


class WorkItemDetailViewTest(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)
        self.item = make_work_item(title="Detail Item")

    def detail_url(self):
        return reverse("workflows:detail", kwargs={"pk": self.item.pk})

    def test_detail_loads_for_staff(self):
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 200)

    def test_detail_requires_login(self):
        self.client.logout()
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 302)

    def test_detail_returns_403_for_citizen(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 403)

    def test_detail_shows_title(self):
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Detail Item")

    def test_detail_uses_correct_template(self):
        response = self.client.get(self.detail_url())
        self.assertTemplateUsed(response, "workflows/detail.html")

    def test_detail_404_for_unknown_pk(self):
        url = reverse("workflows:detail", kwargs={"pk": uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Claim view
# ---------------------------------------------------------------------------


class ClaimWorkItemViewTest(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)
        self.item = make_work_item()

    def claim_url(self):
        return reverse("workflows:claim", kwargs={"pk": self.item.pk})

    def test_claim_assigns_and_redirects(self):
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.staff)

    def test_claim_requires_post(self):
        response = self.client.get(self.claim_url())
        self.assertEqual(response.status_code, 405)

    def test_claim_requires_login(self):
        self.client.logout()
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 302)

    def test_claim_requires_staff(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 403)

    def test_claim_already_assigned_shows_error_message(self):
        other = make_staff("other@gov.ca")
        self.item.assigned_to = other
        self.item.save()
        response = self.client.post(self.claim_url(), follow=True)
        self.assertContains(response, "already assigned")

    def test_csrf_required(self):
        from django.test import Client

        c = Client(enforce_csrf_checks=True)
        c.force_login(self.staff)
        response = c.post(self.claim_url())
        self.assertEqual(response.status_code, 403)


class CsrfEnforcementTest(TestCase):
    """CSRF is enforced on all POST-only workflow action views."""

    def setUp(self):
        self.staff = make_staff()
        self.item = make_work_item()

    def _csrf_client(self):
        from django.test import Client

        c = Client(enforce_csrf_checks=True)
        c.force_login(self.staff)
        return c

    def test_csrf_required_claim(self):
        c = self._csrf_client()
        url = reverse("workflows:claim", kwargs={"pk": self.item.pk})
        self.assertEqual(c.post(url).status_code, 403)

    def test_csrf_required_advance_status(self):
        c = self._csrf_client()
        url = reverse("workflows:advance-status", kwargs={"pk": self.item.pk})
        self.assertEqual(c.post(url, {"new_status": "in_progress"}).status_code, 403)

    def test_csrf_required_escalate(self):
        c = self._csrf_client()
        url = reverse("workflows:escalate", kwargs={"pk": self.item.pk})
        self.assertEqual(c.post(url, {"reason": ""}).status_code, 403)

    def test_csrf_required_add_comment(self):
        c = self._csrf_client()
        url = reverse("workflows:add-comment", kwargs={"pk": self.item.pk})
        self.assertEqual(c.post(url, {"body": "note"}).status_code, 403)

    def test_csrf_required_assign(self):
        assignee = make_staff("worker@gov.ca")
        c = self._csrf_client()
        url = reverse("workflows:assign", kwargs={"pk": self.item.pk})
        self.assertEqual(c.post(url, {"assignee": assignee.pk}).status_code, 403)


# ---------------------------------------------------------------------------
# Advance status view
# ---------------------------------------------------------------------------


class AdvanceStatusViewTest(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)
        self.item = make_work_item(status=WorkItemStatus.PENDING)

    def advance_url(self):
        return reverse("workflows:advance-status", kwargs={"pk": self.item.pk})

    def test_valid_transition_updates_status(self):
        self.client.post(self.advance_url(), {"new_status": WorkItemStatus.IN_PROGRESS})
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, WorkItemStatus.IN_PROGRESS)

    def test_invalid_transition_shows_error_and_does_not_change_status(self):
        # PENDING → COMPLETED is invalid per state machine
        self.client.post(
            self.advance_url(),
            {"new_status": WorkItemStatus.COMPLETED},
            follow=True,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, WorkItemStatus.PENDING)

    def test_redirects_to_detail(self):
        response = self.client.post(self.advance_url(), {"new_status": WorkItemStatus.IN_PROGRESS})
        self.assertRedirects(
            response,
            reverse("workflows:detail", kwargs={"pk": self.item.pk}),
            fetch_redirect_response=False,
        )

    def test_requires_post(self):
        response = self.client.get(self.advance_url())
        self.assertEqual(response.status_code, 405)

    def test_requires_staff(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.post(self.advance_url(), {"new_status": WorkItemStatus.IN_PROGRESS})
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Escalate view
# ---------------------------------------------------------------------------


class EscalateWorkItemViewTest(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)
        self.item = make_work_item()

    def escalate_url(self):
        return reverse("workflows:escalate", kwargs={"pk": self.item.pk})

    def test_escalate_increments_level(self):
        self.client.post(self.escalate_url(), {"reason": "Urgent"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.escalation_level, 1)

    def test_escalate_redirects_to_detail(self):
        response = self.client.post(self.escalate_url(), {"reason": ""})
        self.assertRedirects(
            response,
            reverse("workflows:detail", kwargs={"pk": self.item.pk}),
            fetch_redirect_response=False,
        )

    def test_escalate_requires_staff(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.post(self.escalate_url(), {"reason": ""})
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Add comment view
# ---------------------------------------------------------------------------


class AddCommentViewTest(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)
        self.item = make_work_item()

    def comment_url(self):
        return reverse("workflows:add-comment", kwargs={"pk": self.item.pk})

    def test_adds_comment(self):
        self.client.post(self.comment_url(), {"body": "Internal note"})
        self.assertEqual(self.item.comments.count(), 1)
        self.assertEqual(self.item.comments.first().body, "Internal note")

    def test_empty_body_shows_error(self):
        self.client.post(self.comment_url(), {"body": ""}, follow=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.comments.count(), 0)

    def test_requires_staff(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.post(self.comment_url(), {"body": "Note"})
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Assign view
# ---------------------------------------------------------------------------


class AssignWorkItemViewTest(TestCase):
    def setUp(self):
        self.supervisor = make_staff("super@gov.ca")
        self.assignee = make_staff("worker@gov.ca")
        self.client.force_login(self.supervisor)
        self.item = make_work_item()

    def assign_url(self):
        return reverse("workflows:assign", kwargs={"pk": self.item.pk})

    def test_assigns_to_staff_member(self):
        self.client.post(self.assign_url(), {"assignee": self.assignee.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.assignee)

    def test_invalid_form_shows_error(self):
        self.client.post(self.assign_url(), {"assignee": ""}, follow=True)
        # Status should not have changed
        self.item.refresh_from_db()
        self.assertIsNone(self.item.assigned_to)

    def test_requires_staff(self):
        citizen = make_citizen()
        self.client.force_login(citizen)
        response = self.client.post(self.assign_url(), {"assignee": self.assignee.pk})
        self.assertEqual(response.status_code, 403)
