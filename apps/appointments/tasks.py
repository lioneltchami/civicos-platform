"""
Appointments BB — Celery tasks.

Wave 2 tasks (implemented here):
  generate_slots_for_period   — generate Slot records for the upcoming horizon window
  mark_past_slots_completed   — transition past Slots to 'completed' status

Wave 3 tasks (implemented here):
  cleanup_expired_pending_bookings — cancel PENDING bookings past their timeout window

Wave F tasks (implemented here):
  dispatch_alert_schedule     — deliver a GovStackAlertSchedule's push
                                 notification to its resolved participants at
                                 (or after) its scheduled alert_datetime ETA.
                                 See the dedicated module docstring on
                                 dispatch_alert_schedule() below for the full
                                 idempotency / locking / SSRF design.

Later waves will add:
  Wave 6: iCal reminder email dispatch

Security invariants (apply to ALL tasks):
  - NEVER include PII (citizen email, name) in task args or log messages — use PKs only.
  - Tasks MUST be idempotent (safe to retry on transient failure).
  - record_event() MUST be called inside transaction.atomic() (Wave 3+).
  - Use select_for_update() inside atomic() for capacity/status checks (Wave 3+).
  - acks_late=True on all tasks — Celery will not ACK until the task returns,
    preventing message loss on worker crash.
  - reject_on_worker_lost=True — task will be requeued if worker dies.

Wave F additional invariant — SSRF:
  dispatch_alert_schedule() makes outbound HTTP calls to URLs supplied by
  GovStack callers at subscriber/resource registration time (Wave C/B). Those
  URLs are validated for HTTPS-only + public-IP-only via
  _is_safe_outbound_url() IMMEDIATELY BEFORE each outbound call (not just at
  registration time) — DNS can change between registration and dispatch
  (TOCTOU), so re-validating at call time is mandatory. See
  _is_safe_outbound_url()'s docstring for the full threat model.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from datetime import timedelta
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wave 2: generate_slots_for_period
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="appointments.generate_slots_for_period",
    max_retries=3,
    default_retry_delay=300,  # 5 minutes
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=3300,  # 55 min — catch overruns and log cleanly before hard kill
    time_limit=3600,  # 60 min hard kill
)
def generate_slots_for_period(self, horizon_days: int | None = None) -> dict:  # noqa: ANN001
    """
    Generate Slot records for all active staff × active appointment_type
    combinations over the upcoming horizon window.

    Idempotent: generate_slots_for_range() skips slots that already exist
    for the same (staff, appointment_type, start_datetime) combination.

    Called by: Celery Beat daily at 02:00 UTC.

    Args:
        horizon_days: Days ahead to generate slots for.
                      Defaults to CIVICOS['APPOINTMENTS']['SLOT_GENERATION_HORIZON_DAYS'].

    Returns:
        {"slots_created": N, "combinations_processed": M}
    """  # noqa: RUF002

    from apps.appointments.models import AppointmentType, StaffProfile
    from apps.appointments.services.slots import generate_slots_for_range

    if horizon_days is None:
        appt_settings = settings.CIVICOS.get("APPOINTMENTS", {})
        horizon_days = appt_settings.get("SLOT_GENERATION_HORIZON_DAYS", 60)

    today = timezone.now().date()
    date_to = today + timedelta(days=horizon_days)

    # Fetch active appointment types
    active_appt_types = list(AppointmentType.objects.filter(is_active=True))
    active_appt_type_pks = {at.pk for at in active_appt_types}
    appt_type_by_pk = {at.pk: at for at in active_appt_types}

    # Fetch active staff with at least one linked appointment type.
    # Wrapped in try so a DB connection failure at fetch time retries the task.
    # list() forces queryset evaluation here rather than lazily at iteration —
    # this ensures the outer except actually catches a connection error at fetch
    # time. Do NOT use .iterator() — it disables Django's result cache and
    # thereby silently drops prefetch_related("appointment_types"), causing an
    # extra DB query per staff member (N+1). The queryset is bounded by
    # active/accepting staff so loading it fully into memory is acceptable.
    try:
        staff_list = list(
            StaffProfile.objects.filter(
                is_accepting_bookings=True,
                location__is_active=True,
            )
            .select_related("location")
            .prefetch_related("appointment_types")
        )
    except Exception as exc:
        logger.exception(
            "generate_slots_for_period: failed to fetch staff queryset: %s",
            type(exc).__name__,
        )
        raise self.retry(exc=exc)  # noqa: B904

    total_created = 0
    combinations = 0

    # Iterate and process — per-combination errors are logged but do not abort
    # the run or retry the whole task.
    try:
        for staff_member in staff_list:
            staff_appt_type_pks = set(staff_member.appointment_types.values_list("pk", flat=True))
            eligible_pks = staff_appt_type_pks & active_appt_type_pks

            for appt_type_pk in eligible_pks:
                appt_type = appt_type_by_pk[appt_type_pk]
                try:
                    created = generate_slots_for_range(
                        appointment_type=appt_type,
                        staff=staff_member,
                        date_from=today,
                        date_to=date_to,
                        created_by_task=True,
                    )
                    total_created += created
                    combinations += 1
                except Exception as exc:
                    # Log but don't abort — continue with other combinations
                    logger.error(
                        "generate_slots_for_period: error for staff_id=%s, "
                        "appointment_type_id=%s: %s",
                        staff_member.pk,
                        appt_type_pk,
                        type(exc).__name__,
                    )
    except SoftTimeLimitExceeded:
        logger.warning(
            "generate_slots_for_period soft time limit reached; "
            "processed partial staff list. Task will be re-queued on next Beat trigger."
        )
        # Do NOT re-raise — let the task complete gracefully with partial results.
        # The next nightly run will cover any missed staff members.

    logger.info(
        "generate_slots_for_period: %d slots created across %d staff×type combinations "  # noqa: RUF001
        "(horizon=%d days)",
        total_created,
        combinations,
        horizon_days,
    )
    return {"slots_created": total_created, "combinations_processed": combinations}


# ---------------------------------------------------------------------------
# Wave 2: mark_past_slots_completed
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="appointments.mark_past_slots_completed",
    max_retries=3,
    default_retry_delay=300,  # 5 minutes — consistent with generate_slots_for_period
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=270,  # catch overruns before the hard kill
    time_limit=300,  # hard kill — explicit, self-documenting
)
def mark_past_slots_completed(self) -> dict:  # noqa: ANN001
    """
    Transition all past Slot records in non-terminal statuses to 'completed'.

    A slot is considered 'past' when its end_datetime < now (UTC).
    Only slots in status 'available', 'partial', or 'full' are transitioned —
    'blocked' and 'cancelled' slots remain as-is (they are already terminal
    in the context of booking availability).

    Idempotent: safe to run multiple times. Each run only affects newly-past slots.

    Called by: Celery Beat daily at 23:30 UTC.

    Returns:
        {"slots_updated": N}
    """
    from apps.appointments.models import Slot

    try:
        now = timezone.now()
        try:
            count = Slot.objects.filter(
                end_datetime__lt=now,
                status__in=["available", "partial", "full"],
            ).update(status="completed")
        except SoftTimeLimitExceeded:
            logger.warning(
                "mark_past_slots_completed: soft time limit reached before update "
                "completed. Remaining slots will be processed on the next Beat trigger."
            )
            return {"slots_updated": 0, "timed_out": True}

        logger.info("mark_past_slots_completed: %d slots marked completed", count)
        return {"slots_updated": count}

    except Exception as exc:
        logger.exception(
            "mark_past_slots_completed: error — retrying. error_type=%s",
            type(exc).__name__,
        )
        raise self.retry(exc=exc)  # noqa: B904


# ---------------------------------------------------------------------------
# Wave 3: cleanup_expired_pending_bookings
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="appointments.cleanup_expired_pending_bookings",
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=270,
    time_limit=300,
)
def cleanup_expired_pending_bookings(self) -> dict:  # noqa: ANN001
    """
    Cancel PENDING bookings that have exceeded the pending timeout window.

    Timeout: CIVICOS['APPOINTMENTS']['PENDING_BOOKING_TIMEOUT_MINUTES'] (default 30).
    Idempotent: safe to run multiple times.
    Called by Celery Beat every 15 minutes.

    Returns:
        {"bookings_cancelled": N}
    """

    from apps.appointments.models import Booking
    from apps.appointments.services.booking import cancel_booking

    try:
        appt_cfg = settings.CIVICOS.get("APPOINTMENTS", {})
        timeout_minutes = appt_cfg.get("PENDING_BOOKING_TIMEOUT_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=timeout_minutes)

        expired_pks = list(
            Booking.objects.filter(
                status="pending",
                created_at__lt=cutoff,
            ).values_list("pk", flat=True)[:200]  # Max 200 per run
        )

        cancelled = 0
        for pk in expired_pks:
            try:
                booking = Booking.objects.get(pk=pk)
                if booking.status != "pending":
                    continue  # Race: already changed
                cancel_booking(booking=booking, actor=None, reason="Pending timeout exceeded.")
                cancelled += 1
            except Booking.DoesNotExist:
                pass
            except Exception as exc:
                logger.error(
                    "cleanup_expired_pending_bookings: error booking_id=%s: %s",
                    pk,
                    type(exc).__name__,
                )

        logger.info(
            "cleanup_expired_pending_bookings: %d/%d expired pending bookings cancelled",
            cancelled,
            len(expired_pks),
        )
        return {"bookings_cancelled": cancelled}

    except Exception as exc:
        logger.exception(
            "cleanup_expired_pending_bookings: unhandled error — retrying. error_type=%s",
            type(exc).__name__,
        )
        raise self.retry(exc=exc)  # noqa: B904


# ---------------------------------------------------------------------------
# Wave F: dispatch_alert_schedule — SSRF-safe URL validator + delivery task
# ---------------------------------------------------------------------------

# Seconds to wait for an alert recipient's callback endpoint to respond before
# abandoning the POST. Mirrors apps.payments.govstack_tasks._CALLBACK_TIMEOUT_SECONDS.
_ALERT_DISPATCH_TIMEOUT_SECONDS: float = 10.0

# Booking statuses that represent a "still a real participant" citizen for
# alert-dispatch purposes — see apps.appointments.models.Booking.STATUS_CHOICES.
# STATUS_REJECTED / STATUS_CANCELLED participants never attended; STATUS_COMPLETED
# means the appointment already happened, so there is nothing left to alert about.
_ALERT_ELIGIBLE_BOOKING_STATUSES: tuple[str, ...] = ("pending", "confirmed")

# RFC 6598 Shared Address Space (a.k.a. CGNAT range) — NOT covered by any of
# ipaddress.ip_address's is_private/is_loopback/is_link_local/is_reserved/
# is_multicast/is_unspecified properties (confirmed: ipaddress.ip_address
# ("100.64.0.1") reports False for all six), yet it is routable inside many
# cloud VPC / Kubernetes overlay networks and can reach internal
# infrastructure — must be checked explicitly.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

# IANA special-purpose registry: IETF Protocol Assignments — likewise not
# covered by the six ipaddress properties above.
_IETF_PROTOCOL_ASSIGNMENTS = ipaddress.ip_network("192.0.0.0/24")


def _is_safe_outbound_url(url: str) -> bool:
    """
    Return True only if ``url`` is safe to issue an outbound HTTP POST to.

    Required for EVERY outbound call this task makes (subscriber alert_url,
    staff gs_alert_url, resource alert_url) — see the module docstring's
    "Wave F additional invariant — SSRF" note and the planted SSRF-note
    comments in models.py / services/govstack_subscriber.py this function
    satisfies.

    Checks, in order (fails closed on ANY failure):
      1. scheme must be exactly "https" and a hostname must be present.
      2. The hostname is resolved via DNS (socket.getaddrinfo) — this is the
         TOCTOU-safe step: registration-time validation (see
         services.govstack_subscriber._validate_url, which only checks the
         HTTPS scheme) cannot catch a hostname that resolves to a private IP
         *at dispatch time*, since DNS can be repointed after registration.
      3. EVERY resolved IP address (a hostname may have multiple A/AAAA
         records) must be public and routable. Rejected ranges: private,
         loopback, link-local, reserved, multicast, and unspecified (the six
         ipaddress.ip_address properties), PLUS two ranges those six
         properties do NOT cover: RFC 6598 Shared Address Space / CGNAT
         (100.64.0.0/10) and the IANA IETF Protocol Assignments block
         (192.0.0.0/24). A single unsafe address among several resolved
         addresses is enough to reject the whole URL.
      4. Any exception at all (malformed URL, DNS resolution failure, no
         addresses returned) is treated as unsafe.

    This function intentionally does NOT attempt to resolve the URL a second
    time immediately before the actual `requests.post()` call (a true
    TOCTOU-proof design would need to pin the resolved IP and connect to it
    directly, bypassing a second DNS lookup inside `requests`) — that level
    of hardening is out of scope for Wave F; validating immediately before
    the call, as done here, closes the registration-time-vs-dispatch-time gap
    that the planted SSRF-note comments call out, which is the specific risk
    this wave is required to close.

    Deliberately NOT hardened beyond the two extra ranges above: this is not
    a general-purpose IP-reputation blocklist. 100.64.0.0/10 and
    192.0.0.0/24 were added because they are confirmed gaps in the six
    ipaddress properties already checked; expanding this into a larger
    hardcoded range list is out of scope.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False

        addrinfo = socket.getaddrinfo(parsed.hostname, None)
        if not addrinfo:
            return False

        for info in addrinfo:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
                or ip in _SHARED_ADDRESS_SPACE
                or ip in _IETF_PROTOCOL_ASSIGNMENTS
            ):
                return False

        return True
    except Exception:
        return False


