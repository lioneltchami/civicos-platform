"""
Test suite for the Workflows API building block.

Covers WorkItemQueueView, WorkItemDetailAPIView, and all action views:
claim, assign, status-advance, escalate, and comment.

Security invariants:
- ALL workflow endpoints require is_staff=True.
- Citizens authenticated with valid JWT receive 403 (not 401 — they are authenticated).
- Unauthenticated requests receive 401.
- WorkItems are not scoped to the requesting staff member — all staff see all items.

Service layer notes (verified from apps/workflows/services.py):
- claim_work_item → raises ValueError if already assigned; PermissionError if not staff
- assign_work_item → raises ValueError if assignee is not staff
- update_work_item_status → raises ValueError for invalid transitions; state machine enforced
- escalate_work_item → raises ValueError at escalation_level >= 2
- add_comment → raises ValueError if body is empty or item is terminal
- get_staff_queue → excludes terminal statuses by default
"""

import itertools
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.workflows.models import (
    WorkItem,
    WorkItemPriority,
    WorkItemStatus,
)

User = get_user_model()

VALID_PASSWORD = "SecureTestPass123!"
QUEUE_URL = "/api/v1/workflows/queue/"

_counter = itertools.count(1)


def _email(prefix="user"):
    return f"{prefix}+{next(_counter)}@example.gov"


def _make_citizen(email=None):
    """Create and return a non-staff (citizen) user."""
    return User.objects.create_user(
        email=email or _email("citizen"),
        password=VALID_PASSWORD,
        is_staff=False,
    )


def _make_staff(email=None):
    """Create and return a staff user."""
    return User.objects.create_user(
        email=email or _email("staff"),
        password=VALID_PASSWORD,
        is_staff=True,
    )


def _bearer(client, user):
    """Obtain a JWT access token for a user and return an auth-header dict."""
    resp = client.post(
        "/api/v1/auth/token/",
        {"email": user.email, "password": VALID_PASSWORD},
        format="json",
    )
    assert resp.status_code == 200, f"Token fetch failed for {user.email}: {resp.data}"
    return {"HTTP_AUTHORIZATION": f"Bearer {resp.data['access']}"}


def _make_work_item(
    title="Test Work Item",
    status=WorkItemStatus.PENDING,
    priority=WorkItemPriority.NORMAL,
    assigned_to=None,
    escalation_level=0,
    **kwargs,
):
    """
    Create a WorkItem directly via the ORM (bypasses service layer for test setup).
    Uses the User ContentType as a stand-in content object — any valid ContentType works.
    """
    ct = ContentType.objects.get_for_model(User)
    return WorkItem.objects.create(
        content_type=ct,
        object_id=str(uuid.uuid4()),
        title=title,
        status=status,
        priority=priority,
        due_at=timezone.now() + timedelta(hours=120),
        assigned_to=assigned_to,
        escalation_level=escalation_level,
        **kwargs,
    )


def _detail_url(pk):
    return f"/api/v1/workflows/{pk}/"


def _action_url(pk, action):
    return f"/api/v1/workflows/{pk}/{action}/"


