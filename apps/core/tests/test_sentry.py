"""
Tests for M-G and M-H Sentry PII filtering fixes.

M-G: IPv6 addresses must be masked before events leave the process (PIPEDA).
M-H: _scrub_dict must recurse into list values so PII inside list[dict]
     structures is not silently bypassed.
"""

from django.test import SimpleTestCase

from apps.core.sentry import _mask_ip, _scrub_dict, before_send


# ---------------------------------------------------------------------------
# M-G — IPv6 address masking
# ---------------------------------------------------------------------------

class SentryIPv6MaskingTest(SimpleTestCase):
    """M-G: IPv6 addresses must be masked in Sentry events."""

    def test_ipv4_masked(self):
        self.assertEqual(_mask_ip("ip: 192.168.1.1"), "ip: [masked]")

    def test_ipv6_full_masked(self):
        result = _mask_ip("2001:db8:85a3::8a2e:370:7334")
        self.assertNotIn("2001:db8:85a3", result)
        self.assertIn("[masked]", result)

    def test_ipv6_loopback_masked(self):
        result = _mask_ip("request from ::1")
        self.assertNotIn("::1", result)
        self.assertIn("[masked]", result)

    def test_ipv6_ipv4_mapped_masked(self):
        # ::ffff:192.0.2.1 — the IPv6 prefix is masked; the embedded IPv4 is
        # then caught by the IPv4 pattern if it survives.
        result = _mask_ip("::ffff:192.0.2.1")
        self.assertNotIn("192.0.2.1", result)

    def test_ipv6_link_local_masked(self):
        result = _mask_ip("fe80::1")
        self.assertNotIn("fe80::1", result)
        self.assertIn("[masked]", result)

    def test_ipv4_still_masked(self):
        """Regression: IPv4 masking must continue to work after M-G changes."""
        result = _mask_ip("blocked: 10.0.0.255")
        self.assertNotIn("10.0.0.255", result)
        self.assertIn("[masked]", result)

    def test_multiple_addresses_in_one_string(self):
        result = _mask_ip("src=192.168.0.1 dst=2001:db8::1")
        self.assertNotIn("192.168.0.1", result)
        self.assertNotIn("2001:db8::1", result)

    def test_plain_text_not_mangled(self):
        result = _mask_ip("payment completed successfully")
        self.assertEqual(result, "payment completed successfully")

    def test_before_send_masks_ipv6_remote_addr(self):
        """Full pipeline: before_send must scrub IPv6 from REMOTE_ADDR in event."""
        event = {
            "request": {"env": {"REMOTE_ADDR": "2001:db8::1"}, "headers": {}},
            "extra": {},
        }
        result = before_send(event, {})
        remote = result.get("request", {}).get("env", {}).get("REMOTE_ADDR", "")
        self.assertNotIn("2001:db8::1", remote)

    def test_before_send_masks_ipv4_remote_addr(self):
        """Regression: before_send must still mask IPv4 in REMOTE_ADDR."""
        event = {
            "request": {"env": {"REMOTE_ADDR": "203.0.113.5"}, "headers": {}},
            "extra": {},
        }
        result = before_send(event, {})
        remote = result.get("request", {}).get("env", {}).get("REMOTE_ADDR", "")
        self.assertNotIn("203.0.113.5", remote)

    def test_before_send_masks_http_x_forwarded_for(self):
        """HTTP_X_FORWARDED_FOR may carry the real client IP behind a proxy."""
        event = {
            "request": {
                "env": {"HTTP_X_FORWARDED_FOR": "2001:db8:cafe::1"},
                "headers": {},
            },
            "extra": {},
        }
        result = before_send(event, {})
        fwd = result["request"]["env"]["HTTP_X_FORWARDED_FOR"]
        self.assertNotIn("2001:db8:cafe::1", fwd)

    def test_before_send_no_env_does_not_raise(self):
        """before_send must handle a request dict with no env key gracefully."""
        event = {"request": {"headers": {}}, "extra": {}}
        result = before_send(event, {})
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# M-H — _scrub_dict recurses into lists
# ---------------------------------------------------------------------------

class SentryListScrubTest(SimpleTestCase):
    """M-H: _scrub_dict must recurse into lists of dicts."""

    def test_pii_in_list_of_dicts_scrubbed(self):
        keys = frozenset({"email", "name"})
        data = {
            "users": [
                {"email": "alice@example.com", "role": "admin"},
                {"email": "bob@example.com", "role": "staff"},
            ]
        }
        result = _scrub_dict(data, keys)
        for user in result["users"]:
            self.assertEqual(user["email"], "[Filtered]")
            self.assertIn(user["role"], ["admin", "staff"])  # non-PII preserved

    def test_nested_list_in_list_scrubbed(self):
        keys = frozenset({"token"})
        data = {"auth": [[{"token": "secret"}]]}
        result = _scrub_dict(data, keys)
        self.assertEqual(result["auth"][0][0]["token"], "[Filtered]")

    def test_non_pii_list_values_preserved(self):
        keys = frozenset({"secret"})
        data = {"tags": ["payments", "stripe", "webhooks"]}
        result = _scrub_dict(data, keys)
        self.assertEqual(result["tags"], ["payments", "stripe", "webhooks"])

    def test_list_of_scalars_preserved(self):
        keys = frozenset({"password"})
        data = {"ids": [1, 2, 3], "password": "hunter2"}
        result = _scrub_dict(data, keys)
        self.assertEqual(result["ids"], [1, 2, 3])
        self.assertEqual(result["password"], "[Filtered]")

    def test_empty_list_value_preserved(self):
        keys = frozenset({"email"})
        data = {"attachments": []}
        result = _scrub_dict(data, keys)
        self.assertEqual(result["attachments"], [])

    def test_before_send_scrubs_pii_in_list_of_dicts_in_extra(self):
        """Full pipeline: list-of-dicts PII in event['extra'] must be scrubbed."""
        event = {
            "request": {"headers": {}},
            "extra": {
                "donors": [
                    {"name": "Alice Smith", "amount": "100.00"},
                    {"name": "Bob Jones", "amount": "50.00"},
                ]
            },
        }
        result = before_send(event, {})
        for donor in result["extra"]["donors"]:
            self.assertEqual(donor["name"], "[Filtered]")
            self.assertEqual(donor["amount"], "[Filtered]")
