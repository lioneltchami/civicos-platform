"""Tests for backoffice work item queue views."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.workflows.models import (
    WorkItem,
    WorkItemHistory,
    WorkItemPriority,
    WorkItemStatus,
)

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def _make_staff(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password=VALID_PASSWORD,
        is_staff=True,
    )


def _make_citizen(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password=VALID_PASSWORD,
        is_staff=False,
    )


def _make_work_item(status: str = WorkItemStatus.PENDING, assigned_to=None) -> WorkItem:
    """Create a minimal WorkItem for testing.

    WorkItem requires a content_type + object_id (GenericForeignKey).
    We use the User model as a stand-in content type for test simplicity.
    """
    ct = ContentType.objects.get_for_model(User)
    sentinel_user = User.objects.first()
    return WorkItem.objects.create(
        title="Test work item",
        description="Test description",
        status=status,
        priority=WorkItemPriority.NORMAL,
        content_type=ct,
        object_id=str(sentinel_user.pk) if sentinel_user else "1",
        assigned_to=assigned_to,
    )


# ---------------------------------------------------------------------------
# WorkItemListView
# ---------------------------------------------------------------------------


class WorkItemListTests(TestCase):
    def setUp(self):
        self.staff = _make_staff("staff@example.gov")
        self.citizen = _make_citizen("citizen@example.gov")

    def _url(self):
        return reverse("backoffice:wi-list")

    def test_requires_staff(self):
        """Unauthenticated users are redirected to login."""
        resp = self.client.get(self._url())
        self.assertRedirects(
            resp, f"/account/login/?next={self._url()}", fetch_redirect_response=False
        )

    def test_requires_staff_authenticated_citizen_gets_403(self):
        """Authenticated non-staff users get 403."""
        self.client.force_login(self.citizen)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_staff_sees_queue(self):
        """Staff user gets 200 and work_items in context."""
        _make_work_item()
        self.client.force_login(self.staff)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("work_items", resp.context)

    def test_mine_filter(self):
        """?mine=1 returns only items assigned to the current user."""
        mine = _make_work_item(assigned_to=self.staff)
        other_staff = _make_staff("other@example.gov")
        theirs = _make_work_item(assigned_to=other_staff)

        self.client.force_login(self.staff)
        resp = self.client.get(self._url(), {"mine": "1"})
        self.assertEqual(resp.status_code, 200)
        pks = [wi.pk for wi in resp.context["work_items"]]
        self.assertIn(mine.pk, pks)
        self.assertNotIn(theirs.pk, pks)

    def test_status_filter(self):
        """?status=pending returns only pending items."""
        pending = _make_work_item(status=WorkItemStatus.PENDING)
        _make_work_item(status=WorkItemStatus.COMPLETED)

        self.client.force_login(self.staff)
        resp = self.client.get(self._url(), {"status": "pending"})
        self.assertEqual(resp.status_code, 200)
        pks = [wi.pk for wi in resp.context["work_items"]]
        self.assertIn(pending.pk, pks)
        for wi in resp.context["work_items"]:
            self.assertEqual(wi.status, WorkItemStatus.PENDING)


# ---------------------------------------------------------------------------
# WorkItemDetailView
# ---------------------------------------------------------------------------


class WorkItemDetailTests(TestCase):
    def setUp(self):
        self.staff = _make_staff("staff@example.gov")
        self.citizen = _make_citizen("citizen@example.gov")
        self.wi = _make_work_item()
        self.url = self._url(self.wi.pk)

    def _url(self, pk):
        return reverse("backoffice:wi-detail", kwargs={"pk": pk})

    def test_unauthenticated_redirects(self):
        """Unauthenticated GET redirects to login."""
        resp = self.client.get(self.url)
        self.assertRedirects(
            resp,
            f"/account/login/?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives 403."""
        self.client.force_login(self.citizen)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_detail_200(self):
        """Staff user can view work item detail."""
        wi = _make_work_item()
        self.client.force_login(self.staff)
        resp = self.client.get(self._url(wi.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("work_item", resp.context)

    def test_nonexistent_404(self):
        """Non-existent UUID returns 404."""
        import uuid

        self.client.force_login(self.staff)
        resp = self.client.get(self._url(uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    def test_history_shown(self):
        """History entries appear in context."""
        wi = _make_work_item()
        WorkItemHistory.objects.create(
            work_item=wi,
            action="claimed",
            old_status="",
            new_status="in_progress",
            actor=self.staff,
            notes="",
        )
        self.client.force_login(self.staff)
        resp = self.client.get(self._url(wi.pk))
        self.assertEqual(resp.status_code, 200)
        history = list(resp.context["history"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].action, "claimed")


# ---------------------------------------------------------------------------
# WorkItemClaimView
# ---------------------------------------------------------------------------


class WorkItemClaimTests(TestCase):
    def setUp(self):
        self.staff = _make_staff("staff@example.gov")
        self.citizen = _make_citizen("citizen@example.gov")
        self.wi = _make_work_item(status=WorkItemStatus.PENDING)
        self.url = self._url(self.wi.pk)

    def _url(self, pk):
        return reverse("backoffice:wi-claim", kwargs={"pk": pk})

    def test_unauthenticated_redirects(self):
        """Unauthenticated POST redirects to login."""
        resp = self.client.post(self.url)
        self.assertRedirects(
            resp,
            f"/account/login/?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives 403."""
        self.client.force_login(self.citizen)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_claim_succeeds(self):
        """POST to wi-claim calls claim_work_item and redirects to detail."""
        wi = _make_work_item(status=WorkItemStatus.PENDING)

        with patch("apps.backoffice.views.work_items.claim_work_item") as mock_claim:
            mock_claim.return_value = wi
            self.client.force_login(self.staff)
            resp = self.client.post(self._url(wi.pk))

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )
        mock_claim.assert_called_once_with(wi, self.staff)

    def test_claim_already_assigned_shows_error(self):
        """When claim_work_item raises ValueError, an error message is shown."""
        other_staff = _make_staff("other@example.gov")
        wi = _make_work_item(status=WorkItemStatus.IN_PROGRESS, assigned_to=other_staff)

        with patch(
            "apps.backoffice.views.work_items.claim_work_item",
            side_effect=ValueError("Already assigned"),
        ):
            self.client.force_login(self.staff)
            resp = self.client.post(self._url(wi.pk))

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )
        # Follow redirect to read messages
        resp2 = self.client.get(reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}))
        messages = list(resp2.context["messages"])
        self.assertTrue(any("Already assigned" in str(m) for m in messages))


# ---------------------------------------------------------------------------
# WorkItemStatusView
# ---------------------------------------------------------------------------


class WorkItemStatusTests(TestCase):
    def setUp(self):
        self.staff = _make_staff("staff@example.gov")
        self.citizen = _make_citizen("citizen@example.gov")
        self.wi = _make_work_item(status=WorkItemStatus.PENDING)
        self.url = self._url(self.wi.pk)

    def _url(self, pk):
        return reverse("backoffice:wi-status", kwargs={"pk": pk})

    def test_unauthenticated_redirects(self):
        """Unauthenticated POST redirects to login."""
        resp = self.client.post(self.url, {"new_status": WorkItemStatus.IN_PROGRESS, "notes": ""})
        self.assertRedirects(
            resp,
            f"/account/login/?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives 403."""
        self.client.force_login(self.citizen)
        resp = self.client.post(self.url, {"new_status": WorkItemStatus.IN_PROGRESS, "notes": ""})
        self.assertEqual(resp.status_code, 403)

    def test_advance_status_succeeds(self):
        """pending → in_progress is a valid transition; service is called."""
        wi = _make_work_item(status=WorkItemStatus.PENDING)

        with patch("apps.backoffice.views.work_items.update_work_item_status") as mock_update:
            mock_update.return_value = wi
            self.client.force_login(self.staff)
            resp = self.client.post(
                self._url(wi.pk),
                {"new_status": WorkItemStatus.IN_PROGRESS, "notes": "Moving forward"},
            )

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )
        mock_update.assert_called_once_with(
            wi, WorkItemStatus.IN_PROGRESS, self.staff, notes="Moving forward"
        )

    def test_invalid_transition_shows_error(self):
        """pending → completed is not valid; service is NOT called."""
        wi = _make_work_item(status=WorkItemStatus.PENDING)

        with patch("apps.backoffice.views.work_items.update_work_item_status") as mock_update:
            self.client.force_login(self.staff)
            resp = self.client.post(
                self._url(wi.pk),
                {"new_status": WorkItemStatus.COMPLETED, "notes": ""},
            )
            mock_update.assert_not_called()

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )


# ---------------------------------------------------------------------------
# WorkItemCommentView
# ---------------------------------------------------------------------------


class WorkItemCommentTests(TestCase):
    def setUp(self):
        self.staff = _make_staff("staff@example.gov")
        self.citizen = _make_citizen("citizen@example.gov")
        self.wi = _make_work_item()
        self.url = self._url(self.wi.pk)

    def _url(self, pk):
        return reverse("backoffice:wi-comment", kwargs={"pk": pk})

    def test_unauthenticated_redirects(self):
        """Unauthenticated POST redirects to login."""
        resp = self.client.post(self.url, {"body": "test"})
        self.assertRedirects(
            resp,
            f"/account/login/?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives 403."""
        self.client.force_login(self.citizen)
        resp = self.client.post(self.url, {"body": "test"})
        self.assertEqual(resp.status_code, 403)

    def test_add_comment_succeeds(self):
        """Valid body calls add_comment and redirects."""
        wi = _make_work_item()

        with patch("apps.backoffice.views.work_items.add_comment") as mock_add:
            mock_add.return_value = None
            self.client.force_login(self.staff)
            resp = self.client.post(self._url(wi.pk), {"body": "This needs attention."})

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )
        mock_add.assert_called_once_with(wi, self.staff, "This needs attention.")

    def test_empty_comment_rejected(self):
        """Empty body does not call add_comment."""
        wi = _make_work_item()

        with patch("apps.backoffice.views.work_items.add_comment") as mock_add:
            self.client.force_login(self.staff)
            resp = self.client.post(self._url(wi.pk), {"body": "   "})
            mock_add.assert_not_called()

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )


# ---------------------------------------------------------------------------
# WorkItemAssignView
# ---------------------------------------------------------------------------


class WorkItemAssignTests(TestCase):
    def setUp(self):
        self.staff = _make_staff("staff@example.gov")
        self.citizen = _make_citizen("citizen@example.gov")

    def _url(self, pk):
        return reverse("backoffice:wi-assign", kwargs={"pk": pk})

    def test_unauthenticated_redirects(self):
        """Unauthenticated GET to assign URL redirects to login."""
        wi = _make_work_item()
        resp = self.client.get(self._url(wi.pk))
        self.assertRedirects(
            resp,
            f"/account/login/?next={self._url(wi.pk)}",
            fetch_redirect_response=False,
        )

    def test_citizen_gets_403(self):
        """Authenticated non-staff citizen receives 403 on POST."""
        wi = _make_work_item()
        self.client.force_login(self.citizen)
        resp = self.client.post(self._url(wi.pk), {"assignee_id": str(self.staff.pk)})
        self.assertEqual(resp.status_code, 403)

    def test_assign_to_staff_succeeds(self):
        """POST with valid assignee_id calls assign_work_item and redirects to detail."""
        wi = _make_work_item()
        assignee = _make_staff("assignee@example.gov")

        with patch("apps.backoffice.views.work_items.assign_work_item") as mock_assign:
            mock_assign.return_value = wi
            self.client.force_login(self.staff)
            resp = self.client.post(self._url(wi.pk), {"assignee_id": str(assignee.pk)})

        self.assertRedirects(
            resp,
            reverse("backoffice:wi-detail", kwargs={"pk": wi.pk}),
            fetch_redirect_response=False,
        )
        mock_assign.assert_called_once_with(wi, assignee, self.staff)

    def test_assign_to_nonexistent_staff_shows_error(self):
        """POST with invalid assignee_id (non-existent user) returns 404."""
        wi = _make_work_item()
        self.client.force_login(self.staff)
        resp = self.client.post(self._url(wi.pk), {"assignee_id": "99999"})
        # get_object_or_404 on a non-existent staff user returns 404
        self.assertEqual(resp.status_code, 404)