class WorkItemQueueAccessTests(TestCase):
    """Tests for GET /api/v1/workflows/queue/ — access-control enforcement."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.citizen = _make_citizen()

    def test_unauthenticated_request_returns_401(self):
        # No auth header — must return 401 (no credentials present).
        resp = self.client.get(QUEUE_URL)

        self.assertEqual(resp.status_code, 401)

    def test_citizen_gets_403_authenticated_but_wrong_role(self):
        # A citizen is authenticated but lacks is_staff; must receive 403, not 401.
        resp = self.client.get(QUEUE_URL, **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 403)

    def test_staff_gets_200_with_empty_queue(self):
        # Staff with no work items see an empty paginated queue.
        resp = self.client.get(QUEUE_URL, **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)
        self.assertEqual(resp.data["results"], [])


class WorkItemQueueListTests(TestCase):
    """Tests for GET /api/v1/workflows/queue/ — filtering and list content."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()

    def test_queue_excludes_terminal_statuses_by_default(self):
        # get_staff_queue excludes completed and cancelled items by default.
        _make_work_item(title="Active", status=WorkItemStatus.PENDING)
        _make_work_item(title="Done", status=WorkItemStatus.COMPLETED)
        _make_work_item(title="Cancelled", status=WorkItemStatus.CANCELLED)

        resp = self.client.get(QUEUE_URL, **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 200)
        titles = [item["title"] for item in resp.data["results"]]
        self.assertIn("Active", titles)
        self.assertNotIn("Done", titles)
        self.assertNotIn("Cancelled", titles)

    def test_status_filter_returns_only_matching_items(self):
        # ?status=pending must only return items with that status.
        _make_work_item(title="Pending Item", status=WorkItemStatus.PENDING)
        _make_work_item(title="In Progress Item", status=WorkItemStatus.IN_PROGRESS)

        resp = self.client.get(QUEUE_URL + "?status=pending", **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 200)
        titles = [item["title"] for item in resp.data["results"]]
        self.assertIn("Pending Item", titles)
        self.assertNotIn("In Progress Item", titles)

    def test_mine_filter_returns_only_assigned_to_self(self):
        # ?mine=1 must return only items assigned to the requesting staff member.
        other_staff = _make_staff()
        _make_work_item(title="Mine", assigned_to=self.staff, status=WorkItemStatus.IN_PROGRESS)
        _make_work_item(title="Not Mine", assigned_to=other_staff, status=WorkItemStatus.IN_PROGRESS)

        resp = self.client.get(QUEUE_URL + "?mine=1", **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 200)
        titles = [item["title"] for item in resp.data["results"]]
        self.assertIn("Mine", titles)
        self.assertNotIn("Not Mine", titles)

    def test_priority_filter_returns_only_matching_priority(self):
        # ?priority=1 must return only Critical-priority items.
        _make_work_item(title="Critical", priority=WorkItemPriority.CRITICAL)
        _make_work_item(title="Normal", priority=WorkItemPriority.NORMAL)

        resp = self.client.get(QUEUE_URL + "?priority=1", **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 200)
        titles = [item["title"] for item in resp.data["results"]]
        self.assertIn("Critical", titles)
        self.assertNotIn("Normal", titles)


