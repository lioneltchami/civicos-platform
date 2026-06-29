"""Tests for Form building block models."""
import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from wagtail.models import Page

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


def make_user(email=None, is_staff=False):
    user = User.objects.create_user(
        email=email or f"u{uuid.uuid4().hex[:8]}@test.ca",
        password=VALID_PASSWORD,
    )
    if is_staff:
        user.is_staff = True
        user.save()
    return user


def make_form_page(title="Test Form", consent_text="", retention_days=365):
    """Create a FormPage as a child of the root page."""
    from apps.forms.models import FormPage
    root_page = Page.objects.filter(depth=1).first()
    if root_page is None:
        root_page = Page.add_root(title="Root", slug="root")
    form_page = FormPage(
        title=title,
        slug=f"form-{uuid.uuid4().hex[:6]}",
        consent_text=consent_text,
        retention_days=retention_days,
        to_address="",
        from_address="",
        subject="",
    )
    root_page.add_child(instance=form_page)
    return form_page


def make_form_field(form_page, label="Full name", field_type="singleline", is_pii=False, required=True):
    """Create a FormField on a FormPage."""
    from apps.forms.models import FormField
    return FormField.objects.create(
        page=form_page,
        label=label,
        field_type=field_type,
        required=required,
        is_pii=is_pii,
        sort_order=FormField.objects.filter(page=form_page).count(),
    )


def make_submission(form_page, form_data=None, consent_given=True):
    """Create a FormSubmission directly (bypassing the form view)."""
    from apps.forms.models import FormSubmission
    from django.utils import timezone
    return FormSubmission.objects.create(
        page=form_page,
        form_data=form_data or {"full_name": "Test Citizen"},
        consent_given=consent_given,
        consent_text_shown="I agree.",
        submitter_ip="192.168.1.1",
        expires_at=timezone.now() + timezone.timedelta(days=365),
    )


class FormFieldTest(TestCase):
    def setUp(self):
        self.page = make_form_page()

    def test_form_field_created(self):
        field = make_form_field(self.page, label="Email address", is_pii=True)
        self.assertEqual(field.label, "Email address")
        self.assertTrue(field.is_pii)

    def test_form_field_ordering_by_sort_order(self):
        make_form_field(self.page, label="First")
        make_form_field(self.page, label="Second")
        fields = list(self.page.form_fields.all())
        self.assertEqual(fields[0].label, "First")
        self.assertEqual(fields[1].label, "Second")

    def test_form_field_belongs_to_page(self):
        field = make_form_field(self.page)
        self.assertEqual(field.page_id, self.page.pk)

    def test_non_pii_field_defaults_to_false(self):
        field = make_form_field(self.page, label="Description")
        self.assertFalse(field.is_pii)

    def test_form_field_has_clean_name(self):
        """Wagtail auto-populates clean_name from the label on save."""
        field = make_form_field(self.page, label="Full Name")
        # clean_name is the slugified form — Wagtail lowercases and replaces spaces
        self.assertIsNotNone(field.clean_name)
        self.assertNotEqual(field.clean_name, "")

    def test_multiple_field_types_accepted(self):
        for ftype in ("singleline", "multiline", "email", "number", "checkboxes", "radio", "dropdown"):
            field = make_form_field(self.page, label=f"Field {ftype}", field_type=ftype)
            self.assertEqual(field.field_type, ftype)


