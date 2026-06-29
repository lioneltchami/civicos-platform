"""Tests for the custom User model."""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

User = get_user_model()
VALID_PASSWORD = "SecureTestPass123!"


class UserModelTest(TestCase):
    def test_create_user_with_email_only(self):
        user = User.objects.create_user(email="alice@example.com", password=VALID_PASSWORD)
        self.assertEqual(user.email, "alice@example.com")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_email_is_unique(self):
        User.objects.create_user(email="dup@example.com", password=VALID_PASSWORD)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="dup@example.com", password=VALID_PASSWORD)

    def test_str_returns_email(self):
        user = User.objects.create_user(email="bob@example.com", password=VALID_PASSWORD)
        self.assertEqual(str(user), "bob@example.com")

    def test_preferred_language_defaults_to_en(self):
        user = User.objects.create_user(email="c@example.com", password=VALID_PASSWORD)
        self.assertEqual(user.preferred_language, "en")

    def test_preferred_language_can_be_fr(self):
        user = User.objects.create_user(
            email="d@example.com", password=VALID_PASSWORD, preferred_language="fr"
        )
        self.assertEqual(user.preferred_language, "fr")

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email="admin@example.com", password=VALID_PASSWORD)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_create_user_without_email_raises(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password=VALID_PASSWORD)

    def test_terms_accepted_at_nullable(self):
        user = User.objects.create_user(email="e@example.com", password=VALID_PASSWORD)
        self.assertIsNone(user.terms_accepted_at)

    def test_last_login_ip_nullable(self):
        user = User.objects.create_user(email="f@example.com", password=VALID_PASSWORD)
        self.assertIsNone(user.last_login_ip)

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher"])
    def test_password_is_hashed_with_argon2(self):
        user = User.objects.create_user(email="g@example.com", password=VALID_PASSWORD)
        self.assertTrue(user.password.startswith("$argon2"))

    def test_password_is_hashed_not_plaintext(self):
        user = User.objects.create_user(email="h@example.com", password=VALID_PASSWORD)
        self.assertNotEqual(user.password, VALID_PASSWORD)

    def test_get_full_name_returns_combined_names(self):
        user = User.objects.create_user(
            email="i@example.com", password=VALID_PASSWORD,
            first_name="Alice", last_name="Smith"
        )
        self.assertEqual(user.get_full_name(), "Alice Smith")

    def test_get_full_name_falls_back_to_email(self):
        user = User.objects.create_user(email="j@example.com", password=VALID_PASSWORD)
        self.assertEqual(user.get_full_name(), "j@example.com")

    def test_is_citizen_true_for_non_staff(self):
        user = User.objects.create_user(email="k@example.com", password=VALID_PASSWORD)
        self.assertTrue(user.is_citizen)

    def test_is_citizen_false_for_staff(self):
        user = User.objects.create_user(
            email="l@example.com", password=VALID_PASSWORD, is_staff=True
        )
        self.assertFalse(user.is_citizen)

    def test_display_name_uses_full_name_when_available(self):
        user = User.objects.create_user(
            email="m@example.com", password=VALID_PASSWORD,
            first_name="Marie", last_name="Dupont"
        )
        self.assertEqual(user.display_name, "Marie Dupont")

    def test_display_name_falls_back_to_email_prefix(self):
        user = User.objects.create_user(email="jean.paul@example.com", password=VALID_PASSWORD)
        self.assertEqual(user.display_name, "jean.paul")
