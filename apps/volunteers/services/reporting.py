"""
Volunteer impact and T3010 reporting service.

All functions return plain Python dicts / querysets — no Django response objects.
Callers (tasks, views) are responsible for serialisation.

PIPEDA: No volunteer names or contact details in any aggregate or return value.
        Only programme-level totals, counts, and computed values.

Model field notes (discovered by reading models.py):
  - HoursLog.date          — date field (NOT log_date)
  - HoursLog.volunteer     — FK to VolunteerProfile
  - HoursLog.opportunity   — FK to Opportunity (direct, NOT via VolunteerApplication)
  - HoursLog.shift         — nullable FK to Shift
  - HoursLog.STATUS_APPROVED = "approved"
  - Opportunity.required_skills — M2M to SkillTag (NOT skill_tags)
  - SkillTag.name_en, SkillTag.slug, SkillTag.category
  - Program.slug, Program.name_en
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category mapping helpers
# ---------------------------------------------------------------------------

# Whole-word regex patterns for T3010 volunteer category classification.
# Uses word boundaries to avoid false positives (e.g. "fund" matching "fundamental",
# "program" matching "programming").
_CATEGORY_PATTERNS = [
    ("governance", re.compile(r"\b(governance|board|director|trustee|bylaws)\b", re.IGNORECASE)),
    (
        "fundraising",
        re.compile(r"\b(fundraising|fundraiser|donor|donation|campaign|grant)\b", re.IGNORECASE),
    ),
    (
        "program_delivery",
        re.compile(
            r"\b(program[\s_-]delivery|programme[\s_-]delivery|service[\s_-]delivery|delivery)\b",
            re.IGNORECASE,
        ),
    ),
]


def _classify_skill_tags(tag_slugs: list[str], tag_names: list[str]) -> str:
    """
    Map a list of SkillTag slugs and name_en values to a T3010 category string.

    Returns one of: "governance" | "fundraising" | "program_delivery" | "general"

    Priority order: governance > fundraising > program_delivery > general.

    Uses whole-word regex matching to avoid false positives from substring checks
    (e.g. "fund" in "fundamental", "program" in "programming").
    Slug hyphens and underscores are converted to spaces before matching.
    """
    # Normalise slugs: replace hyphens and underscores with spaces for word-boundary matching
    normalised_slugs = [s.replace("-", " ").replace("_", " ") for s in tag_slugs]
    combined = " ".join(normalised_slugs + tag_names)

    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(combined):
            return category

    return "general"


# ---------------------------------------------------------------------------
# Function 1: hours_by_program
# ---------------------------------------------------------------------------


def hours_by_program(
    year: int, month: int | None = None, program_ids: list | None = None
) -> list[dict]:
    """
    Return approved volunteer hours grouped by (program, opportunity).

    Each item:
        program_slug        str
        program_title_en    str
        opportunity_slug    str
        opportunity_title_en str
        approved_hours      Decimal   — sum of HoursLog.hours where status=APPROVED
        volunteer_count     int       — distinct volunteers with approved hours
        shift_count         int       — distinct shifts logged (HoursLog.shift, nullable)

    Filter: HoursLog.status == APPROVED, date__year == year.
    If month is provided, also filters date__month == month.

    Single DB query using .values() + .annotate().

    PIPEDA: only programme-level aggregates returned; no individual volunteer data.
    """
    from apps.volunteers.models import HoursLog

    filters = Q(
        status=HoursLog.STATUS_APPROVED,
        date__year=year,
        opportunity__isnull=False,
    )
    if month is not None:
        filters &= Q(date__month=month)

    qs = HoursLog.objects.filter(filters)
    if program_ids is not None:
        qs = qs.filter(opportunity__program_id__in=program_ids)

    rows = (
        qs.values(
            "opportunity__program__slug",
            "opportunity__program__name_en",
            "opportunity__slug",
            "opportunity__title_en",
        )
        .annotate(
            approved_hours=Coalesce(
                Sum("hours"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            volunteer_count=Count("volunteer", distinct=True),
            shift_count=Count("shift", distinct=True, filter=Q(shift__isnull=False)),
        )
        .order_by(
            "opportunity__program__name_en",
            "opportunity__title_en",
        )
    )

    return [
        {
            "program_slug": row["opportunity__program__slug"],
            "program_title_en": row["opportunity__program__name_en"],
            "opportunity_slug": row["opportunity__slug"],
            "opportunity_title_en": row["opportunity__title_en"],
            "approved_hours": row["approved_hours"],
            "volunteer_count": row["volunteer_count"],
            "shift_count": row["shift_count"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Function 2: impact_value
# ---------------------------------------------------------------------------


def impact_value(year: int, province: str = "ON", program_ids: list | None = None) -> dict:
    """
    Compute the estimated economic value of volunteer hours for a given year.

    Returns:
        year                int
        province            str
        hourly_rate         Decimal   — from settings.VOLUNTEER_MINIMUM_WAGES[province]
        total_approved_hours Decimal
        estimated_value_cad Decimal   — total_approved_hours × hourly_rate
        volunteer_count     int
        opportunity_count   int

    If province is not in VOLUNTEER_MINIMUM_WAGES, defaults to "ON" with a WARNING.
    PIPEDA: no volunteer PII in return value.
    """  # noqa: RUF002
    from apps.volunteers.models import HoursLog

    wages: dict = getattr(settings, "VOLUNTEER_MINIMUM_WAGES", {"ON": 17.20})

    if province not in wages:
        logger.warning(
            "volunteers.services.reporting.impact_value: unknown province %r — "
            "defaulting to ON. Valid codes: %s",
            province,
            ", ".join(sorted(wages.keys())),
        )
        province = "ON"

    hourly_rate = Decimal(str(wages[province]))

    qs = HoursLog.objects.filter(
        status=HoursLog.STATUS_APPROVED,
        date__year=year,
    )
    if program_ids is not None:
        qs = qs.filter(opportunity__program_id__in=program_ids)

    agg = qs.aggregate(
        total_approved_hours=Coalesce(
            Sum("hours"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        volunteer_count=Count("volunteer", distinct=True),
        opportunity_count=Count("opportunity", distinct=True, filter=Q(opportunity__isnull=False)),
    )

    total_hours = agg["total_approved_hours"]
    estimated_value = (total_hours * hourly_rate).quantize(Decimal("0.01"))

    return {
        "year": year,
        "province": province,
        "hourly_rate": hourly_rate,
        "total_approved_hours": total_hours,
        "estimated_value_cad": estimated_value,
        "volunteer_count": agg["volunteer_count"],
        "opportunity_count": agg["opportunity_count"],
    }


# ---------------------------------------------------------------------------
# Function 3: t3010_volunteer_metrics
# ---------------------------------------------------------------------------


def t3010_volunteer_metrics(year: int, program_ids: list | None = None) -> dict:
    """
    Compute CRA T3010 Schedule 2 "Volunteers" section data for a given year.

    Returns:
        year                    int
        total_volunteers        int       — distinct VolunteerProfiles with ≥1 approved log
        total_volunteer_hours   Decimal   — sum of approved HoursLog.hours
        num_programs            int       — distinct Programs with volunteer activity
        estimated_value_cad     Decimal   — using Ontario rate (single national estimate)
        categories              list[dict]
            category    str   — "general"|"governance"|"fundraising"|"program_delivery"
            hours       Decimal
            volunteers  int

    Category mapping uses Opportunity.required_skills SkillTag slugs and name_en
    values. If an opportunity has no skill tags, or if slugs/names do not match
    any keyword group, hours are counted as "general".

    PIPEDA: aggregates only; no volunteer PII.
    """
    from apps.volunteers.models import HoursLog, Opportunity

    wages: dict = getattr(settings, "VOLUNTEER_MINIMUM_WAGES", {"ON": 17.20})
    on_rate = Decimal(str(wages.get("ON", 17.20)))

    approved_qs = HoursLog.objects.filter(
        status=HoursLog.STATUS_APPROVED,
        date__year=year,
    )
    if program_ids is not None:
        approved_qs = approved_qs.filter(opportunity__program_id__in=program_ids)

    agg = approved_qs.aggregate(
        total_volunteers=Count("volunteer", distinct=True),
        total_hours=Coalesce(
            Sum("hours"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        num_programs=Count(
            "opportunity__program",
            distinct=True,
            filter=Q(opportunity__isnull=False),
        ),
    )

    total_hours: Decimal = agg["total_hours"]
    estimated_value = (total_hours * on_rate).quantize(Decimal("0.01"))

    # ── Category breakdown ─────────────────────────────────────────────────
    # Fetch per-opportunity aggregates, then classify each opportunity using
    # its required_skills tags. This keeps DB round-trips to two total.

    opp_agg = (
        approved_qs.filter(opportunity__isnull=False)
        .values("opportunity__pk")
        .annotate(
            opp_hours=Coalesce(
                Sum("hours"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            opp_volunteers=Count("volunteer", distinct=True),
        )
    )

    # Build opp_pk → {hours, volunteers} map
    opp_data: dict[int, dict] = {
        row["opportunity__pk"]: {
            "hours": row["opp_hours"],
            "volunteers": row["opp_volunteers"],
        }
        for row in opp_agg
    }

    # Log entries with opportunity__isnull=True (manually logged without an opp)
    # are bucketed into "general" at the end.
    null_opp_agg = approved_qs.filter(opportunity__isnull=True).aggregate(
        null_hours=Coalesce(
            Sum("hours"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        null_volunteers=Count("volunteer", distinct=True),
    )

    # Fetch skill tags for all relevant opportunities in one query.
    opp_pks = list(opp_data.keys())
    # prefetch_related equivalent via values() on the through table
    opp_tags = Opportunity.objects.filter(pk__in=opp_pks).values(
        "pk", "required_skills__slug", "required_skills__name_en"
    )

    # Build opp_pk → {slugs, names} map
    opp_tag_map: dict[int, dict[str, list[str]]] = {}
    for row in opp_tags:
        pk = row["pk"]
        if pk not in opp_tag_map:
            opp_tag_map[pk] = {"slugs": [], "names": []}
        if row["required_skills__slug"]:
            opp_tag_map[pk]["slugs"].append(row["required_skills__slug"])
        if row["required_skills__name_en"]:
            opp_tag_map[pk]["names"].append(row["required_skills__name_en"])

    # Accumulate by category
    category_totals: dict[str, dict] = {
        "general": {"hours": Decimal("0.00"), "volunteers": 0},
        "governance": {"hours": Decimal("0.00"), "volunteers": 0},
        "fundraising": {"hours": Decimal("0.00"), "volunteers": 0},
        "program_delivery": {"hours": Decimal("0.00"), "volunteers": 0},
    }

    for opp_pk, data in opp_data.items():
        tags = opp_tag_map.get(opp_pk, {"slugs": [], "names": []})
        cat = _classify_skill_tags(tags["slugs"], tags["names"])
        category_totals[cat]["hours"] += data["hours"]
        # volunteer counts per category are not strictly additive across
        # opportunities (a volunteer may work multiple opps in same category).
        # We report per-category volunteer counts as a simple sum of distinct
        # volunteers per opportunity — note in T3010 only total_volunteers is
        # the authoritative figure; category volunteer counts are indicative.
        category_totals[cat]["volunteers"] += data["volunteers"]

    # Add null-opportunity hours to "general"
    category_totals["general"]["hours"] += null_opp_agg["null_hours"]
    category_totals["general"]["volunteers"] += null_opp_agg["null_volunteers"]

    categories = [
        {
            "category": cat,
            "hours": totals["hours"],
            "volunteers": totals["volunteers"],
        }
        for cat, totals in category_totals.items()
        if totals["hours"] > Decimal("0") or totals["volunteers"] > 0
    ]

    # Ensure consistent output order even when some categories are zero
    _cat_order = ["general", "governance", "fundraising", "program_delivery"]
    categories.sort(
        key=lambda c: _cat_order.index(c["category"]) if c["category"] in _cat_order else 99
    )

    return {
        "year": year,
        "total_volunteers": agg["total_volunteers"],
        "total_volunteer_hours": total_hours,
        "num_programs": agg["num_programs"],
        "estimated_value_cad": estimated_value,
        "categories": categories,
    }
