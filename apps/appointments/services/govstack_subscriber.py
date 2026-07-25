"""
GovStack Scheduler BB — Subscriber service layer.

Subscriber = GovStack concept for a citizen or group that books appointments.
CivicOS backing models: auth_extension.User + GovStackSubscriberProfile.

Model mapping:
  GovStack `name`            → User.first_name + User.last_name
  GovStack `phone`           → User.phone_number (max 20 chars)
  GovStack `email`           → User.email (unique identifier; required)
  GovStack `category`        → GovStackSubscriberProfile.category
  GovStack `alert_url`       → GovStackSubscriberProfile.alert_url
  GovStack `alert_preference`→ GovStackSubscriberProfile.alert_preference
  GovStack `status_poll_url` → GovStackSubscriberProfile.status_poll_url
  GovStack `subscriber_id`   → User.pk (int)

Soft-delete: subscriber_delete() sets User.is_active=False rather than
hard-deleting. Subscriber rows (User + Profile) are preserved for audit trail
and appointment history integrity.

Authentication: GovStack subscribers authenticate via the GovStack identity
layer, not Django's auth backend. Subscriber User records are created with
set_unusable_password() to make direct Django login impossible.

PIPEDA: User.email, User.first_name, User.last_name, User.phone_number are
all PII. None of these values appear in log messages. Only PKs are logged.

SSRF note: alert_url and status_poll_url are stored as-is in Wave C. Wave F
alert dispatch MUST validate HTTPS-only and block private IP ranges before
issuing outbound HTTP calls to these URLs.
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email as _django_validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.appointments.models import GovStackSubscriberProfile
from apps.auth_extension.models import User

logger = logging.getLogger("civicos.appointments.services.govstack_subscriber")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_ALERT_PREFS: frozenset[str] = frozenset({"push", "poll", "email", "sms", "none", ""})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_alert_preference(alert_preference: str) -> None:
    """Raise ValueError if alert_preference is not a recognised choice."""
    if alert_preference not in _VALID_ALERT_PREFS:
        raise ValueError(
            f"Invalid alert_preference {alert_preference!r}. "
            f"Must be one of: {sorted(_VALID_ALERT_PREFS - {''})!r}."
        )


def _validate_url(url: str, field_name: str) -> None:
    """Raise ValueError if url is non-empty and does not start with https://."""
    if url and not url.startswith("https://"):
        raise ValueError(
            f"{field_name} must use HTTPS (got {url!r}). "
            f"Plain HTTP callbacks are not permitted for government data."
        )


def _validate_email_format(email: str) -> None:
    """Raise ValueError if email does not look like a valid email address."""
    try:
        _django_validate_email(email)
    except DjangoValidationError:
        raise ValueError("Invalid email address format.")


def _split_name(name: str) -> tuple[str, str]:
    """
    Split a full name string into (first_name, last_name).

    Uses a single split on the first space. If only one word is given,
    last_name is set to an empty string.
    """
    parts = (name or "").split(" ", 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""
    return first, last


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def subscriber_create(
    name: str = "",
    category: str = "",
    phone: str = "",
    email: str = "",
    alert_url: str = "",
    alert_preference: str = "",
    status_poll_url: str = "",
) -> GovStackSubscriberProfile:
    """
    Create a new Subscriber (User + GovStackSubscriberProfile).

    The caller must supply a non-blank email — it is the unique identifier for
    the subscriber record and the Django User backing it.

    GovStack subscribers authenticate via the GovStack identity layer. The
    Django User is created with set_unusable_password() so that direct Django
    login via password is not possible.

    Both the User and the GovStackSubscriberProfile are created inside a single
    database transaction. An IntegrityError caused by a duplicate email is caught
    and re-raised as a descriptive ValueError so the view layer can return 409.

    Args:
        name:             Full name, e.g. "Jane Doe". Splits on first space.
        category:         GovStack subscriber category, e.g. "individual".
        phone:            Phone number string (max 20 characters).
        email:            Required. Becomes User.email (unique).
        alert_url:        HTTPS URL for push alert delivery.
        alert_preference: One of push / poll / email / sms / none / "".
        status_poll_url:  HTTPS URL the scheduler polls for availability.

    Returns:
        The newly created GovStackSubscriberProfile instance.

    Raises:
        ValueError: email is blank, email format invalid, alert_preference is
                    invalid, a subscriber with this email already exists, or
                    phone exceeds 20 characters.
    """
    if not email or not email.strip():
        raise ValueError("email is required to create a subscriber.")
    email = User.objects.normalize_email(email.strip())
    _validate_email_format(email)

    _validate_alert_preference(alert_preference)
    _validate_url(alert_url, "alert_url")
    _validate_url(status_poll_url, "status_poll_url")

    if phone and len(phone) > 20:
        raise ValueError("phone must not exceed 20 characters.")

    first_name, last_name = _split_name(name)

    try:
        with transaction.atomic():
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone or "",
                is_active=True,
            )
            user.set_unusable_password()
            user.save()

            profile = GovStackSubscriberProfile.objects.create(
                user=user,
                category=category or "",
                alert_url=alert_url or "",
                alert_preference=alert_preference or "",
                status_poll_url=status_poll_url or "",
            )
    except IntegrityError as exc:
        raise ValueError(
            "A subscriber with this email already exists."
        ) from exc

    logger.debug(
        "subscriber_create: created profile pk=%d user_pk=%d",
        profile.pk,
        user.pk,
    )
    return profile