def _attempt_alert_delivery(url: str, payload: dict, log_ctx: str) -> bool:
    """
    Best-effort delivery of one alert POST. Returns True if a request was
    actually sent (regardless of the remote end's response — that outcome is
    logged but never raised), False if the URL failed the SSRF safety check
    and was skipped before any network call was made.

    Mirrors apps.payments.govstack_tasks._post_callback's non-fatal
    try/except/log structure exactly: ALL exceptions from the outbound call
    (connection error, timeout, non-2xx, etc.) are caught and logged as
    warnings — a delivery failure to one recipient must never abort dispatch
    to the remaining recipients or fail the Celery task.

    Security — redirects are never followed (allow_redirects=False):
    ``_is_safe_outbound_url()`` only validates the ORIGINAL url's scheme/DNS/
    IP; it has no visibility into a response's ``Location`` header. Since
    ``requests`` follows redirects by default, an attacker who controls a
    registered alert_url could point it at a public HTTPS host that passes
    validation, then have that host respond with a 3xx redirecting to a
    private IP or cloud metadata endpoint (e.g. 169.254.169.254) —
    transparently defeating the SSRF control. ``allow_redirects=False``
    closes this: the redirect is never followed, and — because
    ``Response.raise_for_status()`` only raises for status codes >= 400 and
    would otherwise silently treat a 3xx as "delivered" — any 3xx response is
    explicitly treated as a failed delivery below, logged identically to any
    other non-2xx outcome (non-fatal; does not abort the rest of the
    dispatch loop).

    Security: only the URL, exception class name, and log_ctx (a caller-
    supplied PK-only identifier such as "citizen_pk=123") are ever logged.
    payload["message_body"] — and the payload dict as a whole — is NEVER
    logged, since it may describe appointment-specific details.
    """
    if not _is_safe_outbound_url(url):
        logger.warning("dispatch_alert_schedule.unsafe_url_skipped url=%s %s", url, log_ctx)
        return False

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=_ALERT_DISPATCH_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            # A validated-safe URL that 3xx-redirects to an internal target
            # must never be silently followed — see docstring above.
            # raise_for_status() would NOT raise on its own for this range
            # (it only raises for >= 400), so this is an explicit check.
            raise requests.exceptions.HTTPError(
                f"{response.status_code} redirect response received "
                f"(not followed — allow_redirects=False)"
            )
        response.raise_for_status()
        logger.info(
            "dispatch_alert_schedule.delivered url=%s status=%s %s",
            url,
            response.status_code,
            log_ctx,
        )
    except Exception as exc:
        logger.warning(
            "dispatch_alert_schedule.delivery_failed url=%s exc_type=%s %s",
            url,
            type(exc).__name__,
            log_ctx,
        )

    return True


