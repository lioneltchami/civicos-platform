"""Tests for the Forms building block staff views."""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from wagtail.models import Page

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None, is_staff=False, is_superuser=False):
    user = User.objects.create_user(
        email=email or f"u{uuid.uuid4().hex[:8]}@test.ca",
        password=VALID_PASSWORD,
    )
    if is_staff or is_superuser:
        user.is_staff = is_staff or is_superuser
        user.is_superuser = is_superuser
        user.save()
    return user


def make_form_page():
    from apps.forms.models import FormPage

    root_page = Page.objects.filter(depth=1).first()
    if root_page is None:
        root_page = Page.add_root(title="Root", slug="root")
    page = FormPage(
        title="Test Form",
        slug=f"form-{uuid.uuid4().hex[:6]}",
        to_address="",
        from_address="",
        subject="",
    )
    root_page.add_child(instance=page)
    return page


def make_form_field(page, label="Name", is_pii=False):
    from apps.forms.models import FormField

    return FormField.objects.create(
        page=page,
        label=label,
        field_type="singleline",
        required=True,
        is_pii=is_pii,
        sort_order=FormField.objects.filter(page=page).count(),
    )


def make_submission(page, form_data=None, consent_given=True):
    from apps.forms.models import FormSubmission

    return FormSubmission.objects.create(
        page=page,
        form_data=form_data or {"name": "Test"},
        consent_given=consent_given,
        expires_at=timezone.now() + timezone.timedelta(days=365),
    )