def subscriber_modify(
    subscriber_id: int,
    name: str | None = None,
    category: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    alert_url: str | None = None,
    alert_preference: str | None = None,
    status_poll_url: str | None = None,
) -> GovStackSubscriberProfile:
    """
    Modify an existing active Subscriber.

    subscriber_id is the User PK. Only fields that are explicitly supplied
    (not None) are updated. Blank strings are treated as intentional clears
    for optional fields; email must not be blank if supplied.

    Uses select_for_update() inside a transaction to prevent TOCTOU races
    when multiple concurrent requests attempt to modify the same subscriber.

    Args:
        subscriber_id:    User PK of the subscriber to modify.
        name:             If supplied, sets User.first_name + User.last_name.
        category:         If supplied, sets GovStackSubscriberProfile.category.
        phone:            If supplied, sets User.phone_number (max 20 characters).
        email:            If supplied (and non-blank), sets User.email.
        alert_url:        If supplied, sets GovStackSubscriberProfile.alert_url.
        alert_preference: If supplied, validates and sets alert_preference.
        status_poll_url:  If supplied, sets GovStackSubscriberProfile.status_poll_url.

    Returns:
        The updated GovStackSubscriberProfile instance.

    Raises:
        GovStackSubscriberProfile.DoesNotExist: subscriber_id not found or inactive.
        ValueError: email is blank, alert_preference is invalid, or URL is malformed.
    """
    with transaction.atomic():
        profile = (
            GovStackSubscriberProfile.objects
            .select_for_update()
            .select_related("user")
            .get(user_id=subscriber_id, user__is_active=True)
        )

        user = profile.user
        user_update_fields: list[str] = []
        profile_update_fields: list[str] = []

        # --- User fields ---
        if name is not None:
            first_name, last_name = _split_name(name)
            user.first_name = first_name
            user.last_name = last_name
            user_update_fields.extend(["first_name", "last_name"])

        if phone is not None:
            if len(phone) > 20:
                raise ValueError("phone must not exceed 20 characters.")
            user.phone_number = phone
            user_update_fields.append("phone_number")

        if email is not None:
            if not email.strip():
                raise ValueError("email must not be blank.")
            email = User.objects.normalize_email(email.strip())
            _validate_email_format(email)
            user.email = email
            user_update_fields.append("email")

        if user_update_fields:
            try:
                user.save(update_fields=user_update_fields)
            except IntegrityError:
                raise ValueError("A subscriber with this email already exists.")
            logger.debug(
                "subscriber_modify: updated user pk=%d fields=%r",
                user.pk,
                user_update_fields,
            )

        # --- Profile fields ---
        if alert_preference is not None:
            _validate_alert_preference(alert_preference)
            profile.alert_preference = alert_preference
            profile_update_fields.append("alert_preference")

        if alert_url is not None:
            _validate_url(alert_url, "alert_url")
            profile.alert_url = alert_url
            profile_update_fields.append("alert_url")

        if status_poll_url is not None:
            _validate_url(status_poll_url, "status_poll_url")
            profile.status_poll_url = status_poll_url
            profile_update_fields.append("status_poll_url")

        if category is not None:
            profile.category = category
            profile_update_fields.append("category")

        if user_update_fields or profile_update_fields:
            if profile_update_fields:
                profile_update_fields.append("updated_at")
                profile.save(update_fields=profile_update_fields)
            else:
                # User fields changed — advance profile timestamp for audit trail completeness.
                profile.save(update_fields=["updated_at"])
            logger.debug(
                "subscriber_modify: updated profile pk=%d user_fields=%r profile_fields=%r",
                profile.pk,
                user_update_fields,
                profile_update_fields,
            )

    return profile


