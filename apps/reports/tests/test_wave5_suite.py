"""
Wave 5 — Comprehensive test suite for apps/reports.

Covers gaps not addressed in waves 2, 3, and 4:

  Models
  -------
  - ReportSnapshot: __str__, period_label, unique_together, ordering,
    computed_at auto_now, upsert idempotency
  - ExportRecord: __str__, field types, actor_pk bigint, actor_ip nullable,
    created_at auto_now_add, choices

  CSV utilities (apps/reports/exports/csv_export.py)
  ---------------------------------------------------
  - _EchoBuffer.write() — passthrough return value
  - _sanitize_csv_cell — all six formula-injection triggers, safe strings,
    non-string coercion
  - streaming_csv_response — column whitelist, UTF-8 BOM, Content-Disposition
    filename, Content-Type, row streaming (dict and sequence), formula
    injection in values neutralised

  Tasks (apps/reports/tasks.py)
  ------------------------------
  - recompute_snapshot: financial type → writes REPORT_TYPE_FINANCIAL snapshot
  - recompute_snapshot: donations type → writes REPORT_TYPE_DONATIONS snapshot
  - recompute_snapshot: idempotent (update_or_create, no duplicate)
  - _compute_single_snapshot: unknown type raises ValueError
  - _compute_single_snapshot: dispatches all three known types
  - _compute_all_snapshots: writes all three snapshot types in one call
  - _compute_all_snapshots: idempotent (re-run updates, not duplicates)

  Regression
  ----------
  - ExportRecord written AFTER PDF generation in MonthlySummaryPdfView
    (if WeasyPrint raises, no phantom audit record is created)

PIPEDA invariants:
  - actor_pk is BigIntegerField — never stores email or name
  - actor_ip nullable — masked before storage
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.reports.exports.csv_export import (
    _EchoBuffer,
    _sanitize_csv_cell,
    streaming_csv_response,
)
from apps.reports.models import ExportRecord, ReportSnapshot
from apps.reports.tasks import _compute_all_snapshots, _compute_single_snapshot


# ---------------------------------------------------------------------------
# ReportSnapshot model tests
# ---------------------------------------------------------------------------

class ReportSnapshotStrTest(TestCase):
    """__str__ returns 'Financial YYYY-MM' style string."""

    def test_str_financial(self):
        snap = ReportSnapshot(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=3,
            data={},
        )
        self.assertIn("Financial", str(snap))
        self.assertIn("2025", str(snap))
        self.assertIn("03", str(snap))

    def test_str_donations(self):
        snap = ReportSnapshot(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2024,
            period_month=12,
            data={},
        )
        self.assertIn("Donations", str(snap))
        self.assertIn("12", str(snap))

    def test_str_operational(self):
        snap = ReportSnapshot(
            report_type=ReportSnapshot.REPORT_TYPE_OPERATIONAL,
            period_year=2026,
            period_month=1,
            data={},
        )
        self.assertIn("Operational", str(snap))
        self.assertIn("01", str(snap))


class ReportSnapshotPeriodLabelTest(TestCase):
    """period_label property returns human-readable 'Month YYYY' string."""

    def test_label_january(self):
        snap = ReportSnapshot(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=1,
            data={},
        )
        self.assertEqual(snap.period_label, "January 2025")

    def test_label_june(self):
        snap = ReportSnapshot(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2024,
            period_month=6,
            data={},
        )
        self.assertEqual(snap.period_label, "June 2024")

    def test_label_december(self):
        snap = ReportSnapshot(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2023,
            period_month=12,
            data={},
        )
        self.assertEqual(snap.period_label, "December 2023")

    def test_all_months_have_non_empty_label(self):
        for m in range(1, 13):
            snap = ReportSnapshot(
                report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
                period_year=2025,
                period_month=m,
                data={},
            )
            self.assertTrue(snap.period_label.strip(), f"Empty label for month {m}")


class ReportSnapshotUniqueTogetherTest(TestCase):
    """unique_together prevents duplicate (report_type, period_year, period_month)."""

    def test_duplicate_raises_integrity_error(self):
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=6,
            data={"version": 1},
        )
        with self.assertRaises(IntegrityError):
            ReportSnapshot.objects.create(
                report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
                period_year=2025,
                period_month=6,
                data={"version": 2},
            )

    def test_same_month_different_type_allowed(self):
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=6,
            data={},
        )
        # Should not raise
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2025,
            period_month=6,
            data={},
        )
        self.assertEqual(
            ReportSnapshot.objects.filter(period_year=2025, period_month=6).count(), 2
        )


class ReportSnapshotUpsertIdempotencyTest(TestCase):
    """update_or_create is idempotent — re-run updates, never duplicates."""

    def test_update_or_create_no_duplicate(self):
        for version in (1, 2, 3):
            ReportSnapshot.objects.update_or_create(
                report_type=ReportSnapshot.REPORT_TYPE_OPERATIONAL,
                period_year=2025,
                period_month=4,
                defaults={"data": {"version": version}, "row_count": version},
            )
        # Still exactly one row after three upserts
        self.assertEqual(
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_OPERATIONAL,
                period_year=2025,
                period_month=4,
            ).count(),
            1,
        )
        # Row was updated to the latest version
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_OPERATIONAL,
            period_year=2025,
            period_month=4,
        )
        self.assertEqual(snap.data["version"], 3)
        self.assertEqual(snap.row_count, 3)

    def test_computed_at_updated_on_re_save(self):
        """
        computed_at (auto_now=True) is updated to exactly the mocked 'now'
        on each save.  We freeze time to a known later instant so the assertion
        is deterministic — no sleep, no timestamp-resolution dependency.
        """
        t1 = datetime(2025, 6, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
        t2 = datetime(2025, 6, 1, 10, 0, 1, tzinfo=dt_timezone.utc)  # strictly later

        with patch("django.utils.timezone.now", return_value=t1):
            snap, created = ReportSnapshot.objects.update_or_create(
                report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
                period_year=2025,
                period_month=5,
                defaults={"data": {}, "row_count": 0},
            )
        self.assertTrue(created)

        with patch("django.utils.timezone.now", return_value=t2):
            snap2, created2 = ReportSnapshot.objects.update_or_create(
                report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
                period_year=2025,
                period_month=5,
                defaults={"data": {"v": 2}, "row_count": 1},
            )
        self.assertFalse(created2)
        # Strict greater-than: proves auto_now advanced, not just "didn't go back"
        self.assertGreater(snap2.computed_at, snap.computed_at)


class ReportSnapshotOrderingTest(TestCase):
    """Default ordering is -period_year, -period_month, report_type."""

    def test_ordering_descending_year_month(self):
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2024,
            period_month=1,
            data={},
        )
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=1,
            data={},
        )
        rows = list(ReportSnapshot.objects.all())
        self.assertEqual(rows[0].period_year, 2025)
        self.assertEqual(rows[1].period_year, 2024)

    def test_ordering_descending_month_within_year(self):
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=3,
            data={},
        )
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2025,
            period_month=1,
            data={},
        )
        rows = list(ReportSnapshot.objects.filter(period_year=2025))
        self.assertEqual(rows[0].period_month, 3)
        self.assertEqual(rows[1].period_month, 1)

    def test_row_count_defaults_to_zero(self):
        snap = ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=7,
            data={},
        )
        self.assertEqual(snap.row_count, 0)


# ---------------------------------------------------------------------------
# ExportRecord model tests
# ---------------------------------------------------------------------------

class ExportRecordStrTest(TestCase):
    """__str__ returns export type display + format + period range."""

    def test_str_csv(self):
        rec = ExportRecord(
            export_type=ExportRecord.EXPORT_TYPE_RECONCILIATION,
            format=ExportRecord.FORMAT_CSV,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            actor_pk=42,
        )
        s = str(rec)
        # Should mention CSV and the period
        self.assertIn("CSV", s)
        self.assertIn("2025-01-01", s)

    def test_str_pdf(self):
        rec = ExportRecord(
            export_type=ExportRecord.EXPORT_TYPE_REVENUE,
            format=ExportRecord.FORMAT_PDF,
            period_start=date(2025, 6, 1),
            period_end=date(2025, 6, 30),
            actor_pk=99,
        )
        s = str(rec)
        self.assertIn("PDF", s)


class ExportRecordFieldsTest(TestCase):
    """Field-level constraints and PIPEDA compliance."""

    def test_actor_pk_is_integer_not_email(self):
        """actor_pk stores integer PK — PIPEDA prohibits email/name in audit logs."""
        rec = ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_REVENUE,
            format=ExportRecord.FORMAT_CSV,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            actor_pk=12345,
            row_count=10,
        )
        # Reloaded from DB
        rec.refresh_from_db()
        self.assertEqual(rec.actor_pk, 12345)
        self.assertIsInstance(rec.actor_pk, int)

    def test_actor_ip_nullable(self):
        """actor_ip may be None when client IP is unavailable."""
        rec = ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_RECONCILIATION,
            format=ExportRecord.FORMAT_CSV,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            actor_pk=1,
            actor_ip=None,
        )
        rec.refresh_from_db()
        self.assertIsNone(rec.actor_ip)

    def test_created_at_auto_set(self):
        before = timezone.now()
        rec = ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_RECEIPTS,
            format=ExportRecord.FORMAT_CSV,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            actor_pk=1,
        )
        after = timezone.now()
        self.assertGreaterEqual(rec.created_at, before)
        self.assertLessEqual(rec.created_at, after)

    def test_row_count_defaults_to_zero(self):
        rec = ExportRecord.objects.create(
            export_type=ExportRecord.EXPORT_TYPE_REVENUE,
            format=ExportRecord.FORMAT_PDF,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
            actor_pk=1,
        )
        self.assertEqual(rec.row_count, 0)

    def test_ordering_newest_first(self):
        """
        Default ordering is -created_at (newest first).  We freeze time for
        each insert so the two records have distinct, known timestamps — avoids
        flakiness when both rows land in the same DB microsecond on fast CI.
        """
        t1 = datetime(2025, 3, 1, 9, 0, 0, tzinfo=dt_timezone.utc)
        t2 = datetime(2025, 3, 1, 9, 0, 1, tzinfo=dt_timezone.utc)  # strictly later

        with patch("django.utils.timezone.now", return_value=t1):
            ExportRecord.objects.create(
                export_type=ExportRecord.EXPORT_TYPE_REVENUE,
                format=ExportRecord.FORMAT_CSV,
                period_start=date(2025, 1, 1),
                period_end=date(2025, 1, 31),
                actor_pk=1,
            )
        with patch("django.utils.timezone.now", return_value=t2):
            ExportRecord.objects.create(
                export_type=ExportRecord.EXPORT_TYPE_RECONCILIATION,
                format=ExportRecord.FORMAT_CSV,
                period_start=date(2025, 2, 1),
                period_end=date(2025, 2, 28),
                actor_pk=2,
            )

        rows = list(ExportRecord.objects.all())
        # Most recent (t2) first
        self.assertEqual(rows[0].export_type, ExportRecord.EXPORT_TYPE_RECONCILIATION)
        self.assertEqual(rows[1].export_type, ExportRecord.EXPORT_TYPE_REVENUE)


class ExportRecordChoicesTest(TestCase):
    """All expected export_type and format constants exist."""

    def test_export_type_choices_present(self):
        expected = {
            ExportRecord.EXPORT_TYPE_RECONCILIATION,
            ExportRecord.EXPORT_TYPE_T3010,
            ExportRecord.EXPORT_TYPE_RECEIPTS,
            ExportRecord.EXPORT_TYPE_REVENUE,
            ExportRecord.EXPORT_TYPE_REFUNDS,
        }
        actual = {code for code, _ in ExportRecord.EXPORT_TYPE_CHOICES}
        self.assertEqual(expected, actual)

    def test_format_choices_present(self):
        expected = {
            ExportRecord.FORMAT_CSV,
            ExportRecord.FORMAT_EXCEL,
            ExportRecord.FORMAT_PDF,
        }
        actual = {code for code, _ in ExportRecord.FORMAT_CHOICES}
        self.assertEqual(expected, actual)

    def test_actor_pk_field_is_big_integer(self):
        """actor_pk must be BigIntegerField — not ForeignKey, not email, not CharField."""
        field = ExportRecord._meta.get_field("actor_pk")
        from django.db.models import BigIntegerField
        self.assertIsInstance(field, BigIntegerField)


# ---------------------------------------------------------------------------
# _EchoBuffer tests
# ---------------------------------------------------------------------------

class EchoBufferTest(TestCase):
    """_EchoBuffer.write() is a passthrough — returns the value it receives."""

    def test_write_returns_value(self):
        buf = _EchoBuffer()
        for v in ("hello", "", "a,b,c\r\n", "123"):
            self.assertEqual(buf.write(v), v)

    def test_write_returns_string_not_none(self):
        buf = _EchoBuffer()
        result = buf.write("x")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# _sanitize_csv_cell tests
# ---------------------------------------------------------------------------

class SanitizeCsvCellTest(TestCase):
    """_sanitize_csv_cell neutralises all six CSV formula-injection triggers."""

    # ── Injection triggers ────────────────────────────────────────────────────

    def test_equals_prefix_sanitized(self):
        result = _sanitize_csv_cell("=SUM(A1)")
        self.assertEqual(result[0], "\t", "Leading '=' should be prefixed with tab")
        self.assertIn("=SUM(A1)", result)

    def test_plus_prefix_sanitized(self):
        result = _sanitize_csv_cell("+19055551234")
        self.assertEqual(result[0], "\t")
        self.assertIn("+19055551234", result)

    def test_minus_prefix_sanitized(self):
        result = _sanitize_csv_cell("-1")
        self.assertEqual(result[0], "\t")
        self.assertIn("-1", result)

    def test_at_prefix_sanitized(self):
        result = _sanitize_csv_cell("@SUM")
        self.assertEqual(result[0], "\t")

    def test_tab_prefix_sanitized(self):
        result = _sanitize_csv_cell("\tDATA")
        self.assertEqual(result[0], "\t")
        # The original tab is preserved after the injected tab prefix
        self.assertEqual(result, "\t\tDATA")

    def test_cr_prefix_sanitized(self):
        result = _sanitize_csv_cell("\rDATA")
        self.assertEqual(result[0], "\t")

    # ── Safe inputs untouched ─────────────────────────────────────────────────

    def test_safe_string_unchanged(self):
        for safe in ("Hello", "123", "Fee code A", "Reconciliation", ""):
            self.assertEqual(_sanitize_csv_cell(safe), safe, f"Unexpected mutation: {safe!r}")

    def test_empty_string_unchanged(self):
        self.assertEqual(_sanitize_csv_cell(""), "")

    # ── Non-string coercion ───────────────────────────────────────────────────

    def test_integer_coerced_to_str(self):
        result = _sanitize_csv_cell(42)
        self.assertEqual(result, "42")

    def test_decimal_coerced_to_str(self):
        from decimal import Decimal
        result = _sanitize_csv_cell(Decimal("100.50"))
        self.assertEqual(result, "100.50")

    def test_none_coerced_to_str(self):
        result = _sanitize_csv_cell(None)
        self.assertEqual(result, "None")

    def test_non_string_with_injection_trigger_sanitized(self):
        """A formula trigger on a value that was coerced from non-string is still blocked."""
        # Simulate a value whose str() starts with '='
        class FakeVal:
            def __str__(self):
                return "=INJECTION"

        result = _sanitize_csv_cell(FakeVal())
        self.assertEqual(result[0], "\t")
        self.assertIn("=INJECTION", result)

    def test_all_six_triggers_are_in_frozenset(self):
        """All six documented triggers are present in _FORMULA_TRIGGERS."""
        from apps.reports.exports.csv_export import _FORMULA_TRIGGERS
        for trigger in ("=", "+", "-", "@", "\t", "\r"):
            self.assertIn(trigger, _FORMULA_TRIGGERS)


# ---------------------------------------------------------------------------
# streaming_csv_response tests
# ---------------------------------------------------------------------------

def _consume_streaming_response(response) -> bytes:
    """Collect all chunks from a StreamingHttpResponse into bytes."""
    return b"".join(
        chunk.encode() if isinstance(chunk, str) else chunk
        for chunk in response.streaming_content
    )


class StreamingCsvResponseColumnWhitelistTest(TestCase):
    """Extra keys in row dicts are silently dropped (PIPEDA column whitelist)."""

    def test_extra_keys_dropped(self):
        rows = [
            {
                "amount": "100.00",
                "payer_email": "secret@example.com",  # must be dropped
                "donor_name": "Jane Doe",             # must be dropped
                "reference": "REF-001",
            }
        ]
        columns = ["amount", "reference"]  # whitelist excludes PII

        response = streaming_csv_response(
            rows, columns, "test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")  # strip BOM

        self.assertNotIn("secret@example.com", content)
        self.assertNotIn("Jane Doe", content)
        self.assertIn("100.00", content)
        self.assertIn("REF-001", content)

    def test_column_order_preserved(self):
        rows = [{"z": "last", "a": "first"}]
        columns = ["a", "z"]
        response = streaming_csv_response(
            rows, columns, "order_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")
        # Header row should be 'a,z'
        first_line = content.splitlines()[0]
        self.assertEqual(first_line, "a,z")


class StreamingCsvResponseBomTest(TestCase):
    """Response starts with UTF-8 BOM (required for Excel compatibility)."""

    def test_bom_present(self):
        rows = [{"col": "value"}]
        response = streaming_csv_response(
            rows, ["col"], "bom_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        raw = _consume_streaming_response(response)
        # UTF-8 BOM is EF BB BF
        self.assertTrue(
            raw.startswith(b"\xef\xbb\xbf"),
            "UTF-8 BOM missing — Excel will not decode non-ASCII correctly",
        )


class StreamingCsvResponseHeadersTest(TestCase):
    """Content-Type and Content-Disposition are set correctly."""

    def test_content_type(self):
        response = streaming_csv_response(
            [], ["col"], "hdr_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")

    def test_content_disposition_filename(self):
        response = streaming_csv_response(
            [], ["col"], "reconciliation", date(2025, 6, 1), date(2025, 6, 30)
        )
        cd = response["Content-Disposition"]
        self.assertIn("attachment", cd)
        self.assertIn("reconciliation", cd)
        self.assertIn("2025-06-01", cd)
        self.assertIn("2025-06-30", cd)
        self.assertIn(".csv", cd)

    def test_content_disposition_no_pii_in_filename(self):
        """Filename contains only prefix + dates — no donor/payer data."""
        response = streaming_csv_response(
            [], ["col"], "receipts", date(2025, 1, 1), date(2025, 12, 31)
        )
        # Extract only the filename value, not the full header (which contains
        # "filename=" and would falsely match "name" as a PII substring).
        cd = response["Content-Disposition"]
        # Pull out just the filename parameter value (the part after filename=")
        m = re.search(r'filename="([^"]+)"', cd)
        self.assertIsNotNone(m, "No filename in Content-Disposition")
        filename_value = m.group(1).lower()
        for pii_token in ("@", "email", "social insurance", "donor", "payer"):
            self.assertNotIn(pii_token, filename_value)


class StreamingCsvResponseRowStreamingTest(TestCase):
    """Rows are streamed correctly — dict rows and sequence rows both work."""

    def test_dict_rows_streamed(self):
        rows = [
            {"ref": "R001", "amount": "50.00"},
            {"ref": "R002", "amount": "75.00"},
        ]
        response = streaming_csv_response(
            rows, ["ref", "amount"], "dict_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")
        self.assertIn("R001", content)
        self.assertIn("R002", content)
        self.assertIn("50.00", content)
        self.assertIn("75.00", content)

    def test_sequence_rows_streamed(self):
        """Non-dict rows (lists/tuples) work: each cell sanitized in order."""
        rows = [["REF-001", "100.00"], ["REF-002", "200.00"]]
        response = streaming_csv_response(
            rows, ["reference", "amount"], "seq_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")
        self.assertIn("REF-001", content)
        self.assertIn("200.00", content)

    def test_empty_rows_produces_header_only(self):
        response = streaming_csv_response(
            [], ["col_a", "col_b"], "empty_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")
        lines = [l for l in content.splitlines() if l]
        self.assertEqual(len(lines), 1)  # header only
        self.assertEqual(lines[0], "col_a,col_b")

    def test_missing_dict_key_renders_empty_string(self):
        """Row dict missing a column key → empty cell, not KeyError."""
        rows = [{"col_a": "val_a"}]  # col_b missing
        response = streaming_csv_response(
            rows, ["col_a", "col_b"], "missing_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")
        self.assertIn("val_a", content)
        # Should not raise; col_b will render as empty string

    def test_formula_injection_in_row_values_neutralized(self):
        """Formula triggers in row values are sanitized before streaming."""
        rows = [{"formula": "=DANGEROUS()"}]
        response = streaming_csv_response(
            rows, ["formula"], "injection_test", date(2025, 1, 1), date(2025, 1, 31)
        )
        content = _consume_streaming_response(response).decode("utf-8-sig")
        # Positive assertion: the tab-prefixed form must be present
        self.assertIn("\t=DANGEROUS()", content)
        # Negative assertion: no line must start with an unescaped '=' trigger
        # (catches both first-column and comma-preceded cases)
        for line in content.splitlines():
            stripped = line.lstrip(",")  # strip any leading delimiters
            self.assertFalse(
                stripped.startswith("=DANGEROUS"),
                f"Unsanitized formula found at start of field in line: {line!r}",
            )


# ---------------------------------------------------------------------------
# Task tests — recompute_snapshot (financial and donations)
# ---------------------------------------------------------------------------

class RecomputeSnapshotFinancialTaskTest(TestCase):
    """recompute_snapshot dispatches correctly for REPORT_TYPE_FINANCIAL."""

    def test_financial_snapshot_written(self):
        from apps.reports.tasks import recompute_snapshot
        result = recompute_snapshot.apply(
            args=[ReportSnapshot.REPORT_TYPE_FINANCIAL, 2025, 6]
        )
        self.assertTrue(result.successful())
        ret = result.get()
        self.assertEqual(ret["report_type"], ReportSnapshot.REPORT_TYPE_FINANCIAL)
        self.assertEqual(ret["year"], 2025)
        self.assertEqual(ret["month"], 6)
        self.assertTrue(ret["success"])
        self.assertIn("row_count", ret)

        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=6,
        )
        self.assertIsNotNone(snap)

    def test_financial_snapshot_idempotent(self):
        """Running recompute_snapshot twice for the same period must not duplicate rows."""
        from apps.reports.tasks import recompute_snapshot
        recompute_snapshot.apply(
            args=[ReportSnapshot.REPORT_TYPE_FINANCIAL, 2025, 7]
        )
        recompute_snapshot.apply(
            args=[ReportSnapshot.REPORT_TYPE_FINANCIAL, 2025, 7]
        )
        self.assertEqual(
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
                period_year=2025,
                period_month=7,
            ).count(),
            1,
        )


class RecomputeSnapshotDonationsTaskTest(TestCase):
    """recompute_snapshot dispatches correctly for REPORT_TYPE_DONATIONS."""

    def test_donations_snapshot_written(self):
        from apps.reports.tasks import recompute_snapshot
        result = recompute_snapshot.apply(
            args=[ReportSnapshot.REPORT_TYPE_DONATIONS, 2025, 4]
        )
        self.assertTrue(result.successful())
        ret = result.get()
        self.assertEqual(ret["report_type"], ReportSnapshot.REPORT_TYPE_DONATIONS)
        self.assertEqual(ret["year"], 2025)
        self.assertEqual(ret["month"], 4)
        self.assertTrue(ret["success"])

        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
            period_year=2025,
            period_month=4,
        )
        self.assertIsNotNone(snap)

    def test_donations_snapshot_idempotent(self):
        from apps.reports.tasks import recompute_snapshot
        recompute_snapshot.apply(
            args=[ReportSnapshot.REPORT_TYPE_DONATIONS, 2025, 5]
        )
        recompute_snapshot.apply(
            args=[ReportSnapshot.REPORT_TYPE_DONATIONS, 2025, 5]
        )
        self.assertEqual(
            ReportSnapshot.objects.filter(
                report_type=ReportSnapshot.REPORT_TYPE_DONATIONS,
                period_year=2025,
                period_month=5,
            ).count(),
            1,
        )


# ---------------------------------------------------------------------------
# _compute_single_snapshot dispatch and ValueError tests
# ---------------------------------------------------------------------------

class ComputeSingleSnapshotTest(TestCase):
    """_compute_single_snapshot dispatches to the correct service function."""

    def test_financial_dispatches(self):
        data, row_count = _compute_single_snapshot(
            ReportSnapshot.REPORT_TYPE_FINANCIAL, 2025, 1
        )
        self.assertIsInstance(data, dict)
        self.assertIsInstance(row_count, int)

    def test_donations_dispatches(self):
        data, row_count = _compute_single_snapshot(
            ReportSnapshot.REPORT_TYPE_DONATIONS, 2025, 1
        )
        self.assertIsInstance(data, dict)
        self.assertIsInstance(row_count, int)

    def test_operational_dispatches(self):
        data, row_count = _compute_single_snapshot(
            ReportSnapshot.REPORT_TYPE_OPERATIONAL, 2025, 1
        )
        self.assertIsInstance(data, dict)
        self.assertIsInstance(row_count, int)

    def test_unknown_type_raises_value_error(self):
        """_compute_single_snapshot must raise ValueError for unknown report_type."""
        with self.assertRaises(ValueError) as ctx:
            _compute_single_snapshot("not_a_real_type", 2025, 1)
        self.assertIn("not_a_real_type", str(ctx.exception))

    def test_row_count_non_negative(self):
        """row_count returned by dispatch is always >= 0."""
        for rtype in (
            ReportSnapshot.REPORT_TYPE_FINANCIAL,
            ReportSnapshot.REPORT_TYPE_DONATIONS,
            ReportSnapshot.REPORT_TYPE_OPERATIONAL,
        ):
            _, row_count = _compute_single_snapshot(rtype, 2025, 2)
            self.assertGreaterEqual(row_count, 0, f"Negative row_count for {rtype}")


# ---------------------------------------------------------------------------
# _compute_all_snapshots integration tests
# ---------------------------------------------------------------------------

class ComputeAllSnapshotsIntegrationTest(TestCase):
    """_compute_all_snapshots writes all three snapshot types."""

    def test_writes_all_three_types(self):
        written = _compute_all_snapshots(2025, 3)
        self.assertEqual(written, 3, "Expected 3 snapshots written (financial, donations, operational)")

        for report_type in (
            ReportSnapshot.REPORT_TYPE_FINANCIAL,
            ReportSnapshot.REPORT_TYPE_DONATIONS,
            ReportSnapshot.REPORT_TYPE_OPERATIONAL,
        ):
            self.assertTrue(
                ReportSnapshot.objects.filter(
                    report_type=report_type,
                    period_year=2025,
                    period_month=3,
                ).exists(),
                f"Missing snapshot for {report_type}",
            )

    def test_idempotent_no_duplicates(self):
        """Running _compute_all_snapshots twice must not create duplicate rows."""
        _compute_all_snapshots(2025, 8)
        _compute_all_snapshots(2025, 8)
        total = ReportSnapshot.objects.filter(period_year=2025, period_month=8).count()
        self.assertEqual(total, 3)  # exactly 3 types, no duplicates

    def test_returns_count_of_written_snapshots(self):
        written = _compute_all_snapshots(2024, 12)
        self.assertIsInstance(written, int)
        self.assertGreaterEqual(written, 0)
        self.assertLessEqual(written, 3)

    def test_updates_existing_snapshots(self):
        """Second call updates the data field, not creates a new row."""
        # Pre-create a financial snapshot with stale data
        ReportSnapshot.objects.create(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=9,
            data={"stale": True},
            row_count=0,
        )
        _compute_all_snapshots(2025, 9)
        snap = ReportSnapshot.objects.get(
            report_type=ReportSnapshot.REPORT_TYPE_FINANCIAL,
            period_year=2025,
            period_month=9,
        )
        # Data should have been overwritten with fresh aggregate, not the stale dict
        self.assertNotIn("stale", snap.data)


# ---------------------------------------------------------------------------
# MonthlySummaryPdfView — ExportRecord created AFTER PDF (regression)
# ---------------------------------------------------------------------------

class MonthlySummaryPdfExportRecordOrderTest(TestCase):
    """
    Regression: ExportRecord must be created AFTER PDF generation succeeds.

    If WeasyPrint raises (missing C libs, template error, OOM), the audit
    trail must NOT record a completed export — the file never reached the
    client.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email=f"pdftest_{uuid.uuid4().hex[:6]}@example.com",
            password="testpass123",
            is_staff=True,
        )
        perm = Permission.objects.get(
            codename="export_financialreport",
            content_type__app_label="payments",
        )
        self.user.user_permissions.add(perm)

    def test_no_export_record_created_when_pdf_raises(self):
        """
        When export_monthly_summary_pdf() raises, MonthlySummaryPdfView must
        not create an ExportRecord.  Uses patch to simulate WeasyPrint failure.
        """
        initial_count = ExportRecord.objects.count()

        self.client.force_login(self.user)

        with patch(
            "apps.reports.exports.pdf_export.export_monthly_summary_pdf",
            side_effect=RuntimeError("WeasyPrint unavailable in test environment"),
        ):
            try:
                self.client.get(
                    reverse("reports:monthly-summary-pdf", kwargs={"year": 2025, "month": 6})
                )
            except RuntimeError:
                pass  # View may propagate or return 500 depending on DEBUG setting

        # Regardless of how the exception propagated, no ExportRecord was written
        self.assertEqual(
            ExportRecord.objects.count(),
            initial_count,
            "ExportRecord must not be created when PDF generation fails",
        )

    def test_export_record_created_when_pdf_succeeds(self):
        """
        When export_monthly_summary_pdf() succeeds, ExportRecord is created.
        """
        initial_count = ExportRecord.objects.count()
        self.client.force_login(self.user)

        fake_pdf_response = HttpResponse(b"%PDF-fake", content_type="application/pdf")

        with patch(
            "apps.reports.exports.pdf_export.export_monthly_summary_pdf",
            return_value=fake_pdf_response,
        ):
            response = self.client.get(
                reverse("reports:monthly-summary-pdf", kwargs={"year": 2025, "month": 6})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ExportRecord.objects.count(),
            initial_count + 1,
            "ExportRecord must be created exactly once after successful PDF generation",
        )

        rec = ExportRecord.objects.order_by("-created_at").first()
        self.assertEqual(rec.format, ExportRecord.FORMAT_PDF)
        self.assertEqual(rec.export_type, ExportRecord.EXPORT_TYPE_REVENUE)
        self.assertEqual(rec.period_start, date(2025, 6, 1))
        self.assertEqual(rec.period_end, date(2025, 6, 30))
        self.assertEqual(rec.actor_pk, self.user.pk)
        # actor_pk is integer, not email
        self.assertIsInstance(rec.actor_pk, int)
