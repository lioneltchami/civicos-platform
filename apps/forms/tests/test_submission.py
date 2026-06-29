"""
End-to-end tests for the form submission flow.
Tests FormPage.process_form_submission(), consent tracking,
retention date setting, and signal emission.
"""
import uuid
from unittest.mock import patch, MagicMock, call
from django.test import TestCase, override_settings
from django.utils import timezone
from wagtail.models import Page

VALID_PASSWORD = "SecureTestPass123!"


def make_form_page(consent_text="", retention_days=365):
    from apps.forms.models import FormPage
    root_page = Page.objects.filter(depth=1).first()
    if root_page is None:
        root_page = Page.add_root(title="Root", slug="root")
    page = FormPage(
        title=f"Form {uuid.uuid4().hex[:4]}",
        slug=f"form-{uuid.uuid4().hex[:6]}",
        consent_text=consent_text,
        retention_days=retention_days,
        to_address="",
        from_address="",
        subject="",
    )
    root_page.add_child(instance=page)
    return page


def make_form_field(page, label, is_pii=False):
    from apps.forms.models import FormField
    return FormField.objects.create(
        page=page, label=label, field_type="singleline",
        required=True, is_pii=is_pii,
        sort_order=FormField.objects.filter(page=page).count(),
    )


def _make_real_submission(page, form_data=None):
    """Create a persisted FormSubmission for patching the parent's return value."""
    from apps.forms.models import FormSubmission
    return FormSubmission.objects.create(
        page=page,
        form_data=form_data or {"name": "Test Citizen"},
        consent_given=False,
    )


def _make_mock_form(cleaned_data=None, request=None):
    """Build a lightweight mock of the Wagtail-generated bound form object."""
    form = MagicMock()
    form.cleaned_data = cleaned_data or {"name": "Test Citizen"}
    form.request = request
    return form


