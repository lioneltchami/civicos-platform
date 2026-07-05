"""
Appointments BB — Slot availability service.

SlotAvailabilityService implements the canonical slot availability algorithm
from SPEC_APPOINTMENTS_BB.md §7. It returns available time windows for a
given AppointmentType over a date range, considering:
  - Staff weekly availability templates (AvailabilityTemplate)
  - Date-level exceptions (StaffException: holiday, leave, override, training)
  - Existing Slot busy-times (buffer-expanded collision detection)
  - Policy constraints (min_lead_time, max_advance_days, booking_frequency_days)
  - Daily appointment cap per staff member (StaffProfile.max_daily_appointments)

Concurrency note: This service is EVENTUALLY CONSISTENT. It shows availability
as of query time but does not lock any rows. The booking service layer enforces
strict consistency via SELECT FOR UPDATE inside atomic() at booking time.

Privacy: No PII is written to logs. Staff and citizen references use PKs only.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger("civicos.appointments.services.availability")


# ---------------------------------------------------------------------------
# Settings-policy proxy
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _SettingsPolicy:
    """
    Proxy object that exposes CIVICOS['APPOINTMENTS'] settings as if they were
    a SchedulingPolicy instance. Used when no SchedulingPolicy is attached to
    the AppointmentType or Location.
    """
    slot_interval_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    min_lead_time_hours: int
    max_advance_days: int
    max_active_bookings_per_citizen: int
    booking_frequency_days: int
    waitlist_enabled: bool
    waitlist_acceptance_window_hours: int
    max_waitlist_per_slot: int
    waitlist_notify_batch_size: int
    no_show_warning_threshold: int
    no_show_suspension_threshold: int
    cancellation_notice_hours: int
    reschedule_notice_hours: int
    max_reschedule_count: int


def _policy_from_settings() -> _SettingsPolicy:
    """Build a _SettingsPolicy from CIVICOS['APPOINTMENTS'] with safe defaults."""
    s = settings.CIVICOS.get("APPOINTMENTS", {})
    return _SettingsPolicy(
        slot_interval_minutes=s.get("DEFAULT_SLOT_INTERVAL_MINUTES", 15),
        buffer_before_minutes=s.get("DEFAULT_BUFFER_BEFORE_MINUTES", 0),
        buffer_after_minutes=s.get("DEFAULT_BUFFER_AFTER_MINUTES", 0),
        min_lead_time_hours=s.get("DEFAULT_MIN_LEAD_HOURS", 1),
        max_advance_days=s.get("DEFAULT_MAX_ADVANCE_DAYS", 180),
        max_active_bookings_per_citizen=s.get("DEFAULT_MAX_ACTIVE_BOOKINGS", 3),
        booking_frequency_days=0,
        waitlist_enabled=True,
        waitlist_acceptance_window_hours=s.get("WAITLIST_ACCEPTANCE_WINDOW_HOURS", 2),
        max_waitlist_per_slot=10,
        waitlist_notify_batch_size=s.get("WAITLIST_NOTIFY_BATCH_SIZE", 3),
        no_show_warning_threshold=1,
        no_show_suspension_threshold=s.get("GLOBAL_NO_SHOW_SUSPENSION_THRESHOLD", 3),
        cancellation_notice_hours=24,
        reschedule_notice_hours=24,
        max_reschedule_count=3,
    )


def _resolve_policy(appointment_type, location=None):
    """
    Three-level policy fallback:
      1. AppointmentType.scheduling_policy (most specific)
      2. Location.scheduling_policy
      3. settings.CIVICOS['APPOINTMENTS'] defaults (global fallback)
    Returns a SchedulingPolicy instance or _SettingsPolicy proxy.
    """
    policy = appointment_type.get_effective_policy()
    if policy is not None:
        return policy

    if location is not None:
        loc_policy = location.get_effective_policy()
        if loc_policy is not None:
            return loc_policy

    return _policy_from_settings()


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

def _overlaps(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
) -> bool:
    """Return True if interval [a_start, a_end) overlaps [b_start, b_end)."""
    return a_start < b_end and a_end > b_start


# ---------------------------------------------------------------------------
# SlotAvailabilityService
# ---------------------------------------------------------------------------

class SlotAvailabilityService:
    """
    Computes available appointment slots for a given AppointmentType over a
    date range. Mirrors the algorithm specified in SPEC_APPOINTMENTS_BB.md §7.

    Usage::
        svc = SlotAvailabilityService()
        slots = svc.get_available_slots(
            appointment_type=appt_type,
            date_from=date(2026, 7, 15),
            date_to=date(2026, 7, 22),
            location=location,
        )

    Each returned dict has keys:
        slot_id           — str(UUID) if DB-persisted, None if computed
        staff_id          — StaffProfile PK
        location_id       — Location PK
        appointment_type_id — AppointmentType PK
        start_datetime    — timezone-aware UTC datetime
        end_datetime      — timezone-aware UTC datetime
        effective_start   — UTC datetime (start minus buffer_before)
        effective_end     — UTC datetime (end plus buffer_after)
        timezone          — IANA string (e.g. "America/Toronto")
        start_local       — datetime in location timezone
        available_spaces  — int (from DB if persisted, capacity_per_slot otherwise)
        waitlist_count    — int (from DB if persisted, 0 otherwise)
    """

    def get_available_slots(
        self,
        appointment_type,
        date_from: date,
        date_to: date,
        location=None,
        staff=None,
        citizen=None,
    ) -> list[dict]:
        """
        Return available slots for appointment_type over [date_from, date_to].

        Args:
            appointment_type: AppointmentType to check availability for.
            date_from:        First date to include (inclusive).
            date_to:          Last date to include (inclusive).
            location:         Optional Location filter.
            staff:            Optional StaffProfile filter.
            citizen:          Optional User — used for booking_frequency_days check.

        Returns:
            List of slot dicts, sorted by start_datetime.
        """
        from apps.appointments.models import (
            AppointmentType,
            AvailabilityTemplate,
            Slot,
            StaffException,
            StaffProfile,
        )

        # 1. Resolve eligible staff
        staff_qs = StaffProfile.objects.filter(
            appointment_types=appointment_type,
            is_accepting_bookings=True,
        ).select_related("location")

        if staff is not None:
            staff_qs = staff_qs.filter(pk=staff.pk)

        if location is not None:
            staff_qs = staff_qs.filter(location=location)

        eligible_staff = list(staff_qs)

        if not eligible_staff:
            logger.debug(
                "get_available_slots: no eligible staff for appointment_type=%s",
                appointment_type.pk,
            )
            return []

        # 2. Resolve policy (use first eligible staff's location for fallback)
        first_location = eligible_staff[0].location
        policy = _resolve_policy(appointment_type, location=first_location)

        duration_minutes = appointment_type.duration_minutes
        buffer_before = policy.buffer_before_minutes
        buffer_after = policy.buffer_after_minutes
        slot_interval = policy.slot_interval_minutes
        min_lead_hours = policy.min_lead_time_hours
        max_advance = policy.max_advance_days

        now_utc = timezone.now()
        earliest_allowed = now_utc + timedelta(hours=min_lead_hours)
        latest_allowed = (
            now_utc + timedelta(days=max_advance)
            if max_advance > 0
            else None
        )

        # 3. Frequency control — check if citizen is within cooldown window
        citizen_blocked = False
        if citizen is not None and policy.booking_frequency_days > 0:
            citizen_blocked = self._citizen_within_frequency_window(
                citizen=citizen,
                appointment_type=appointment_type,
                frequency_days=policy.booking_frequency_days,
                now_utc=now_utc,
            )

        if citizen_blocked:
            logger.debug(
                "get_available_slots: citizen blocked by frequency policy (citizen_id=%s)",
                citizen.pk,
            )
            return []

        results: list[dict] = []

        # Iterate dates x staff
        current_date = date_from
        while current_date <= date_to:
            for staff_member in eligible_staff:
                staff_location = staff_member.location
                if staff_location is None:
                    logger.warning(
                        "get_available_slots: StaffProfile #%s has no location — skipping",
                        staff_member.pk,
                    )
                    continue

                tz_name = staff_location.timezone or "America/Toronto"
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    logger.warning(
                        "get_available_slots: invalid timezone '%s' for location #%s — using UTC",
                        tz_name, staff_location.pk,
                    )
                    tz = ZoneInfo("UTC")

                # a. Find availability templates for this date's ISO weekday
                iso_weekday = current_date.isoweekday()  # 1=Mon, 7=Sun
                templates = list(
                    AvailabilityTemplate.objects.filter(
                        staff=staff_member,
                        day_of_week=iso_weekday,
                        valid_from__lte=current_date,
                    ).filter(
                        models_Q_valid_until(current_date)
                    )
                )

                if not templates:
                    continue  # No availability this day

                # b. Check for StaffException on this date
                try:
                    exception = StaffException.objects.get(
                        staff=staff_member,
                        exception_date=current_date,
                    )
                    if exception.exception_type in ("holiday", "leave", "training"):
                        continue  # Staff unavailable all day
                    # override — use override hours
                    avail_windows = [
                        (exception.override_start_time, exception.override_end_time)
                    ]
                except StaffException.DoesNotExist:
                    # No exception — use all template windows
                    avail_windows = [
                        (tpl.start_time, tpl.end_time) for tpl in templates
                    ]

                # c. Collect existing busy times for this staff on this date (UTC)
                # Build explicit UTC window for this local calendar day to avoid
                # date-vs-UTC mismatch (e.g. America/Vancouver late slots fall on
                # the next UTC date).
                _day_start_utc = datetime(
                    current_date.year, current_date.month, current_date.day,
                    tzinfo=tz,
                ).astimezone(ZoneInfo("UTC"))
                _day_end_utc = _day_start_utc + timedelta(days=1)

                busy_times = list(
                    Slot.objects.filter(
                        staff=staff_member,
                        start_datetime__gte=_day_start_utc,
                        start_datetime__lt=_day_end_utc,
                        status__in=("available", "partial", "full", "blocked"),
                    ).values_list("effective_start", "effective_end")
                )

                # d. Daily appointment cap check
                # Use Sum("spaces_used") so group slots count their actual
                # occupied spaces, not just row count.
                daily_cap = staff_member.max_daily_appointments
                if daily_cap is not None:
                    confirmed_today = (
                        Slot.objects.filter(
                            staff=staff_member,
                            start_datetime__gte=_day_start_utc,
                            start_datetime__lt=_day_end_utc,
                            spaces_used__gt=0,
                        ).aggregate(total=Sum("spaces_used"))["total"]
                        or 0
                    )
                    if confirmed_today >= daily_cap:
                        continue  # Staff is at daily cap

                # e. Generate candidate slots for each availability window
                for avail_start_local, avail_end_local in avail_windows:
                    if not avail_start_local or not avail_end_local:
                        continue

                    # Convert local times to UTC
                    avail_start_utc = datetime(
                        current_date.year, current_date.month, current_date.day,
                        avail_start_local.hour, avail_start_local.minute,
                        tzinfo=tz,
                    ).astimezone(ZoneInfo("UTC"))
                    avail_end_utc = datetime(
                        current_date.year, current_date.month, current_date.day,
                        avail_end_local.hour, avail_end_local.minute,
                        tzinfo=tz,
                    ).astimezone(ZoneInfo("UTC"))

                    # Walk the interval grid
                    current_slot_start = avail_start_utc
                    slot_duration = timedelta(minutes=duration_minutes)
                    slot_stride = timedelta(minutes=slot_interval)
                    buffer_before_td = timedelta(minutes=buffer_before)
                    buffer_after_td = timedelta(minutes=buffer_after)

                    while True:
                        slot_end = current_slot_start + slot_duration
                        # Check if slot (with buffer_after) fits within availability window
                        if slot_end + buffer_after_td > avail_end_utc:
                            break

                        eff_start = current_slot_start - buffer_before_td
                        eff_end = slot_end + buffer_after_td

                        # min_lead_time filter
                        if current_slot_start < earliest_allowed:
                            current_slot_start += slot_stride
                            continue

                        # max_advance_days filter
                        if latest_allowed is not None and current_slot_start > latest_allowed:
                            break

                        # Busy-time collision check
                        collision = any(
                            _overlaps(eff_start, eff_end, b_start, b_end)
                            for b_start, b_end in busy_times
                        )
                        if not collision:
                            start_local = current_slot_start.astimezone(tz)
                            results.append({
                                "slot_id": None,  # Not yet persisted
                                "staff_id": staff_member.pk,
                                "location_id": staff_location.pk,
                                "appointment_type_id": appointment_type.pk,
                                "start_datetime": current_slot_start,
                                "end_datetime": slot_end,
                                "effective_start": eff_start,
                                "effective_end": eff_end,
                                "timezone": tz_name,
                                "start_local": start_local,
                                "available_spaces": appointment_type.capacity_per_slot,
                                "waitlist_count": 0,
                            })

                        current_slot_start += slot_stride

            current_date += timedelta(days=1)

        # Sort by start time
        results.sort(key=lambda s: s["start_datetime"])
        return results

    def _citizen_within_frequency_window(
        self,
        citizen,
        appointment_type,
        frequency_days: int,
        now_utc: datetime,
    ) -> bool:
        """
        Return True if the citizen has a confirmed or completed booking for
        this appointment_type's service_type within the frequency_days window.

        Used for food bank / frequency-control services.
        Privacy: no PII logged — citizen referenced by pk only.
        """
        # Booking model is Wave 3 — guard against import error
        try:
            from apps.appointments.models import Booking  # Wave 3
        except ImportError:
            return False

        cutoff = now_utc - timedelta(days=frequency_days)
        return Booking.objects.filter(
            citizen=citizen,
            slot__appointment_type__service_type=appointment_type.service_type,
            status__in=("confirmed", "completed"),
            slot__start_datetime__gte=cutoff,
        ).exists()


def models_Q_valid_until(target_date: date):
    """Return a Q object filtering AvailabilityTemplate.valid_until for the given date."""
    from django.db.models import Q
    return Q(valid_until__isnull=True) | Q(valid_until__gte=target_date)
