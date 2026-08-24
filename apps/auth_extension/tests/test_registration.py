"""Tests for the citizen registration flow."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"

# allauth mounts signup at /account/signup/
SIGNUP_URL = "/account/signup/"


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="none",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class RegistrationFlowTest(TestCase):
    def _signup_data(self, **overrides):
        data = {
            "email": "newuser@example.com",
            "email2": "newuser@example.com",
            "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD,
            "preferred_language": "en",
            "terms_accepted": True,
        }
        data.update(overrides)
        return data

    def test_signup_page_loads(self):
        response = self.client.get(SIGNUP_URL)
        self.assertEqual(response.status_code, 200)

    def test_signup_with_valid_data_creates_user(self):
        self.client.post(SIGNUP_URL, self._signup_data())
        self.assertTrue(User.objects.filter(email="newuser@example.com").exists())

    def test_signup_requires_terms_acceptance(self):
        response = self.client.post(SIGNUP_URL, self._signup_data(terms_accepted=False))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="newuser@example.com").exists())

    def test_signup_duplicate_email_fails(self):
        User.objects.create_user(email="existing@example.com", password=VALID_PASSWORD)
        response = self.client.post(
            SIGNUP_URL,
            self._signup_data(email="existing@example.com", email2="existing@example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email="existing@example.com").count(), 1)

    def test_signup_stores_preferred_language_fr(self):
        self.client.post(SIGNUP_URL, self._signup_data(preferred_language="fr"))
        user = User.objects.get(email="newuser@example.com")
        self.assertEqual(user.preferred_language, "fr")

    def test_signup_sets_terms_accepted_at(self):
        self.client.post(SIGNUP_URL, self._signup_data())
        user = User.objects.get(email="newuser@example.com")
        self.assertIsNotNone(user.terms_accepted_at)

    def test_signup_password_mismatch_fails(self):
        response = self.client.post(
            SIGNUP_URL,
            self._signup_data(password2="DifferentPass123!"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="newuser@example.com").exists())

    def test_signup_form_contains_terms_field(self):
        response = self.client.get(SIGNUP_URL)
        self.assertContains(response, "terms_accepted")

    def test_signup_form_contains_language_field(self):
        response = self.client.get(SIGNUP_URL)
        self.assertContains(response, "preferred_language")

    def test_signup_with_terms_false_string_does_not_accept(self):
        """Ensure unchecked checkbox (value absent from POST) is treated as False."""
        data = self._signup_data()
        del data["terms_accepted"]
        response = self.client.post(SIGNUP_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="newuser@example.com").exists())