class SubmissionListViewTest(TestCase):
    def setUp(self):
        self.staff = make_user(is_staff=True)
        self.citizen = make_user()
        self.page = make_form_page()
        self.sub = make_submission(self.page)
        self.url = f"/forms/submissions/{self.page.pk}/"
        self.client.force_login(self.staff)

    def test_list_loads_for_staff(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_citizen_gets_403(self):
        self.client.force_login(self.citizen)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_redirect(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_list_shows_submissions(self):
        response = self.client.get(self.url)
        self.assertIn(self.sub, response.context["submissions"])

    def test_consent_filter_yes_only_returns_consented(self):
        make_submission(self.page, form_data={"name": "NoConsent"}, consent_given=False)
        response = self.client.get(self.url + "?consent=yes")
        for sub in response.context["submissions"]:
            self.assertTrue(sub.consent_given)

    def test_consent_filter_no_only_returns_not_consented(self):
        make_submission(self.page, form_data={"name": "NoConsent"}, consent_given=False)
        response = self.client.get(self.url + "?consent=no")
        for sub in response.context["submissions"]:
            self.assertFalse(sub.consent_given)

    def test_pii_fields_in_context(self):
        make_form_field(self.page, label="Email", is_pii=True)
        response = self.client.get(self.url)
        self.assertIn("pii_fields", response.context)

    def test_pii_fields_contains_pii_clean_names(self):
        field = make_form_field(self.page, label="Email", is_pii=True)
        response = self.client.get(self.url)
        self.assertIn(field.clean_name, response.context["pii_fields"])

    def test_non_pii_fields_excluded_from_pii_context(self):
        non_pii = make_form_field(self.page, label="Issue", is_pii=False)
        response = self.client.get(self.url)
        self.assertNotIn(non_pii.clean_name, response.context["pii_fields"])

    def test_form_page_in_context(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["form_page"], self.page)

    def test_total_count_in_context(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["total_count"], 1)

    def test_date_from_filter_excludes_older_submissions(self):
        """Submissions before the from-date should be excluded."""
        # The existing sub was just created (today); filter to tomorrow → empty
        tomorrow = (timezone.now() + timezone.timedelta(days=1)).date().isoformat()
        response = self.client.get(self.url + f"?from={tomorrow}")
        self.assertEqual(len(response.context["submissions"]), 0)

    def test_invalid_date_filter_is_silently_ignored(self):
        """Malformed date values should not raise; query should return all submissions."""
        response = self.client.get(self.url + "?from=not-a-date&to=also-bad")
        self.assertEqual(response.status_code, 200)

    def test_list_paginates_at_30_submissions(self):
        """Page 1 should show at most 30 submissions; page 2 should show the rest."""
        # setUp already creates 1 submission; create 30 more = 31 total
        for i in range(30):
            make_submission(self.page, form_data={"name": f"Citizen {i}"})

        response_p1 = self.client.get(self.url)
        self.assertEqual(response_p1.status_code, 200)
        self.assertTrue(response_p1.context["is_paginated"])
        self.assertEqual(len(response_p1.context["submissions"]), 30)

        response_p2 = self.client.get(self.url + "?page=2")
        self.assertEqual(response_p2.status_code, 200)
        self.assertEqual(len(response_p2.context["submissions"]), 1)

    def test_404_for_nonexistent_form_page(self):
        response = self.client.get("/forms/submissions/999999/")
        self.assertEqual(response.status_code, 404)

    def test_correct_template_used(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "forms/staff/submission_list.html")


class SubmissionDetailViewTest(TestCase):
    def setUp(self):
        self.staff = make_user(is_staff=True)
        self.citizen = make_user()
        self.page = make_form_page()
        self.sub = make_submission(self.page)
        self.url = f"/forms/submissions/{self.page.pk}/{self.sub.pk}/"
        self.client.force_login(self.staff)

    def test_detail_loads_for_staff(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_citizen_gets_403(self):
        self.client.force_login(self.citizen)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_redirect(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_wrong_page_returns_404(self):
        other_page = make_form_page()
        response = self.client.get(f"/forms/submissions/{other_page.pk}/{self.sub.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_submission_returns_404(self):
        response = self.client.get(f"/forms/submissions/{self.page.pk}/999999/")
        self.assertEqual(response.status_code, 404)

    def test_submission_in_context(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["submission"], self.sub)

    def test_form_page_in_context(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["form_page"], self.page)

    def test_pii_fields_in_context(self):
        pii_field = make_form_field(self.page, label="Full name", is_pii=True)
        response = self.client.get(self.url)
        self.assertIn(pii_field.clean_name, response.context["pii_fields"])

    def test_form_data_in_context(self):
        sub = make_submission(self.page, form_data={"name": "Alice"})
        url = f"/forms/submissions/{self.page.pk}/{sub.pk}/"
        response = self.client.get(url)
        self.assertEqual(response.context["form_data"], {"name": "Alice"})

    def test_correct_template_used(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "forms/staff/submission_detail.html")


class SubmissionExportViewTest(TestCase):
    def setUp(self):
        self.staff = make_user(is_staff=True)
        self.citizen = make_user()
        self.page = make_form_page()
        make_submission(self.page)
        self.url = f"/forms/submissions/{self.page.pk}/export/"
        self.client.force_login(self.staff)

    def test_export_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_export_returns_csv_content_type(self):
        response = self.client.get(self.url)
        self.assertIn("text/csv", response["Content-Type"])

    def test_export_has_attachment_content_disposition(self):
        response = self.client.get(self.url)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_export_filename_contains_csv_extension(self):
        response = self.client.get(self.url)
        self.assertIn(".csv", response["Content-Disposition"])

    def test_export_filename_contains_form_slug(self):
        response = self.client.get(self.url)
        self.assertIn(self.page.slug, response["Content-Disposition"])

    def test_citizen_gets_403(self):
        self.client.force_login(self.citizen)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_redirect(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_post_returns_405(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 405)

    def test_export_masks_last_two_ip_octets(self):
        from apps.forms.models import FormSubmission

        FormSubmission.objects.filter(page=self.page).update(submitter_ip="192.168.10.25")
        response = self.client.get(self.url)
        content = response.content.decode("utf-8-sig")
        self.assertNotIn("192.168.10.25", content)
        self.assertIn("192.168.x.x", content)

    def test_export_csv_has_header_row(self):
        response = self.client.get(self.url)
        content = response.content.decode("utf-8-sig")
        self.assertIn("ID", content)
        self.assertIn("Submitted at", content)
        self.assertIn("Consent given", content)

    def test_export_includes_field_labels_in_header(self):
        make_form_field(self.page, label="Postal Code")
        response = self.client.get(self.url)
        content = response.content.decode("utf-8-sig")
        self.assertIn("Postal Code", content)

    def test_export_404_for_nonexistent_form_page(self):
        response = self.client.get("/forms/submissions/999999/export/")
        self.assertEqual(response.status_code, 404)

    def test_export_empty_page_has_only_header(self):
        """A form page with no submissions produces a CSV with just the header row."""
        empty_page = make_form_page()
        response = self.client.get(f"/forms/submissions/{empty_page.pk}/export/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8-sig")
        # Only the header row, no data rows
        lines = [l for l in content.splitlines() if l.strip()]  # noqa: E741
        self.assertEqual(len(lines), 1)

    def test_export_ip_is_empty_when_submitter_ip_is_none(self):
        """Submission with no IP should produce an empty cell, not 'masked' or a crash."""
        from apps.forms.models import FormSubmission

        FormSubmission.objects.filter(page=self.page).update(submitter_ip=None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8-sig")
        lines = content.strip().splitlines()
        # Row 1 is header, row 2 is the submission
        self.assertGreater(len(lines), 1)
        data_row = lines[1]
        # The IP column (index 3) should be empty, not "masked" or a traceback
        columns = data_row.split(",")
        self.assertEqual(columns[3].strip(), "")  # IP column is empty


class SubmissionRedactViewTest(TestCase):
    def setUp(self):
        self.staff = make_user(is_staff=True)
        self.citizen = make_user()
        self.page = make_form_page()
        make_form_field(self.page, label="Name", is_pii=True)
        self.sub = make_submission(self.page, form_data={"name": "Alice Smith"})
        self.url = f"/forms/submissions/{self.page.pk}/{self.sub.pk}/redact/"
        self.client.force_login(self.staff)

    def test_redact_replaces_pii_field_value(self):
        self.client.post(self.url)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.form_data.get("name"), "[REDACTED]")

    def test_redact_requires_post_method(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_citizen_cannot_redact(self):
        self.client.force_login(self.citizen)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)
        self.sub.refresh_from_db()
        self.assertNotEqual(self.sub.form_data.get("name"), "[REDACTED]")

    def test_anonymous_cannot_redact(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.sub.refresh_from_db()
        self.assertNotEqual(self.sub.form_data.get("name"), "[REDACTED]")

    def test_redact_redirects_to_list(self):
        response = self.client.post(self.url)
        self.assertRedirects(
            response,
            f"/forms/submissions/{self.page.pk}/",
            fetch_redirect_response=False,
        )

    def test_redact_wrong_page_returns_404(self):
        other_page = make_form_page()
        response = self.client.post(f"/forms/submissions/{other_page.pk}/{self.sub.pk}/redact/")
        self.assertEqual(response.status_code, 404)

    def test_redact_nonexistent_submission_returns_404(self):
        response = self.client.post(f"/forms/submissions/{self.page.pk}/999999/redact/")
        self.assertEqual(response.status_code, 404)

    def test_redact_adds_success_message(self):
        response = self.client.post(self.url)
        # Follow the redirect to get the message
        msgs = list(response.wsgi_request._messages)
        self.assertTrue(any("redacted" in str(m).lower() for m in msgs))

    def test_redact_writes_audit_log_entry(self):
        """A successful redaction must create an AuditLogEntry for compliance."""
        from apps.audit.models import AuditLogEntry

        before_count = AuditLogEntry.objects.filter(event_type="admin.pii.redacted").count()
        self.client.post(self.url)
        after_count = AuditLogEntry.objects.filter(event_type="admin.pii.redacted").count()
        self.assertEqual(after_count, before_count + 1)

    def test_redact_leaves_non_pii_fields_intact(self):
        make_form_field(self.page, label="Issue description", is_pii=False)
        sub = make_submission(
            self.page, form_data={"name": "Bob", "issue_description": "Broken sidewalk"}
        )
        url = f"/forms/submissions/{self.page.pk}/{sub.pk}/redact/"
        self.client.post(url)
        sub.refresh_from_db()
        self.assertEqual(sub.form_data.get("issue_description"), "Broken sidewalk")


class SubmissionDeleteViewTest(TestCase):
    """SubmissionDeleteView requires is_superuser — staff alone are rejected."""

    def setUp(self):
        self.superuser = make_user(is_superuser=True)
        self.staff = make_user(is_staff=True)
        self.citizen = make_user()
        self.page = make_form_page()
        self.sub = make_submission(self.page)
        self.url = f"/forms/submissions/{self.page.pk}/{self.sub.pk}/delete/"

    def test_superuser_can_delete(self):
        from apps.forms.models import FormSubmission

        self.client.force_login(self.superuser)
        pk = self.sub.pk
        self.client.post(self.url)
        self.assertFalse(FormSubmission.objects.filter(pk=pk).exists())

    def test_staff_non_superuser_gets_403(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_citizen_gets_403(self):
        self.client.force_login(self.citizen)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_redirect(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_delete_requires_post(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_delete_redirects_to_list(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url)
        self.assertRedirects(
            response,
            f"/forms/submissions/{self.page.pk}/",
            fetch_redirect_response=False,
        )