class WorkItemDetailTests(TestCase):
    """Tests for GET /api/v1/workflows/<uuid:pk>/ — single work item detail."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.citizen = _make_citizen()
        self.work_item = _make_work_item(title="Passport Review")

    def test_staff_can_retrieve_work_item(self):
        # Staff must be able to fetch the full detail including history and comments.
        resp = self.client.get(_detail_url(self.work_item.pk), **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(str(resp.data["id"]), str(self.work_item.pk))
        self.assertEqual(resp.data["title"], "Passport Review")
        # Detail serializer must include history and comments arrays
        self.assertIn("history", resp.data)
        self.assertIn("comments", resp.data)

    def test_citizen_gets_403_on_detail(self):
        # Citizens are never permitted to view work items — 403.
        resp = self.client.get(_detail_url(self.work_item.pk), **_bearer(self.client, self.citizen))

        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_detail_returns_401(self):
        # No auth must return 401.
        resp = self.client.get(_detail_url(self.work_item.pk))

        self.assertEqual(resp.status_code, 401)

    def test_nonexistent_pk_returns_404(self):
        # A random UUID not in the DB must return 404.
        resp = self.client.get(_detail_url(uuid.uuid4()), **_bearer(self.client, self.staff))

        self.assertEqual(resp.status_code, 404)


class ClaimWorkItemTests(TestCase):
    """Tests for POST /api/v1/workflows/<uuid:pk>/claim/."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.citizen = _make_citizen()

    def test_staff_can_claim_unassigned_pending_item(self):
        # Claiming an unassigned pending item moves it to IN_PROGRESS.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "claim"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)
        work_item.refresh_from_db()
        self.assertEqual(work_item.status, WorkItemStatus.IN_PROGRESS)
        self.assertEqual(work_item.assigned_to_id, self.staff.pk)

    def test_claim_already_assigned_item_returns_400(self):
        # Claiming an already-assigned item must raise a business-rule 400 error.
        other_staff = _make_staff()
        work_item = _make_work_item(
            status=WorkItemStatus.IN_PROGRESS,
            assigned_to=other_staff,
        )

        resp = self.client.post(
            _action_url(work_item.pk, "claim"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_citizen_claim_returns_403(self):
        # Citizens must never be able to claim work items — 403.
        work_item = _make_work_item()

        resp = self.client.post(
            _action_url(work_item.pk, "claim"),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_claim_returns_401(self):
        # No auth on claim must return 401.
        work_item = _make_work_item()

        resp = self.client.post(_action_url(work_item.pk, "claim"), {}, format="json")

        self.assertEqual(resp.status_code, 401)

    def test_claim_nonexistent_item_returns_404(self):
        # A missing pk on claim must return 404.
        resp = self.client.post(
            _action_url(uuid.uuid4(), "claim"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 404)


class AssignWorkItemTests(TestCase):
    """Tests for POST /api/v1/workflows/<uuid:pk>/assign/."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.staff_assignee = _make_staff()
        self.citizen = _make_citizen()
        self.work_item = _make_work_item(status=WorkItemStatus.PENDING)

    def test_staff_can_assign_to_valid_staff_member(self):
        # A supervisor (staff) must be able to assign a work item to another staff member.
        resp = self.client.post(
            _action_url(self.work_item.pk, "assign"),
            {"assignee_id": str(self.staff_assignee.pk)},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)
        self.work_item.refresh_from_db()
        self.assertEqual(self.work_item.assigned_to_id, self.staff_assignee.pk)

    def test_assign_to_citizen_returns_400(self):
        # Assigning to a non-staff user must be rejected with 400.
        # AssignWorkItemSerializer filters the queryset to is_staff=True,
        # so a citizen PK will fail serializer validation as "object does not exist".
        resp = self.client.post(
            _action_url(self.work_item.pk, "assign"),
            {"assignee_id": str(self.citizen.pk)},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_assign_missing_assignee_id_returns_400(self):
        # Omitting assignee_id must produce a validation error.
        resp = self.client.post(
            _action_url(self.work_item.pk, "assign"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_citizen_assign_returns_403(self):
        # Citizens must receive 403 on the assign endpoint.
        resp = self.client.post(
            _action_url(self.work_item.pk, "assign"),
            {"assignee_id": str(self.staff_assignee.pk)},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 403)


class AdvanceStatusTests(TestCase):
    """Tests for POST /api/v1/workflows/<uuid:pk>/status/ — state machine transitions."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.citizen = _make_citizen()

    def test_valid_transition_pending_to_in_progress(self):
        # PENDING → IN_PROGRESS is a valid transition; must return 200.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": WorkItemStatus.IN_PROGRESS},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)
        work_item.refresh_from_db()
        self.assertEqual(work_item.status, WorkItemStatus.IN_PROGRESS)

    def test_valid_transition_in_progress_to_completed(self):
        # IN_PROGRESS → COMPLETED is valid; sets completed_at timestamp.
        work_item = _make_work_item(status=WorkItemStatus.IN_PROGRESS)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": WorkItemStatus.COMPLETED},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)
        work_item.refresh_from_db()
        self.assertEqual(work_item.status, WorkItemStatus.COMPLETED)
        self.assertIsNotNone(work_item.completed_at)

    def test_invalid_transition_pending_to_completed_returns_400(self):
        # PENDING → COMPLETED is not in VALID_TRANSITIONS; must return 400.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": WorkItemStatus.COMPLETED},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_invalid_transition_from_terminal_status_returns_400(self):
        # COMPLETED → IN_PROGRESS is not allowed; must return 400.
        work_item = _make_work_item(status=WorkItemStatus.COMPLETED)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": WorkItemStatus.IN_PROGRESS},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_invalid_new_status_value_returns_400(self):
        # A value not in WorkItemStatus choices must be rejected by the serializer.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": "flying"},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_status_with_optional_notes_succeeds(self):
        # An optional notes field must be accepted without errors.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": WorkItemStatus.IN_PROGRESS, "notes": "Starting review now."},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)

    def test_citizen_advance_status_returns_403(self):
        # Citizens must not be able to advance a work item's status.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "status"),
            {"new_status": WorkItemStatus.IN_PROGRESS},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 403)


class EscalateWorkItemTests(TestCase):
    """Tests for POST /api/v1/workflows/<uuid:pk>/escalate/."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.citizen = _make_citizen()

    def test_escalate_pending_item_increments_level(self):
        # First escalation must increment escalation_level from 0 to 1.
        work_item = _make_work_item(escalation_level=0)

        resp = self.client.post(
            _action_url(work_item.pk, "escalate"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)
        work_item.refresh_from_db()
        self.assertEqual(work_item.escalation_level, 1)

    def test_escalate_sets_escalated_at_on_first_escalation(self):
        # escalated_at must be set to a non-null timestamp on first escalation.
        work_item = _make_work_item(escalation_level=0)

        self.client.post(
            _action_url(work_item.pk, "escalate"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        work_item.refresh_from_db()
        self.assertIsNotNone(work_item.escalated_at)

    def test_escalate_with_reason_succeeds(self):
        # Optional reason must be accepted and recorded.
        work_item = _make_work_item(escalation_level=0)

        resp = self.client.post(
            _action_url(work_item.pk, "escalate"),
            {"reason": "Citizen has been waiting 7 days."},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)

    def test_escalate_to_level_2_succeeds(self):
        # Second escalation (level 1 → 2, the maximum) must return 200.
        work_item = _make_work_item(escalation_level=1)

        resp = self.client.post(
            _action_url(work_item.pk, "escalate"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 200)
        work_item.refresh_from_db()
        self.assertEqual(work_item.escalation_level, 2)

    def test_escalate_at_max_level_returns_400(self):
        # Escalating beyond level 2 must be rejected with 400.
        work_item = _make_work_item(escalation_level=2)

        resp = self.client.post(
            _action_url(work_item.pk, "escalate"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_escalate_terminal_item_returns_400(self):
        # Escalating a completed item must be rejected with 400.
        work_item = _make_work_item(status=WorkItemStatus.COMPLETED)

        resp = self.client.post(
            _action_url(work_item.pk, "escalate"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_citizen_escalate_returns_403(self):
        # Citizens must receive 403 on escalate — staff-only endpoint.
        work_item = _make_work_item()

        resp = self.client.post(
            _action_url(work_item.pk, "escalate"),
            {},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 403)


class AddCommentTests(TestCase):
    """Tests for POST /api/v1/workflows/<uuid:pk>/comment/."""

    def setUp(self):
        self.client = APIClient()
        self.staff = _make_staff()
        self.citizen = _make_citizen()

    def test_staff_can_add_comment_with_body(self):
        # A valid comment body must be accepted and return 201 with the comment.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": "Awaiting additional documentation from the applicant."},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 201)
        self.assertIn("body", resp.data)
        self.assertIn("author_display", resp.data)

    def test_empty_body_returns_400(self):
        # An empty comment body must be rejected — body is required (min_length=1).
        work_item = _make_work_item()

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": ""},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_missing_body_returns_400(self):
        # Omitting the body field entirely must also be rejected.
        work_item = _make_work_item()

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_comment_on_terminal_item_returns_400(self):
        # Commenting on a completed (terminal) work item must be rejected by the service layer.
        work_item = _make_work_item(status=WorkItemStatus.COMPLETED)

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": "This item is done."},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_comment_on_cancelled_item_returns_400(self):
        # Commenting on a cancelled (terminal) work item must also be rejected.
        work_item = _make_work_item(status=WorkItemStatus.CANCELLED)

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": "Trying to comment on cancelled item."},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)

    def test_citizen_comment_returns_403(self):
        # Citizens must receive 403 on the comment endpoint — staff-only.
        work_item = _make_work_item()

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": "This should fail."},
            format="json",
            **_bearer(self.client, self.citizen),
        )

        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_comment_returns_401(self):
        # No auth must return 401 on the comment endpoint.
        work_item = _make_work_item()

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": "No auth comment."},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    def test_comment_on_nonexistent_work_item_returns_404(self):
        # A missing pk on comment must return 404.
        resp = self.client.post(
            _action_url(uuid.uuid4(), "comment"),
            {"body": "Some comment."},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 404)

    def test_whitespace_only_body_returns_400(self):
        # The service layer strips whitespace; a whitespace body becomes empty and raises ValueError.
        work_item = _make_work_item(status=WorkItemStatus.PENDING)

        resp = self.client.post(
            _action_url(work_item.pk, "comment"),
            {"body": "   "},
            format="json",
            **_bearer(self.client, self.staff),
        )

        self.assertEqual(resp.status_code, 400)
