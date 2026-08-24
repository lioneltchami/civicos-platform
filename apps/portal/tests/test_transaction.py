"""
TransactionTestCase tests for portal service on_commit callbacks.

Regular TestCase wraps tests in a savepoint that never commits,
so transaction.on_commit() callbacks never fire. These tests use
TransactionTestCase (which uses real commits with teardown) to
verify that _fire_notification and _write_audit are actually
invoked after a successful commit.

Note: TransactionTestCase is slower than TestCase because it truncates
tables between tests. Keep this file small and focused on the
commit-gated code paths only.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None):
    return User.objects.create_user(
        email=email or f"u{uuid.uuid4().hex[:8]}@test.ca",
        password=VALID_PASSWORD,
    )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class CreateServiceRequestOnCommitTest(TransactionTestCase):
    """
    Verifies that _fire_notification and _write_audit are invoked
    after a real DB commit when create_service_request() succeeds.
    """

    def test_notification_fired_after_commit(self):
        """_fire_notification must be called after the transaction commits."""
        citizen = make_user()
        with patch("apps.portal.services._fire_notification") as mock_notify:
            from apps.portal.models import ServiceRequestStatus
            from apps.portal.services import create_service_request

            sr = create_service_request(citizen, "Pothole Repair", {"desc": "Big one"})
            # on_commit fires immediately after the atomic block exits in
            # TransactionTestCase (real commit happens)
            mock_notify.assert_called_once_with(sr, ServiceRequestStatus.SUBMITTED)

    def test_audit_written_after_commit(self):
        """_write_audit must be called after the transaction commits."""
        citizen = make_user()
        with patch("apps.portal.services._write_audit") as mock_audit:
            from apps.portal.services import create_service_request

            sr = create_service_request(citizen, "Test Service", {})
            mock_audit.assert_called_once_with(sr, "workflow.submission.received", citizen)

    def test_notification_not_fired_if_transaction_fails(self):
        """
        If the database transaction rolls back, on_commit callbacks
        must NOT fire — the citizen must not receive a spurious email
        for a request that doesn't exist.
        """
        citizen = make_user()
        with patch("apps.portal.services._fire_notification") as mock_notify:
            from django.db import transaction

            from apps.portal.services import create_service_request

            try:
                with transaction.atomic():
                    create_service_request(citizen, "Test", {})
                    raise ValueError("Simulated DB error — roll back")
            except ValueError:
                pass

            # on_commit must NOT have been called — the transaction rolled back
            mock_notify.assert_not_called()

    def test_reference_number_persisted_before_on_commit(self):
        """
        The ServiceRequest record (with reference number) must be in the DB
        before on_commit fires — so the notification task can fetch it.
        """
        citizen = make_user()
        from apps.portal.models import ServiceRequest
        from apps.portal.services import create_service_request

        with patch("apps.portal.services._fire_notification"):
            with patch("apps.portal.services._write_audit"):
                sr = create_service_request(citizen, "Test", {})

        # Record must exist in DB
        fetched = ServiceRequest.objects.get(pk=sr.pk)
        self.assertIsNotNone(fetched.reference_number)
        self.assertTrue(fetched.reference_number.startswith("GS-"))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class UpdateRequestStatusOnCommitTest(TransactionTestCase):
    """
    Verifies that status-change notifications and audit entries
    fire after commit in update_request_status().
    """

    def _make_submitted_request(self):
        citizen = make_user()
        with patch("apps.portal.services._fire_notification"):
            with patch("apps.portal.services._write_audit"):
                from apps.portal.services import create_service_request

                sr = create_service_request(citizen, "Test Service", {})
        return sr, citizen

    def test_notification_fired_on_status_update(self):
        sr, citizen = self._make_submitted_request()
        with patch("apps.portal.services._fire_notification") as mock_notify:
            with patch("apps.portal.services._write_audit"):
                from apps.portal.models import ServiceRequestStatus
                from apps.portal.services import update_request_status

                update_request_status(sr, ServiceRequestStatus.IN_REVIEW, citizen, "Under review")
            mock_notify.assert_called_once_with(sr, ServiceRequestStatus.IN_REVIEW)

    def test_audit_written_on_status_update(self):
        sr, citizen = self._make_submitted_request()
        with patch("apps.portal.services._write_audit") as mock_audit:
            with patch("apps.portal.services._fire_notification"):
                from apps.portal.models import ServiceRequestStatus
                from apps.portal.services import update_request_status

                update_request_status(sr, ServiceRequestStatus.IN_REVIEW, citizen, "Under review")
            mock_audit.assert_called_once_with(
                sr,
                "workflow.status.changed",
                citizen,
                detail={
                    "old_status": ServiceRequestStatus.SUBMITTED,
                    "new_status": ServiceRequestStatus.IN_REVIEW,
                },
            )

    def test_no_notification_if_status_update_rolls_back(self):
        sr, citizen = self._make_submitted_request()
        with patch("apps.portal.services._fire_notification") as mock_notify:
            with patch("apps.portal.services._write_audit"):
                from django.db import transaction

                from apps.portal.models import ServiceRequestStatus
                from apps.portal.services import update_request_status

                try:
                    with transaction.atomic():
                        update_request_status(sr, ServiceRequestStatus.IN_REVIEW, citizen)
                        raise ValueError("Simulated failure")
                except ValueError:
                    pass

                mock_notify.assert_not_called()

    def test_status_update_record_persisted_before_on_commit(self):
        """StatusUpdate must be in DB before on_commit fires."""
        sr, citizen = self._make_submitted_request()
        with patch("apps.portal.services._fire_notification"):
            with patch("apps.portal.services._write_audit"):
                from apps.portal.models import ServiceRequestStatus, StatusUpdate
                from apps.portal.services import update_request_status

                initial_count = StatusUpdate.objects.filter(service_request=sr).count()
                update_request_status(sr, ServiceRequestStatus.IN_REVIEW, citizen)

        self.assertEqual(StatusUpdate.objects.filter(service_request=sr).count(), initial_count + 1)
