"""
Tests for AuditLogEntry hash-chain integrity.

GovStack Consent BB v1.3.0, Section 6.3 (REQUIRED):
    "All consent logs shall be tamperproof, Audit logging"

These tests verify:
1. Each entry's entry_hash is computed deterministically from its fields.
2. Each entry's prev_hash links to the previous entry's entry_hash.
3. The first entry in the chain has prev_hash == "".
4. verify_chain() returns ok=True on an untampered chain.
5. verify_chain() detects a corrupted entry_hash.
6. verify_chain() detects a broken prev_hash linkage.
7. verify_chain() continues past a single bad entry (no false-cascade).
8. Concurrent writes do not produce duplicate prev_hash values (serialisation).
"""

import threading

from django.conf import settings
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from apps.audit.models import AuditLogEntry, AuditEventType, ChainVerificationResult
from apps.audit.services import record_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(**kwargs) -> AuditLogEntry:
    """Create an audit entry via the service layer (which sets hashes)."""
    defaults = {
        "event_type": AuditEventType.RECORD_VIEWED,
        "actor_id": "test-user-1",
        "resource_type": "consent.ConsentAgreement",
        "resource_id": "42",
    }
    defaults.update(kwargs)
    entry = record_event(**defaults)
    assert entry is not None, "record_event returned None — check service error log"
    return entry


# ---------------------------------------------------------------------------
# Hash correctness
# ---------------------------------------------------------------------------

class AuditEntryHashTests(TestCase):
    """Unit tests for entry_hash computation."""

    def test_entry_hash_is_set_on_save(self):
        entry = _make_entry()
        self.assertNotEqual(entry.entry_hash, "")
        self.assertEqual(len(entry.entry_hash), 64)  # SHA-256 hex

    def test_entry_hash_is_deterministic(self):
        """Recomputing the hash on the saved entry must produce the same value."""
        entry = _make_entry()
        self.assertEqual(entry.entry_hash, entry._compute_hash())

    def test_first_entry_has_empty_prev_hash(self):
        """The chain genesis entry must have prev_hash == ""."""
        AuditLogEntry.objects.all().delete()  # ensure clean slate
        entry = _make_entry()
        self.assertEqual(entry.prev_hash, "")

    def test_second_entry_prev_hash_equals_first_entry_hash(self):
        AuditLogEntry.objects.all().delete()
        first = _make_entry(event_type=AuditEventType.LOGIN_SUCCESS)
        second = _make_entry(event_type=AuditEventType.RECORD_VIEWED)
        self.assertEqual(second.prev_hash, first.entry_hash)

    def test_chain_links_across_multiple_entries(self):
        AuditLogEntry.objects.all().delete()
        entries = [_make_entry() for _ in range(5)]
        for i in range(1, 5):
            self.assertEqual(
                entries[i].prev_hash,
                entries[i - 1].entry_hash,
                msg=f"Entry {i} prev_hash does not link to entry {i-1}",
            )

    def test_entries_are_immutable(self):
        """Attempting to save an existing entry must raise ValueError."""
        entry = _make_entry()
        entry.actor_email = "tampered@example.com"
        with self.assertRaises(ValueError):
            entry.save()

    def test_entries_cannot_be_deleted(self):
        entry = _make_entry()
        with self.assertRaises(ValueError):
            entry.delete()


# ---------------------------------------------------------------------------
# verify_chain — clean chain
# ---------------------------------------------------------------------------

class VerifyChainCleanTests(TestCase):
    """verify_chain() must return ok=True on an untampered chain."""

    def test_empty_chain_is_ok(self):
        AuditLogEntry.objects.all().delete()
        result = AuditLogEntry.verify_chain()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 0)
        self.assertEqual(result.violations, [])

    def test_single_entry_chain_is_ok(self):
        AuditLogEntry.objects.all().delete()
        _make_entry()
        result = AuditLogEntry.verify_chain()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 1)

    def test_multi_entry_chain_is_ok(self):
        AuditLogEntry.objects.all().delete()
        for _ in range(10):
            _make_entry()
        result = AuditLogEntry.verify_chain()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 10)

    def test_start_id_scopes_check(self):
        AuditLogEntry.objects.all().delete()
        entries = [_make_entry() for _ in range(5)]
        start = entries[2].id
        result = AuditLogEntry.verify_chain(start_id=start)
        # Entries at index 2, 3, 4 — but entry[2] has prev_hash pointing to
        # entry[1] which is outside the window, so prev_hash check expects ""
        # for the first entry in window. Scoped verify is used for incremental
        # checks where the caller already knows the prior chain is clean.
        self.assertEqual(result.entries_checked, 3)

    def test_batch_size_one_still_passes(self):
        """Streaming in batches of 1 should not break the result."""
        AuditLogEntry.objects.all().delete()
        for _ in range(6):
            _make_entry()
        result = AuditLogEntry.verify_chain(batch_size=1)
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 6)


# ---------------------------------------------------------------------------
# verify_chain — tampered chain
# ---------------------------------------------------------------------------