def subscriber_delete(subscriber_id: int) -> None:
    """
    Soft-delete a Subscriber by setting User.is_active=False.

    Does NOT call User.delete() or profile.delete(). The User and
    GovStackSubscriberProfile rows are preserved so that appointment
    history and audit trails remain intact.

    Args:
        subscriber_id: User PK of the subscriber to soft-delete.

    Raises:
        GovStackSubscriberProfile.DoesNotExist: subscriber_id not found or already inactive.
    """
    with transaction.atomic():
        profile = (
            GovStackSubscriberProfile.objects
            .select_for_update()
            .select_related("user")
            .get(user_id=subscriber_id, user__is_active=True)
        )
        profile.user.is_active = False
        profile.user.save(update_fields=["is_active"])
    logger.debug("subscriber_delete: soft-deleted subscriber user_pk=%d", subscriber_id)


def subscriber_list(
    subscriber_filter: dict,
    subscriber_details_required: dict,
) -> list[dict]:
    """
    Return a filtered list of active Subscriber records as GovStack dicts.

    Applies optional filter parameters from subscriber_filter:
      subscriber_id     — exact match on User PK
      name              — case-insensitive contains on first_name OR last_name
      phone             — case-insensitive contains on phone_number
      email             — case-insensitive contains on email
      category          — case-insensitive contains on profile.category
      alert_preference  — case-insensitive contains on profile.alert_preference

    Shapes each result using subscriber_details_required boolean flags.
    subscriber_id is ALWAYS included in the output regardless of the flag.

    Default visibility (matching GovStack spec for PII minimisation):
      subscriber_id     always included
      name              True  (display name is expected in most list responses)
      category          False
      phone             False (PII)
      email             False (PII — only expose when explicitly requested)
      alert_url         False
      alert_preference  False
      status_poll_url   False

    PIPEDA: email and phone are PII. They are excluded from responses unless
    the caller explicitly requests them via subscriber_details_required.
    No PII appears in log output.

    Args:
        subscriber_filter:          Dict of optional filter keys (see above).
        subscriber_details_required: Dict of field → bool flags.

    Returns:
        List of dicts ready for JSON serialisation.
    """
    qs = GovStackSubscriberProfile.objects.select_related("user").filter(user__is_active=True)

    filter_data = subscriber_filter or {}

    subscriber_id_filter = filter_data.get("subscriber_id", "")
    if subscriber_id_filter:
        try:
            subscriber_id_int = int(subscriber_id_filter)
        except (ValueError, TypeError):
            raise ValueError("subscriber_id filter must be an integer.")
        qs = qs.filter(user_id=subscriber_id_int)

    name_filter = filter_data.get("name", "")
    if name_filter:
        qs = qs.filter(
            Q(user__first_name__icontains=name_filter)
            | Q(user__last_name__icontains=name_filter)
        )

    phone_filter = filter_data.get("phone", "")
    if phone_filter:
        qs = qs.filter(user__phone_number__icontains=phone_filter)

    email_filter = filter_data.get("email", "")
    if email_filter:
        qs = qs.filter(user__email__icontains=email_filter)

    category_filter = filter_data.get("category", "")
    if category_filter:
        qs = qs.filter(category__icontains=category_filter)

    alert_preference_filter = filter_data.get("alert_preference", "")
    if alert_preference_filter:
        qs = qs.filter(alert_preference__icontains=alert_preference_filter)

    MAX_RESULTS = 500
    qs = qs.order_by("pk")[:MAX_RESULTS]

    # --- shape results ---
    required = subscriber_details_required or {}
    results: list[dict] = []

    for profile in qs:
        record: dict = {"subscriber_id": str(profile.user_id)}  # always included

        if required.get("name", False):
            record["name"] = profile.user.get_full_name()

        if required.get("category", False):
            record["category"] = profile.category

        if required.get("phone", False):
            record["phone"] = profile.user.phone_number

        if required.get("email", False):
            record["email"] = profile.user.email

        if required.get("alert_url", False):
            record["alert_url"] = profile.alert_url

        if required.get("alert_preference", False):
            record["alert_preference"] = profile.alert_preference

        if required.get("status_poll_url", False):
            record["status_poll_url"] = profile.status_poll_url

        results.append(record)

    if len(results) == MAX_RESULTS:
        logger.warning(
            "subscriber_list: result capped at %d rows. Use subscriber_id or other filters to narrow.",
            MAX_RESULTS,
        )
    logger.debug("subscriber_list: returned %d results", len(results))
    return results