class ProcessFormSubmissionTest(TestCase):
    """Tests for FormPage.process_form_submission() called directly."""

    _PARENT = "wagtail.contrib.forms.models.AbstractEmailForm.process_form_submission"

    def test_submission_is_returned(self):
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(_make_mock_form())
        self.assertIsNotNone(result.pk)

    def test_expires_at_set_from_retention_days(self):
        page = make_form_page(retention_days=180)
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        before = timezone.now()
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(_make_mock_form())
        result.refresh_from_db()
        self.assertIsNotNone(result.expires_at)
        expected = before + timezone.timedelta(days=180)
        delta = abs((result.expires_at - expected).total_seconds())
        self.assertLess(delta, 5)  # Within 5 seconds of expected

    def test_expires_at_respects_custom_retention(self):
        for days in (30, 90, 730):
            with self.subTest(days=days):
                page = make_form_page(retention_days=days)
                make_form_field(page, label="Name")
                real_sub = _make_real_submission(page)
                before = timezone.now()
                with patch(self._PARENT, return_value=real_sub):
                    result = page.process_form_submission(_make_mock_form())
                result.refresh_from_db()
                expected_delta = (result.expires_at - before).total_seconds()
                self.assertAlmostEqual(
                    expected_delta, days * 86400, delta=10
                )

    def test_submitter_ip_captured_from_remote_addr(self):
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        mock_request = MagicMock()
        mock_request.META = {"REMOTE_ADDR": "10.0.0.1"}
        mock_form = _make_mock_form(request=mock_request)
        # SECURE_PROXY_SSL_HEADER not set → use REMOTE_ADDR
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(mock_form)
        result.refresh_from_db()
        self.assertEqual(result.submitter_ip, "10.0.0.1")

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_submitter_ip_uses_x_forwarded_for_behind_proxy(self):
        """When SECURE_PROXY_SSL_HEADER is set, use X-Forwarded-For (first IP)."""
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        mock_request = MagicMock()
        mock_request.META = {
            "REMOTE_ADDR": "10.0.0.1",
            "HTTP_X_FORWARDED_FOR": "203.0.113.5, 10.0.0.1",
        }
        mock_form = _make_mock_form(request=mock_request)
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(mock_form)
        result.refresh_from_db()
        self.assertEqual(result.submitter_ip, "203.0.113.5")

    def test_submitter_ip_none_when_no_request(self):
        """If no request is attached to the form, submitter_ip should remain None."""
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        mock_form = _make_mock_form()
        mock_form.request = None
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(mock_form)
        result.refresh_from_db()
        self.assertIsNone(result.submitter_ip)

    def test_consent_recorded_when_consent_text_set(self):
        page = make_form_page(consent_text="I agree to data collection.")
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        mock_form = _make_mock_form(cleaned_data={"name": "Test", "_consent": True})
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(mock_form)
        result.refresh_from_db()
        self.assertTrue(result.consent_given)
        self.assertEqual(result.consent_text_shown, "I agree to data collection.")

    def test_consent_not_recorded_when_no_consent_text(self):
        """When consent_text is blank, consent_given must remain False."""
        page = make_form_page(consent_text="")
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        mock_form = _make_mock_form(cleaned_data={"name": "Test"})
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(mock_form)
        result.refresh_from_db()
        self.assertFalse(result.consent_given)
        self.assertEqual(result.consent_text_shown, "")

    def test_consent_text_snapshot_matches_page_consent_text(self):
        """Snapshot of consent text at submission time must equal the page's consent_text."""
        consent = "Privacy notice: your data is used for X purposes only."
        page = make_form_page(consent_text=consent)
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)
        mock_form = _make_mock_form(cleaned_data={"name": "Test", "_consent": True})
        with patch(self._PARENT, return_value=real_sub):
            result = page.process_form_submission(mock_form)
        result.refresh_from_db()
        self.assertEqual(result.consent_text_shown, consent)

    def test_form_submission_received_signal_emitted(self):
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)

        signal_calls = []
        from apps.core.signals import form_submission_received

        def handler(sender, form_page, submission, request, **kwargs):
            signal_calls.append({"form_page": form_page, "submission": submission})

        form_submission_received.connect(handler)
        try:
            with patch(self._PARENT, return_value=real_sub):
                page.process_form_submission(_make_mock_form())
            self.assertEqual(len(signal_calls), 1)
            self.assertEqual(signal_calls[0]["form_page"], page)
            self.assertEqual(signal_calls[0]["submission"], real_sub)
        finally:
            form_submission_received.disconnect(handler)

    def test_signal_emitted_with_correct_sender(self):
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)

        senders = []
        from apps.core.signals import form_submission_received

        def handler(sender, **kwargs):
            senders.append(sender)

        form_submission_received.connect(handler)
        try:
            with patch(self._PARENT, return_value=real_sub):
                page.process_form_submission(_make_mock_form())
            from apps.forms.models import FormPage
            self.assertEqual(senders[0], FormPage)
        finally:
            form_submission_received.disconnect(handler)

    def test_signal_failure_does_not_break_submission(self):
        """Signal handler exception must be caught; the citizen-facing form must not error."""
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)

        with patch(
            "apps.core.signals.form_submission_received.send_robust",
            side_effect=Exception("handler exploded"),
        ):
            with patch(self._PARENT, return_value=real_sub):
                # Should NOT raise
                result = page.process_form_submission(_make_mock_form())
        self.assertIsNotNone(result.pk)

    def test_signal_not_emitted_if_parent_raises(self):
        """If the parent process_form_submission fails, the signal must not fire."""
        page = make_form_page()
        make_form_field(page, label="Name")

        signal_calls = []
        from apps.core.signals import form_submission_received

        def handler(**kwargs):
            signal_calls.append(kwargs)

        form_submission_received.connect(handler)
        try:
            with patch(self._PARENT, side_effect=RuntimeError("DB failure")):
                with self.assertRaises(RuntimeError):
                    page.process_form_submission(_make_mock_form())
            self.assertEqual(len(signal_calls), 0)
        finally:
            form_submission_received.disconnect(handler)

    def test_save_called_with_correct_update_fields(self):
        """
        process_form_submission() must call save() with the four extended fields
        to avoid clobbering concurrent updates on other fields.
        """
        page = make_form_page()
        make_form_field(page, label="Name")
        real_sub = _make_real_submission(page)

        with patch(self._PARENT, return_value=real_sub):
            with patch.object(real_sub, "save") as mock_save:
                page.process_form_submission(_make_mock_form())
        mock_save.assert_called_once_with(
            update_fields=["submitter_ip", "consent_given", "consent_text_shown", "expires_at"]
        )


