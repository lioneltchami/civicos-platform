"""
Analytics & Reporting BB — Models.

Two models:
- ReportSnapshot: pre-computed monthly aggregate, one row per (report_type, period_year,
  period_month). Updated nightly by the compute_monthly_snapshots Celery task.
  Never auto-deleted — 7-year CRA retention requirement.

- ExportRecord: audit trail of every export download (CSV/Excel/PDF). No file content
  stored server-side — reports are streamed directly. Logs actor_pk (UUID), not email
  (PIPEDA: no PII in audit logs).

PIPEDA note: neither model stores any personal information. All donor/payer data is
aggregated before storage; individual-level detail is only ever streamed transiently
during an export response.
"""
from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class ReportSnapshot(models.Model):
    """
    Pre-computed monthly aggregate for one report domain.

    Computation strategy:
    - Prior months: served from this table (computed nightly by Celery Beat).
    - Current month: queried in real-time from source models on each page load.

    The ``data`` field holds a typed dict specific to each ``report_type`` — see
    docs/analytics-reporting-bb-spec.md §4.1 for the full JSON schema per type.

    Retention: financial record — must not be auto-deleted. Admin enforces
    has_delete_permission=False. Minimum 7-year retention (CRA requirement).
    """

    REPORT_TYPE_FINANCIAL = "financial"
    REPORT_TYPE_DONATIONS = "donations"
    REPORT_TYPE_OPERATIONAL = "operational"
    REPORT_TYPE_CHOICES = [
        (REPORT_TYPE_FINANCIAL, _("Financial")),
        (REPORT_TYPE_DONATIONS, _("Donations & CRA")),
        (REPORT_TYPE_OPERATIONAL, _("Operational")),
    ]

    report_type = models.CharField(
        max_length=20,
        choices=REPORT_TYPE_CHOICES,
        verbose_name=_("Report type"),
    )
    period_year = models.PositiveSmallIntegerField(
        verbose_name=_("Period year"),
        help_text=_("Calendar year, e.g. 2025."),
    )
    period_month = models.PositiveSmallIntegerField(
        verbose_name=_("Period month"),
        help_text=_("Calendar month 1–12."),
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    data = models.JSONField(
        verbose_name=_("Aggregated data"),
        help_text=_(
            "Pre-computed aggregate metrics dict. Schema varies by report_type — "
            "see docs/analytics-reporting-bb-spec.md §4.1."
        ),
    )
    row_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Source row count"),
        help_text=_("Number of source rows aggregated into this snapshot."),
    )
    computed_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Last computed at"),
    )

    class Meta:
        verbose_name = _("Report snapshot")
        verbose_name_plural = _("Report snapshots")
        unique_together = [("report_type", "period_year", "period_month")]
        # No additional index on (report_type, period_year, period_month) —
        # the unique_together constraint above already creates an implicit B-tree
        # index on these three columns. A second explicit index would be redundant
        # and waste write overhead on every snapshot upsert.
        ordering = ["-period_year", "-period_month", "report_type"]

    def __str__(self) -> str:
        return (
            f"{self.get_report_type_display()} "
            f"{self.period_year}-{self.period_month:02d}"
        )

    @property
    def period_label(self) -> str:
        """Human-readable period label, e.g. 'March 2025'."""
        import calendar
        return f"{calendar.month_name[self.period_month]} {self.period_year}"


class ExportRecord(models.Model):
    """
    Audit trail for every report export download.

    Records who exported what, when, and how many rows. No file content is
    stored — reports are streamed directly in the HTTP response. This record
    exists solely to satisfy PIPEDA accountability and CRA audit requirements.

    PIPEDA invariants:
    - actor_pk stores the user's UUID primary key, never their email address.
    - actor_ip is masked at the IPv4 /24 or IPv6 /48 level before storage
      (consistent with the sentry.py masking policy).
    - No donor name, email, address, or SIN is ever stored here.
    """

    EXPORT_TYPE_RECONCILIATION = "reconciliation"
    EXPORT_TYPE_T3010 = "t3010_prep"
    EXPORT_TYPE_RECEIPTS = "receipts"
    EXPORT_TYPE_REVENUE = "revenue"
    EXPORT_TYPE_REFUNDS = "refunds"
    EXPORT_TYPE_CHOICES = [
        (EXPORT_TYPE_RECONCILIATION, _("Payment reconciliation")),
        (EXPORT_TYPE_T3010, _("T3010 preparatory data")),
        (EXPORT_TYPE_RECEIPTS, _("Donation receipts list")),
        (EXPORT_TYPE_REVENUE, _("Monthly revenue")),
        (EXPORT_TYPE_REFUNDS, _("Refund summary")),
    ]

    FORMAT_CSV = "csv"
    FORMAT_EXCEL = "xlsx"
    FORMAT_PDF = "pdf"
    FORMAT_CHOICES = [
        (FORMAT_CSV, "CSV"),
        (FORMAT_EXCEL, "Excel (.xlsx)"),
        (FORMAT_PDF, "PDF"),
    ]

    export_type = models.CharField(
        max_length=30,
        choices=EXPORT_TYPE_CHOICES,
        verbose_name=_("Export type"),
    )
    format = models.CharField(
        max_length=5,
        choices=FORMAT_CHOICES,
        verbose_name=_("Format"),
    )
    period_start = models.DateField(verbose_name=_("Period start"))
    period_end = models.DateField(verbose_name=_("Period end"))
    actor_pk = models.UUIDField(
        verbose_name=_("Actor PK"),
        help_text=_("UUID primary key of the staff user who triggered this export."),
    )
    actor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("Actor IP (masked)"),
        help_text=_(
            "Client IP with last octet (IPv4) or last 80 bits (IPv6) zeroed "
            "before storage — PIPEDA data minimization."
        ),
    )
    row_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Row count"),
        help_text=_("Number of data rows included in the export."),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Exported at"),
    )

    class Meta:
        verbose_name = _("Export record")
        verbose_name_plural = _("Export records")
        indexes = [
            models.Index(
                fields=["export_type", "created_at"],
                name="rpt_exp_type_ts_idx",
            ),
            models.Index(
                fields=["actor_pk", "created_at"],
                name="rpt_exp_actor_ts_idx",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return (
            f"{self.get_export_type_display()} "
            f"({self.format.upper()}) "
            f"{self.period_start}–{self.period_end}"
        )
