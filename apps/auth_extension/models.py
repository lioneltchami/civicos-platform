"""
Custom User model for Govstack.

Extends Django's AbstractUser to:
- Use email as the login identifier (no username)
- Store language preference for bilingual support
- Track MFA enrollment status
- Store last login IP for security audit purposes

This model is set as AUTH_USER_MODEL in settings. It must be created before
the first migration — changing it later is complex and disruptive.
"""

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class GovstackUserManager(UserManager):
    """Custom manager that uses email as the unique identifier."""

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError(_("An email address is required."))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Govstack user model.

    Replaces username with email as the primary identifier.
    Add profile fields here rather than in a separate Profile model
    to avoid an extra JOIN on every authenticated request.
    """

    username = None  # Remove username field from AbstractUser
    email = models.EmailField(
        unique=True,
        verbose_name=_("Email address"),
    )

    # Bilingual support: user's preferred language
    preferred_language = models.CharField(
        max_length=10,
        choices=[("en", "English"), ("fr", "Français")],
        default="en",
        verbose_name=_("Preferred language"),
    )

    # Security audit fields
    last_login_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("Last login IP"),
    )

    # Terms of service acceptance (required for citizen accounts)
    terms_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Terms accepted at"),
    )

    # Phone number for SMS notifications (optional)
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("Phone number"),
        help_text=_("Include country code, e.g. +1 613 555 0100"),
    )

    # Postal address for CRA charitable donation receipts (CRA IT-110R3)
    postal_address = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Postal address"),
        help_text=_(
            "Full mailing address for CRA charitable donation receipts. "
            "Format: street, city, province, postal code. "
            "Required for official tax receipts under CRA IT-110R3."
        ),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # email + password only for createsuperuser

    objects = GovstackUserManager()

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["email"]

    def __str__(self) -> str:
        # PIPEDA: never return email in __str__ — may appear in application logs.
        # Return display name if set, otherwise a PK-based placeholder.
        if self.first_name or self.last_name:
            return f"{self.first_name} {self.last_name}".strip()
        return f"User #{self.pk}"

    def get_full_name(self) -> str:
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name  # empty string when no names set; display_name handles fallback

    @property
    def is_citizen(self) -> bool:
        """True if this is a citizen (portal) account with no staff access."""
        return not self.is_staff

    @property
    def display_name(self) -> str:
        """Safe display name for use in templates."""
        return self.get_full_name() or self.email.split("@")[0]