class FormPageServingTest(TestCase):
    """
    Tests for the FormPage.serve() override that injects request onto the form.
    Uses the Django test client to POST to the Wagtail page URL.
    """

    def setUp(self):
        from wagtail.models import Site
        from django.core.cache import cache
        root_page = Page.objects.filter(depth=1).first()
        if root_page is None:
            root_page = Page.add_root(title="Root", slug="root")
        self.root_page = root_page
        # Ensure a Wagtail Site exists pointing to root
        Site.objects.all().delete()
        Site.objects.create(
            hostname="localhost",
            port=80,
            root_page=root_page,
            is_default_site=True,
        )
        # Clear Wagtail's site root paths cache so the new site is visible
        cache.clear()

    def _make_live_form_page(self, consent_text="", retention_days=365):
        """Create a published FormPage under root."""
        from apps.forms.models import FormPage
        page = FormPage(
            title="Live Form",
            slug=f"live-form-{uuid.uuid4().hex[:6]}",
            consent_text=consent_text,
            retention_days=retention_days,
            to_address="",
            from_address="",
            subject="",
            live=True,
        )
        self.root_page.add_child(instance=page)
        return page

    def test_get_renders_form_page(self):
        page = self._make_live_form_page()
        make_form_field(page, label="Name")
        response = self.client.get(page.url)
        self.assertEqual(response.status_code, 200)

    def test_form_in_context_on_get(self):
        page = self._make_live_form_page()
        make_form_field(page, label="Name")
        response = self.client.get(page.url)
        self.assertIn("form", response.context)

    def test_valid_post_creates_submission(self):
        from apps.forms.models import FormSubmission
        page = self._make_live_form_page()
        make_form_field(page, label="Name")
        count_before = FormSubmission.objects.filter(page=page).count()
        self.client.post(page.url, data={"name": "Test Citizen"})
        self.assertEqual(
            FormSubmission.objects.filter(page=page).count(),
            count_before + 1,
        )

    def test_valid_post_sets_expires_at(self):
        from apps.forms.models import FormSubmission
        page = self._make_live_form_page(retention_days=90)
        make_form_field(page, label="Name")
        self.client.post(page.url, data={"name": "Test Citizen"})
        sub = FormSubmission.objects.filter(page=page).latest("submit_time")
        self.assertIsNotNone(sub.expires_at)

    def test_valid_post_captures_submitter_ip(self):
        from apps.forms.models import FormSubmission
        page = self._make_live_form_page()
        make_form_field(page, label="Name")
        self.client.post(
            page.url,
            data={"name": "Test Citizen"},
            REMOTE_ADDR="127.0.0.1",
        )
        sub = FormSubmission.objects.filter(page=page).latest("submit_time")
        self.assertEqual(sub.submitter_ip, "127.0.0.1")

    def test_post_with_consent_records_consent(self):
        from apps.forms.models import FormSubmission
        page = self._make_live_form_page(consent_text="I agree.")
        make_form_field(page, label="Name")
        self.client.post(page.url, data={"name": "Test", "_consent": True})
        sub = FormSubmission.objects.filter(page=page).latest("submit_time")
        self.assertTrue(sub.consent_given)
        self.assertEqual(sub.consent_text_shown, "I agree.")

    def test_post_without_consent_does_not_submit(self):
        """Missing required consent checkbox should cause form validation to fail."""
        from apps.forms.models import FormSubmission
        page = self._make_live_form_page(consent_text="I agree.")
        make_form_field(page, label="Name")
        count_before = FormSubmission.objects.filter(page=page).count()
        # Omit 'consent' from POST data
        self.client.post(page.url, data={"name": "Test"})
        # No new submission should be created
        self.assertEqual(
            FormSubmission.objects.filter(page=page).count(),
            count_before,
        )

    def test_valid_post_redirects_to_landing_page(self):
        page = self._make_live_form_page()
        make_form_field(page, label="Name")
        response = self.client.post(page.url, data={"name": "Test"})
        # Wagtail's default behaviour is to render the landing page (200),
        # not a redirect, but this depends on render_landing_page() implementation.
        # Accept either 200 (rendered landing) or 302 (redirect to landing).
        self.assertIn(response.status_code, [200, 302])