class VerifyChainTamperedTests(TestCase):
    """verify_chain() must detect tampering."""

    def _build_chain(self, n: int = 5):
        AuditLogEntry.objects.all().delete()
        return [_make_entry() for _ in range(n)]

    def _corrupt_entry_hash(self, entry: AuditLogEntry) -> None:
        """Directly write a bad hash to the DB, bypassing the immutability guard."""
        AuditLogEntry.objects.filter(pk=entry.pk).update(entry_hash="deadbeef" * 8)

    def _corrupt_prev_hash(self, entry: AuditLogEntry) -> None:
        AuditLogEntry.objects.filter(pk=entry.pk).update(prev_hash="badhash" * 9)

    def test_corrupted_entry_hash_is_detected(self):
        entries = self._build_chain(5)
        self._corrupt_entry_hash(entries[2])
        result = AuditLogEntry.verify_chain()
        self.assertFalse(result.ok)
        violated_ids = {v["entry_id"] for v in result.violations}
        self.assertIn(entries[2].id, violated_ids)

    def test_corrupted_prev_hash_is_detected(self):
        entries = self._build_chain(5)
        self._corrupt_prev_hash(entries[3])
        result = AuditLogEntry.verify_chain()
        self.assertFalse(result.ok)
        violated_ids = {v["entry_id"] for v in result.violations}
        self.assertIn(entries[3].id, violated_ids)

    def test_single_corruption_cascades_to_next_entry(self):
        """
        Corrupting entry[1]'s stored entry_hash causes two violations:

        1. entry[1] hash mismatch  (stored != recomputed)
        2. entry[2] prev_hash mismatch (entry[2].prev_hash == original hash of
           entry[1], but verify_chain uses the *stored* corrupted hash of
           entry[1] as the expected prev_hash for entry[2])

        This cascade is correct and expected — it pinpoints exactly which
        entries were affected by or written after the corruption.  The
        violations stop at entry[2]: entries[3..4] are not flagged because
        entry[2]'s stored entry_hash is still valid and verify_chain advances
        expected_prev_hash to entry[2]'s stored hash.
        """
        entries = self._build_chain(5)
        self._corrupt_entry_hash(entries[1])
        result = AuditLogEntry.verify_chain()
        self.assertFalse(result.ok)

        violated_ids = [v["entry_id"] for v in result.violations]
        # Both entry[1] (hash mismatch) and entry[2] (prev_hash mismatch) flagged
        self.assertIn(entries[1].id, violated_ids)
        self.assertIn(entries[2].id, violated_ids)

        # Entries 3 and 4 must NOT be flagged — the cascade stops at entry[2]
        # because verify_chain uses entry[2]'s stored (intact) hash going forward.
        for idx in (3, 4):
            self.assertNotIn(entries[idx].id, violated_ids,
                             f"entry[{idx}] should not be flagged")

    def test_first_entry_tampered_is_detected(self):
        entries = self._build_chain(3)
        self._corrupt_entry_hash(entries[0])
        result = AuditLogEntry.verify_chain()
        self.assertFalse(result.ok)
        violated_ids = {v["entry_id"] for v in result.violations}
        self.assertIn(entries[0].id, violated_ids)

    def test_last_entry_tampered_is_detected(self):
        entries = self._build_chain(3)
        self._corrupt_entry_hash(entries[-1])
        result = AuditLogEntry.verify_chain()
        self.assertFalse(result.ok)
        violated_ids = {v["entry_id"] for v in result.violations}
        self.assertIn(entries[-1].id, violated_ids)

    def test_result_reports_entries_checked_even_on_violation(self):
        entries = self._build_chain(4)
        self._corrupt_entry_hash(entries[1])
        result = AuditLogEntry.verify_chain()
        self.assertEqual(result.entries_checked, 4)


# ---------------------------------------------------------------------------
# Concurrent writes (serialisation)
# ---------------------------------------------------------------------------

@skipUnlessDBFeature("has_select_for_update")
class AuditChainConcurrencyTests(TransactionTestCase):
    """
    Verify that concurrent writers produce a valid (non-forked) chain.

    Each thread writes N entries; after all threads finish, verify_chain()
    must return ok=True with no duplicate prev_hash values across the table.

    Uses TransactionTestCase because threading requires real DB commits
    (TestCase wraps everything in one transaction that threads cannot see).

    Skipped on SQLite: SQLite's file-level locking does not support true
    row-level SELECT FOR UPDATE and raises OperationalError under concurrent
    writers. Run against PostgreSQL (make test-docker) to exercise this path.
    """

    def test_concurrent_writers_produce_unique_prev_hashes(self):
        THREADS = 4
        ENTRIES_PER_THREAD = 8

        errors = []

        def worker():
            try:
                for _ in range(ENTRIES_PER_THREAD):
                    result = record_event(
                        event_type=AuditEventType.RECORD_VIEWED,
                        actor_id="concurrent-test",
                        resource_type="audit.test",
                        resource_id="0",
                    )
                    if result is None:
                        errors.append("record_event returned None")
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Worker errors: {errors}")

        total = AuditLogEntry.objects.count()
        self.assertEqual(total, THREADS * ENTRIES_PER_THREAD)

        # Every prev_hash must be unique (no two entries share the same parent).
        # The only exception is the genesis entry which has prev_hash == "".
        prev_hashes = list(
            AuditLogEntry.objects.exclude(prev_hash="")
            .values_list("prev_hash", flat=True)
        )
        self.assertEqual(
            len(prev_hashes),
            len(set(prev_hashes)),
            "Duplicate prev_hash values found — concurrent writes produced a forked chain",
        )

        # Full chain must also be intact end-to-end.
        result = AuditLogEntry.verify_chain()
        self.assertTrue(
            result.ok,
            f"Chain violations after concurrent writes: {result.violations}",
        )
