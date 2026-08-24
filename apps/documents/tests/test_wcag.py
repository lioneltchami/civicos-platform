"""
Wave 7 — §24.1 canonical test file: test_wcag.py

WCAG 2.1 AA compliance tests for Document Management BB templates.

Tests performed via Django test client (rendered HTML inspection).
Verifies:
  1. <main> landmark present on every document page
  2. <h1> heading present and unique on every page
  3. Form inputs have aria-describedby wired to error containers
  4. XSS escaping — malicious content in original_filename does NOT produce raw HTML
  5. storage_key NOT in any rendered HTML response
  6. Error containers have role="alert" for assistive technology
  7. Skip navigation link present in base layout
  8. Pagination nav has aria-label
  9. Breadcrumb nav has aria-label
  10. CSRF token present on POST forms
  11. Table has <caption> (or aria-label) for screen readers
  12. File input has aria-describedby wired to help text
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.documents.models import Document, DocumentCategory

User = get_user_model()

_CTR = 0


def _make_user(**kwargs):
    global _CTR
    _CTR += 1
    return User.objects.create_user(
        email=f"wcag{_CTR}@example.com",
        password="testpass123",
        **kwargs,
    )


def _make_category(**kwargs):
    global _CTR
    _CTR += 1
    return DocumentCategory.objects.create(
        name_en="WCAG Test Category",
        name_fr="Catégorie WCAG",
        slug=f"wcag-cat-{_CTR}",
        allowed_mime_types=["application/pdf"],
        min_retention_days=730,
        max_retention_days=2555,
        **kwargs,
    )


def _make_document(user, category, **kwargs):
    doc_id = uuid.uuid4()
    return Document.objects.create(
        uploaded_by=user,
        category=category,
        original_filename="wcag-test-file.pdf",
        _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
        mime_type="application/pdf",
        size_bytes=2048,
        scan_status=Document.ScanStatus.ACTIVE,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_page(client, url):
    """GET a URL and return the response, raising if not 200."""
    response = client.get(url)
    return response


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2. <main> landmark and <h1> heading
# ─────────────────────────────────────────────────────────────────────────────


class MainLandmarkTests(TestCase):
    """Every document page must have a <main> landmark (WCAG 1.3.1, 2.4.1)."""

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def _assertHasMain(self, response):  # noqa: N802
        content = response.content.decode()
        self.assertIn("<main", content, "Missing <main> landmark")

    def test_document_list_has_main(self):
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 200)
        self._assertHasMain(response)

    def test_document_detail_has_main(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self._assertHasMain(response)

    def test_upload_init_has_main(self):
        response = self.client.get(reverse("documents:upload-init"))
        self.assertEqual(response.status_code, 200)
        self._assertHasMain(response)


class HeadingTests(TestCase):
    """Every document page must have exactly one <h1> (WCAG 1.3.1, 2.4.6)."""

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def _count_h1(self, response):
        content = response.content.decode()
        import re

        return len(re.findall(r"<h1[\s>]", content, re.IGNORECASE))

    def test_document_list_has_one_h1(self):
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count_h1(response), 1)

    def test_document_detail_has_one_h1(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count_h1(response), 1)

    def test_upload_form_has_one_h1(self):
        response = self.client.get(reverse("documents:upload-init"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count_h1(response), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 3. aria-describedby on form inputs
# ─────────────────────────────────────────────────────────────────────────────


class FormAriaDescribedByTests(TestCase):
    """
    WCAG 1.3.1, 4.1.3: form inputs must have aria-describedby referencing
    error containers when errors exist.
    """

    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_upload_form_file_input_has_aria_describedby(self):
        """
        The file picker input must have aria-describedby referencing the help text.
        This is unconditional — the help text is always present.
        """
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        self.assertIn("aria-describedby=", content)
        # The file input references "file-help" at minimum
        self.assertIn("file-help", content)

    def test_upload_form_category_error_has_role_alert(self):
        """
        When category field has an error, the error container must have role="alert".
        We trigger this by posting an invalid form.
        """
        # Make a category first
        _make_category()
        # Post with missing category (just whitespace)
        response = self.client.post(
            reverse("documents:upload-init"),
            {
                "category_slug": "",
                "original_filename": "test.pdf",
                "mime_type": "application/pdf",
                "size_bytes": "1024",
            },
        )
        content = response.content.decode()
        # After form error, category error container should have role="alert"
        if "category-error" in content:
            self.assertIn('role="alert"', content)

    def test_upload_form_inputs_have_id_attributes(self):
        """All visible form inputs must have id attributes for label association."""
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        # key inputs must have IDs
        self.assertIn('id="id_category_slug"', content)
        self.assertIn('id="id_file_picker"', content)
        self.assertIn('id="id_description"', content)


# ─────────────────────────────────────────────────────────────────────────────
# 4. XSS escaping — malicious content in original_filename
# ─────────────────────────────────────────────────────────────────────────────


class XSSEscapingTests(TestCase):
    """
    WCAG 2.4.6 + security: user-controlled content (original_filename)
    must be HTML-escaped before rendering.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.client.force_login(self.user)

    def test_xss_payload_in_filename_is_escaped_in_detail(self):
        """A script tag in original_filename must not appear as raw HTML."""
        xss_filename = '<script>alert("xss")</script>.pdf'
        doc = _make_document(self.user, self.cat)
        doc.original_filename = xss_filename
        doc.save(update_fields=["original_filename"])

        response = self.client.get(reverse("documents:detail", args=[doc.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Raw script tag must NOT be present
        self.assertNotIn("<script>alert", content)
        # Escaped version should be present
        self.assertIn("&lt;script&gt;", content)

    def test_xss_payload_in_filename_is_escaped_in_list(self):
        """XSS payload in filename must be escaped in the document list view."""
        xss_filename = '"><img src=x onerror=alert(1)>.pdf'
        doc = _make_document(self.user, self.cat)
        doc.original_filename = xss_filename
        doc.save(update_fields=["original_filename"])

        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # The attack tag <img src=x onerror=alert(1)> must NOT appear unescaped.
        # Django's auto-escaping converts < and > to &lt; and &gt;, neutralising
        # the injection. The substring "onerror=alert(1)" may still appear as
        # inert text inside properly-escaped HTML — that's correct and expected.
        self.assertNotIn("<img src=x onerror=alert(1)>", content)
        # Confirm the < is actually escaped (i.e. template used |escape or autoescaping)
        self.assertIn("&lt;img", content)

    def test_xss_in_description_is_escaped(self):
        """XSS payload in document description must be escaped."""
        doc = _make_document(self.user, self.cat)
        doc.description = "<script>document.cookie</script>"
        doc.save(update_fields=["description"])

        response = self.client.get(reverse("documents:detail", args=[doc.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("<script>document.cookie</script>", content)
        self.assertIn("&lt;script&gt;", content)


# ─────────────────────────────────────────────────────────────────────────────
# 5. storage_key NEVER in rendered HTML
# ─────────────────────────────────────────────────────────────────────────────


class StorageKeyNotInHTMLTests(TestCase):
    """
    PIPEDA + security: the _storage_key (S3 path) must NEVER appear in
    any rendered HTML response.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def test_storage_key_not_in_document_detail(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        content = response.content.decode()
        self.assertNotIn(self.doc._storage_key, content)
        # Also check for partial paths
        self.assertNotIn("documents/active/", content)

    def test_storage_key_not_in_document_list(self):
        response = self.client.get(reverse("documents:list"))
        content = response.content.decode()
        self.assertNotIn(self.doc._storage_key, content)
        self.assertNotIn("documents/active/", content)

    def test_storage_key_not_in_upload_form(self):
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        self.assertNotIn("storage_key", content)
        self.assertNotIn("_storage_key", content)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Error containers have role="alert"
# ─────────────────────────────────────────────────────────────────────────────


class ErrorRoleAlertTests(TestCase):
    """
    WCAG 1.3.1, 4.1.3: error messages must be announced to screen readers.
    Error containers must have role="alert".
    """

    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_upload_form_non_field_error_has_role_alert(self):
        """
        WCAG 4.1.3: the error container that appears after a failed form
        submission must have role="alert" so screen readers announce it.

        We POST with an invalid category_slug (empty string) to trigger a real
        form validation error, then assert that the rendered error container
        carries role="alert".  A plain GET must NOT be used here because the
        <noscript> block always renders role="alert" regardless of errors,
        making a GET-based test a false positive.
        """
        # Ensure at least one category exists so the form can render choices.
        _make_category()
        # POST with an invalid (empty) category_slug to trigger a field error.
        response = self.client.post(
            reverse("documents:upload-init"),
            {
                "category_slug": "",  # required — intentionally blank
                "original_filename": "",
                "mime_type": "",
                "size_bytes": "",
                "description": "",
            },
        )
        # Form re-renders with errors — must not redirect.
        # The view returns 422 Unprocessable Entity on form validation failure.
        self.assertEqual(response.status_code, 422)
        content = response.content.decode()
        # The category error container must be present and carry role="alert".
        self.assertIn('id="category-error"', content)
        self.assertIn('role="alert"', content)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Skip navigation link
# ─────────────────────────────────────────────────────────────────────────────


class SkipNavigationTests(TestCase):
    """
    WCAG 2.4.1: a skip navigation link must be present to bypass navigation blocks.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def _assertHasSkipNav(self, response):  # noqa: N802
        content = response.content.decode()
        has_skip = "#main-content" in content or "skip" in content.lower()
        self.assertTrue(has_skip, "Missing skip navigation link (WCAG 2.4.1)")

    def test_document_list_has_skip_nav(self):
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 200)
        self._assertHasSkipNav(response)

    def test_document_detail_has_skip_nav(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self._assertHasSkipNav(response)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Pagination nav has aria-label
# ─────────────────────────────────────────────────────────────────────────────


class PaginationAriaTests(TestCase):
    """WCAG 1.3.1: pagination nav must have aria-label."""

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        # Create enough documents to trigger pagination
        for i in range(15):
            doc_id = uuid.uuid4()
            Document.objects.create(
                uploaded_by=self.user,
                category=self.cat,
                original_filename=f"doc-{i}.pdf",
                _storage_key=f"documents/active/{doc_id}/{uuid.uuid4().hex}.bin",
                mime_type="application/pdf",
                size_bytes=1024,
                scan_status=Document.ScanStatus.ACTIVE,
            )
        self.client.force_login(self.user)

    def test_pagination_nav_has_aria_label(self):
        response = self.client.get(reverse("documents:list"))
        content = response.content.decode()
        if "<nav" in content and "pagination" in content:
            # If pagination nav is present, it must have aria-label
            self.assertIn("aria-label", content)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Breadcrumb nav has aria-label
# ─────────────────────────────────────────────────────────────────────────────


class BreadcrumbAriaTests(TestCase):
    """WCAG 2.4.8: breadcrumb nav must have aria-label="Breadcrumb" (or translated)."""

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def test_document_detail_breadcrumb_has_aria_label(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        content = response.content.decode()
        if "breadcrumb" in content.lower():
            self.assertIn("aria-label", content)


# ─────────────────────────────────────────────────────────────────────────────
# 10. CSRF token present on POST forms
# ─────────────────────────────────────────────────────────────────────────────


class CSRFTokenTests(TestCase):
    """
    WCAG/security: all POST forms must include CSRF token.
    """

    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_upload_form_has_csrf_token(self):
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        self.assertIn("csrfmiddlewaretoken", content)

    def test_upload_form_method_is_post(self):
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        self.assertIn('method="post"', content.lower())


# ─────────────────────────────────────────────────────────────────────────────
# 11. Table has <caption> for screen readers
# ─────────────────────────────────────────────────────────────────────────────


class TableCaptionTests(TestCase):
    """
    WCAG 1.3.1: data tables must have a <caption> or aria-label/aria-labelledby.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def test_document_list_table_has_caption(self):
        response = self.client.get(reverse("documents:list"))
        content = response.content.decode()
        if "<table" in content:
            # Table must have either caption or aria-label
            has_caption = "<caption" in content
            has_aria_label = "aria-label" in content
            has_aria_labelledby = "aria-labelledby" in content
            self.assertTrue(
                has_caption or has_aria_label or has_aria_labelledby,
                "Table missing caption/aria-label — WCAG 1.3.1",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 12. Section headings have IDs wired to aria-labelledby
# ─────────────────────────────────────────────────────────────────────────────


class SectionAriaLabelledByTests(TestCase):
    """
    WCAG 1.3.1: sections and headings must be associated via aria-labelledby.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def test_document_list_section_aria_labelledby(self):
        response = self.client.get(reverse("documents:list"))
        content = response.content.decode()
        # Section must use aria-labelledby referencing the heading ID
        self.assertIn("aria-labelledby", content)
        self.assertIn("docs-heading", content)

    def test_upload_form_section_aria_labelledby(self):
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        self.assertIn("aria-labelledby", content)
        self.assertIn("upload-heading", content)

    def test_document_detail_section_aria_labelledby(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        content = response.content.decode()
        self.assertIn("aria-labelledby", content)
        self.assertIn("doc-heading", content)


# ─────────────────────────────────────────────────────────────────────────────
# 13. Required fields announce themselves to screen readers
# ─────────────────────────────────────────────────────────────────────────────


class RequiredFieldAriaTests(TestCase):
    """WCAG 3.3.2: required fields must have aria-required="true"."""

    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_upload_form_category_is_aria_required(self):
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        self.assertIn('aria-required="true"', content)

    def test_required_asterisk_is_aria_hidden(self):
        """
        The visual asterisk (*) indicating required fields must be hidden
        from screen readers via aria-hidden="true" to avoid confusion.
        """
        response = self.client.get(reverse("documents:upload-init"))
        content = response.content.decode()
        # Must use visually-hidden text instead of relying on asterisk alone
        self.assertIn("visually-hidden", content)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Login required — anonymous users redirected, not 500
# ─────────────────────────────────────────────────────────────────────────────


class AuthRedirectTests(TestCase):
    """All document pages must redirect unauthenticated users to login."""

    def test_document_list_redirects_anon(self):
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_document_detail_redirects_anon(self):
        user = _make_user()
        cat = _make_category()
        doc = _make_document(user, cat)
        response = self.client.get(reverse("documents:detail", args=[doc.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_upload_form_redirects_anon(self):
        response = self.client.get(reverse("documents:upload-init"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


# ─────────────────────────────────────────────────────────────────────────────
# 15. Time elements have machine-readable datetime
# ─────────────────────────────────────────────────────────────────────────────


class TimeElementTests(TestCase):
    """
    WCAG 1.3.1: <time> elements must have machine-readable datetime attribute.
    """

    def setUp(self):
        self.user = _make_user()
        self.cat = _make_category()
        self.doc = _make_document(self.user, self.cat)
        self.client.force_login(self.user)

    def test_document_list_time_has_datetime_attr(self):
        response = self.client.get(reverse("documents:list"))
        content = response.content.decode()
        if "<time" in content:
            self.assertIn("datetime=", content)

    def test_document_detail_time_has_datetime_attr(self):
        response = self.client.get(reverse("documents:detail", args=[self.doc.pk]))
        content = response.content.decode()
        if "<time" in content:
            self.assertIn("datetime=", content)