class FormSubmissionTest(TestCase):
    def setUp(self):
        self.page = make_form_page()

    def test_submission_created(self):
        sub = make_submission(self.page)
        self.assertEqual(sub.page_id, self.page.pk)
        self.assertTrue(sub.consent_given)

    def test_submission_has_expires_at(self):
        sub = make_submission(self.page)
        self.assertIsNotNone(sub.expires_at)

    def test_submission_ordering_newest_first(self):
        sub1 = make_submission(self.page, form_data={"name": "A"})
        sub2 = make_submission(self.page, form_data={"name": "B"})
        subs = list(self.page.govstack_form_submissions.all())
        # Newest first: sub2 was created after sub1
        self.assertEqual(subs[0].pk, sub2.pk)

    def test_submission_form_data_stored_as_dict(self):
        data = {"full_name": "Alice", "email": "alice@example.com"}
        sub = make_submission(self.page, form_data=data)
        self.assertIsInstance(sub.form_data, dict)
        self.assertEqual(sub.form_data["full_name"], "Alice")

    def test_submission_consent_text_captured(self):
        sub = make_submission(self.page)
        self.assertEqual(sub.consent_text_shown, "I agree.")

    def test_submission_ip_stored(self):
        sub = make_submission(self.page)
        self.assertEqual(sub.submitter_ip, "192.168.1.1")

    def test_redact_pii_replaces_pii_field_values(self):
        make_form_field(self.page, label="Full name", is_pii=True)
        # clean_name is slugified: "full_name"
        sub = make_submission(self.page, form_data={"full_name": "Alice Smith"})
        sub.redact_pii()
        sub.refresh_from_db()
        self.assertEqual(sub.form_data["full_name"], "[REDACTED]")

    def test_redact_pii_leaves_non_pii_fields_intact(self):
        make_form_field(self.page, label="Full name", is_pii=True)
        make_form_field(self.page, label="Issue description", is_pii=False)
        sub = make_submission(self.page, form_data={
            "full_name": "Alice Smith",
            "issue_description": "Pothole on Main St",
        })
        sub.redact_pii()
        sub.refresh_from_db()
        self.assertEqual(sub.form_data["issue_description"], "Pothole on Main St")

    def test_redact_pii_on_page_with_no_pii_fields_is_noop(self):
        make_form_field(self.page, label="Description", is_pii=False)
        sub = make_submission(self.page, form_data={"description": "Test"})
        sub.redact_pii()  # Should not raise
        sub.refresh_from_db()
        self.assertEqual(sub.form_data["description"], "Test")  # Unchanged

    def test_redact_pii_adds_redaction_marker_to_consent_text(self):
        make_form_field(self.page, label="Name", is_pii=True)
        sub = make_submission(self.page, form_data={"name": "Bob"})
        sub.redact_pii()
        sub.refresh_from_db()
        self.assertIn("[PII REDACTED", sub.consent_text_shown)

    def test_redact_pii_is_idempotent(self):
        """Calling redact_pii() twice should not raise and result is still [REDACTED]."""
        make_form_field(self.page, label="Full name", is_pii=True)
        sub = make_submission(self.page, form_data={"full_name": "Carol"})
        sub.redact_pii()
        sub.redact_pii()
        sub.refresh_from_db()
        self.assertEqual(sub.form_data["full_name"], "[REDACTED]")

    def test_submission_related_name_govstack_form_submissions(self):
        """Custom related_name avoids clash with Wagtail's built-in accessor."""
        sub = make_submission(self.page)
        self.assertIn(sub, self.page.govstack_form_submissions.all())

    def test_submission_deleted_with_page(self):
        """FormSubmission is CASCADE-deleted when its FormPage is deleted."""
        from apps.forms.models import FormSubmission
        sub = make_submission(self.page)
        pk = sub.pk
        self.page.delete()
        self.assertFalse(FormSubmission.objects.filter(pk=pk).exists())


class FormPageConsentInjectionTest(TestCase):
    """Tests that get_form_class() injects a consent BooleanField when consent_text is set."""

    def test_form_class_has_consent_field_when_consent_text_set(self):
        page = make_form_page(consent_text="I agree to data collection.")
        make_form_field(page, label="Name")
        form_class = page.get_form_class()
        self.assertIn("consent", form_class.base_fields)

    def test_form_class_has_no_consent_field_when_consent_text_empty(self):
        page = make_form_page(consent_text="")
        make_form_field(page, label="Name")
        form_class = page.get_form_class()
        self.assertNotIn("consent", form_class.base_fields)

    def test_consent_field_is_required(self):
        page = make_form_page(consent_text="I agree.")
        make_form_field(page, label="Name")
        form_class = page.get_form_class()
        consent_field = form_class.base_fields["consent"]
        self.assertTrue(consent_field.required)

    def test_consent_field_label_matches_consent_text(self):
        consent_text = "I consent to the collection of my personal information."
        page = make_form_page(consent_text=consent_text)
        make_form_field(page, label="Name")
        form_class = page.get_form_class()
        self.assertEqual(form_class.base_fields["consent"].label, consent_text)

    def test_form_class_subclass_does_not_mutate_parent(self):
        """get_form_class() must create a new class each call, not mutate the cached one."""
        page_with_consent = make_form_page(consent_text="I agree.")
        page_without = make_form_page(consent_text="")
        make_form_field(page_with_consent, label="Name")
        make_form_field(page_without, label="Name")
        cls_with = page_with_consent.get_form_class()
        cls_without = page_without.get_form_class()
        self.assertIn("consent", cls_with.base_fields)
        self.assertNotIn("consent", cls_without.base_fields)

    def test_get_submissions_list_url_returns_correct_path(self):
        page = make_form_page()
        url = page.get_submissions_list_url()
        self.assertIn(str(page.pk), url)
        self.assertIn("submissions", url)