@shared_task(
    bind=True,
    name="appointments.dispatch_alert_schedule",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=90,
    time_limit=120,
)
def dispatch_alert_schedule(self, alert_schedule_pk: str) -> dict:  # noqa: ANN001
    """Durably admit one alert generation before any wake-up or transport I/O.

    ``delivery_generation``, ``delivery_admittable`` and the durable admission
    markers are the sole admission authority.  Legacy ``dispatched`` and
    ``celery_task_id`` fields are compatibility projections only.
    """
    from apps.appointments.models import Booking, GovStackAlertSchedule
    from apps.appointments.scheduler_tasks import publish_scheduler_outbox
    from apps.appointments.services import scheduler_runtime

    wakeup_registered = False

    def wake_outbox_after_commit() -> None:
        try:
            publish_scheduler_outbox.delay()
        except Exception as exc:  # durable admission has already committed
            logger.warning(
                "dispatch_alert_schedule.outbox_wakeup_failed alert_schedule_pk=%s exc_type=%s",
                alert_schedule_pk,
                type(exc).__name__,
            )

    try:
        with transaction.atomic():
            schedule = (
                GovStackAlertSchedule.objects.select_for_update(of=("self",))
                .select_related("slot", "slot__staff", "slot__resource", "message")
                .get(pk=alert_schedule_pk)
            )
            recipients: list[tuple[str, str]] = []
            if schedule.target_category in ("", "subscriber"):
                bookings = Booking.objects.filter(
                    slot=schedule.slot,
                    status__in=_ALERT_ELIGIBLE_BOOKING_STATUSES,
                ).select_related("citizen__govstack_subscriber_profile")
                for booking in bookings:
                    profile = getattr(booking.citizen, "govstack_subscriber_profile", None)
                    if (
                        profile is not None
                        and profile.alert_preference == "push"
                        and profile.alert_url
                    ):
                        recipients.append(("subscriber", str(profile.pk)))
            if schedule.target_category in ("", "resource"):
                staff = schedule.slot.staff
                if staff is not None and staff.gs_alert_preference == "push" and staff.gs_alert_url:
                    recipients.append(("staff", str(staff.pk)))
                resource = schedule.slot.resource
                if (
                    resource is not None
                    and resource.alert_preference == "push"
                    and resource.alert_url
                ):
                    recipients.append(("resource", str(resource.pk)))

            admission = scheduler_runtime.admit_schedule_generation(
                schedule_id=schedule.pk,
                expected_generation=schedule.delivery_generation,
                recipients=recipients,
                owner_key=f"schedule:{schedule.pk}",
                correlation_id=f"scheduler:{schedule.pk}:{schedule.delivery_generation}",
                payload={},
            )
            if (
                admission["outcome"] == GovStackAlertSchedule.ADMISSION_CREATED
                and admission["created"]
            ):
                transaction.on_commit(wake_outbox_after_commit)
                wakeup_registered = True
    except GovStackAlertSchedule.DoesNotExist:
        logger.error("dispatch_alert_schedule.not_found alert_schedule_pk=%s", alert_schedule_pk)
        return {"attempted": 0, "skipped_unsafe": 0, "outcome": "not_found", "materialized": 0}

    logger.info(
        "dispatch_alert_schedule.admitted alert_schedule_pk=%s outcome=%s created=%s",
        alert_schedule_pk,
        admission["outcome"],
        admission["created"],
    )
    return {
        "attempted": 0,
        "skipped_unsafe": 0,
        "outcome": admission["outcome"],
        "generation": admission["generation"],
        "materialized": admission["created"],
        "wakeup_registered": wakeup_registered,
    }
