"""
M-F — test_receipt_email.py

Security regression tests for apps/payments/services/receipt_email.py.

Covers:
  - _clean_header() utility removes all newline variants (CR, LF, CRLF)
  - send_receipt_email() sanitises charity_legal_name before embedding it in
    the email Subject header, preventing MIME header injection attacks.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.payments.services.receipt_email import _clean_header, send_receipt_email

# ---------------------------------------------------------------------------
# _clean_header unit tests
# ---------------------------------------------------------------------------


class CleanHeaderTests(TestCase):
    """M-F: _clean_header() must neutralise all newline variants."""

    def test_clean_header_removes_crlf(self):
        self.assertEqual(_clean_header("foo\r\nbar"), "foo bar")

    def test_clean_header_removes_lf(self):
        self.assertEqual(_clean_header("foo\nbar"), "foo bar")

    def test_clean_header_removes_cr(self):
        self.assertEqual(_clean_header("foo\rbar"), "foo bar")

    def test_clean_header_removes_multiple_newlines(self):
        result = _clean_header("a\r\nb\nc\rd")
        self.assertNotIn("\r\n", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("\r", result)

    def test_clean_header_strips_leading_trailing_whitespace(self):
        self.assertEqual(_clean_header("  Acme Charity  "), "Acme Charity")

    def test_clean_header_passthrough_clean_value(self):
        self.assertEqual(_clean_header("Test Charity Inc."), "Test Charity Inc.")

    def test_clean_header_empty_string(self):
        self.assertEqual(_clean_header(""), "")

    def test_clean_header_attacker_payload(self):
        """Full injection payload must not produce any header-breaking bytes."""
        malicious = "Acme Charity\r\nBcc: attacker@evil.com\r\n"
        result = _clean_header(malicious)
        self.assertNotIn("\r\n", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("\r", result)
        # The legit part of the name is still in the result
        self.assertIn("Acme Charity", result)


# ---------------------------------------------------------------------------
# send_receipt_email — header injection regression tests
# ---------------------------------------------------------------------------


def _make_mock_receipt(charity_legal_name="Test Charity Inc.", serial_number=None):
    """Build a minimal mock receipt object for send_receipt_email()."""
    serial_number = serial_number or f"2024-{str(uuid.uuid4().int % 1000000).zfill(6)}"

    donor = MagicMock()
    donor.email = "donor@example.ca"

    donation = MagicMock()
    donation.donor = donor

    receipt = MagicMock()
    receipt.serial_number = serial_number
    receipt.charity_legal_name = charity_legal_name
    receipt.eligible_amount = Decimal("100.00")
    receipt.advantage_amount = Decimal("0.00")
    receipt.donation_date = date(2024, 6, 1)
    receipt.receipt_date = date(2024, 6, 15)
    receipt.is_annual_consolidated = False
    receipt.donation = donation
    return receipt


class SendReceiptEmailHeaderInjectionTests(TestCase):
    """M-F: send_receipt_email() must sanitise charity_legal_name in Subject."""

    def _call_send_receipt_email(self, charity_legal_name, serial="2024-000001"):
        """
        Call send_receipt_email() with a mock receipt and patched email backend.
        Returns the EmailMessage instance that would have been sent.
        """
        receipt = _make_mock_receipt(
            charity_legal_name=charity_legal_name,
            serial_number=serial,
        )
        pdf_bytes = b"%PDF-1.4 fake"

        def capture_send(fail_silently=False):
            # 'self' inside EmailMessage.send() refers to the email instance,
            # but we patch at the EmailMessage level via the captured reference.
            pass

        # Patch EmailMessage.send so we can inspect the constructed subject
        # without a real SMTP connection.
        with patch(
            "apps.payments.services.receipt_email.render_to_string",
            return_value="<html>receipt body</html>",
        ):
            with patch("apps.payments.services.receipt_email.EmailMessage") as MockEmailMessage:  # noqa: N806
                mock_email_instance = MagicMock()
                mock_email_instance.send.return_value = 1
                MockEmailMessage.return_value = mock_email_instance

                result = send_receipt_email(receipt, pdf_bytes)

        # Extract the subject passed to EmailMessage(subject=...)
        self.assertTrue(MockEmailMessage.called, "EmailMessage was never instantiated")
        init_kwargs = MockEmailMessage.call_args[1]  # keyword args
        subject = init_kwargs.get(
            "subject", MockEmailMessage.call_args[0][0] if MockEmailMessage.call_args[0] else ""
        )
        return subject, result

    def test_clean_charity_name_appears_verbatim_in_subject(self):
        """A clean charity name passes through unchanged."""
        subject, ok = self._call_send_receipt_email("Test Charity Inc.")
        self.assertTrue(ok)
        self.assertIn("Test Charity Inc.", subject)

    def test_crlf_injection_payload_stripped_from_subject(self):
        """M-F: CRLF in charity_legal_name must not appear in the email subject."""
        malicious = "Acme Charity\r\nBcc: attacker@evil.com"
        subject, ok = self._call_send_receipt_email(malicious)
        self.assertTrue(ok)
        self.assertNotIn("\r\n", subject)
        self.assertNotIn("\n", subject)
        self.assertNotIn("\r", subject)

    def test_lf_only_injection_stripped_from_subject(self):
        """M-F: bare LF in charity_legal_name must not appear in the email subject."""
        malicious = "Acme Charity\nBcc: attacker@evil.com"
        subject, ok = self._call_send_receipt_email(malicious)
        self.assertNotIn("\n", subject)
        self.assertNotIn("\r", subject)

    def test_cr_only_injection_stripped_from_subject(self):
        """M-F: bare CR in charity_legal_name must not appear in the email subject."""
        malicious = "Acme Charity\rBcc: attacker@evil.com"
        subject, ok = self._call_send_receipt_email(malicious)
        self.assertNotIn("\r", subject)
        self.assertNotIn("\n", subject)

    def test_subject_still_contains_serial_number(self):
        """Subject must still identify the receipt even after sanitisation."""
        serial = "2024-007777"
        subject, _ = self._call_send_receipt_email("Good Charity", serial=serial)
        self.assertIn(serial, subject)

    def test_no_donor_email_returns_false_without_sending(self):
        """send_receipt_email returns False when donor has no email (not a bug)."""
        receipt = _make_mock_receipt()
        receipt.donation.donor.email = ""  # no email on file

        with patch(
            "apps.payments.services.receipt_email.render_to_string",
            return_value="<html></html>",
        ):
            with patch("apps.payments.services.receipt_email.EmailMessage") as MockEmail:  # noqa: N806
                result = send_receipt_email(receipt, b"%PDF fake")

        self.assertFalse(result)
        MockEmail.assert_not_called()
