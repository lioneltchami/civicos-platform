"""
apps/documents/tests/test_presign_kms_and_clamav_settings.py
============================================================
Regression tests for two adversarial-audit findings:

  SSE-KMS (MEDIUM) — ``_generate_s3_presigned_post()`` read the KMS key ID from
      ``STORAGES["default"]["OPTIONS"]["object_parameters"]`` ONLY. Nothing in
      the repo has ever populated that path — ``config/settings/production.py``
      declares ``AWS_S3_OBJECT_PARAMETERS`` at the top level, which is the other
      half of django-storages' own resolution order. The KMS branch was
      therefore unreachable in production and Protected B documents were
      uploaded with no SSE-KMS headers at all.

  FIX #5 (LOW) — ``CIVICOS["CLAMAV_TIMEOUT"]`` was read by
      ``_scan_with_clamav()`` but was never defined in ``config/settings/base.py``,
      so operators had no way to tune the clamd socket timeout.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.documents.models import DocumentCategory
from apps.documents.services.upload import (
    _generate_s3_presigned_post,
    _make_storage_key,
)

_S3_STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

_KMS_ARN = "arn:aws:kms:ca-central-1:123456789012:key/abcd-1234"


def _make_category() -> DocumentCategory:
    return DocumentCategory.objects.create(
        name_en="KMS test",
        name_fr="KMS test",
        slug=f"kms-{uuid.uuid4().hex[:6]}",
        security_classification=DocumentCategory.SecurityClassification.PROTECTED_B,
        allowed_mime_types=[],
        max_size_bytes=0,
        min_retention_days=730,
        max_retention_days=2555,
    )


class _FakeDoc:
    """Minimal stand-in — _generate_s3_presigned_post only reads pk/storage_key."""

    def __init__(self):
        self.pk = uuid.uuid4()
        self.storage_key = _make_storage_key(str(self.pk), prefix="quarantine")
        self.mime_type = "application/pdf"


class PresignedPostKmsResolutionTests(TestCase):
    """The presigned POST must carry SSE-KMS fields whenever a key is configured."""

    def setUp(self):
        self.category = _make_category()
        self.doc = _FakeDoc()

    def _call(self):
        """
        Invoke the presign helper with boto3/botocore stubbed; return
        (fields, conditions) as handed to generate_presigned_post().

        boto3 and botocore are injected into sys.modules rather than patched
        in place: _generate_s3_presigned_post() imports them lazily inside the
        function body, and importing the real packages is neither necessary
        for this assertion nor reliable in every CI image.
        """
        import sys
        import types

        fake_client = MagicMock()
        fake_client.generate_presigned_post.return_value = {
            "url": "https://bucket.s3.amazonaws.com/",
            "fields": {"key": self.doc.storage_key},
        }

        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = MagicMock(return_value=fake_client)

        fake_exceptions = types.ModuleType("botocore.exceptions")
        fake_exceptions.ClientError = type("ClientError", (Exception,), {})
        fake_exceptions.BotoCoreError = type("BotoCoreError", (Exception,), {})
        fake_botocore = types.ModuleType("botocore")
        fake_botocore.exceptions = fake_exceptions

        with patch.dict(
            sys.modules,
            {
                "boto3": fake_boto3,
                "botocore": fake_botocore,
                "botocore.exceptions": fake_exceptions,
            },
        ):
            _generate_s3_presigned_post(
                doc=self.doc,
                category=self.category,
                ttl_seconds=900,
                expires_at_str="2026-01-01T00:00:00+00:00",
                max_size=10 * 1024 * 1024,
            )

        kwargs = fake_client.generate_presigned_post.call_args.kwargs
        return kwargs["Fields"] or {}, kwargs["Conditions"]

    @override_settings(
        STORAGES=_S3_STORAGES,
        AWS_STORAGE_BUCKET_NAME="civicos-docs",
        AWS_S3_OBJECT_PARAMETERS={"CacheControl": "max-age=86400", "SSEKMSKeyId": _KMS_ARN},
    )
    def test_kms_key_is_read_from_top_level_aws_s3_object_parameters(self):
        """
        The production settings path. This is the case that was broken: the key
        lives in the top-level AWS_S3_OBJECT_PARAMETERS setting, not in
        STORAGES["default"]["OPTIONS"]["object_parameters"].
        """
        fields, conditions = self._call()

        self.assertEqual(fields.get("x-amz-server-side-encryption"), "aws:kms")
        self.assertEqual(fields.get("x-amz-server-side-encryption-aws-kms-key-id"), _KMS_ARN)
        self.assertIn({"x-amz-server-side-encryption": "aws:kms"}, conditions)
        self.assertIn({"x-amz-server-side-encryption-aws-kms-key-id": _KMS_ARN}, conditions)

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
                "OPTIONS": {"object_parameters": {"SSEKMSKeyId": _KMS_ARN}},
            },
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        AWS_STORAGE_BUCKET_NAME="civicos-docs",
    )
    def test_kms_key_is_still_read_from_storages_options(self):
        """The pre-existing OPTIONS path must keep working (no regression)."""
        fields, conditions = self._call()

        self.assertEqual(fields.get("x-amz-server-side-encryption"), "aws:kms")
        self.assertEqual(fields.get("x-amz-server-side-encryption-aws-kms-key-id"), _KMS_ARN)
        self.assertIn({"x-amz-server-side-encryption": "aws:kms"}, conditions)

    @override_settings(
        STORAGES=_S3_STORAGES,
        AWS_STORAGE_BUCKET_NAME="civicos-docs",
        AWS_S3_OBJECT_PARAMETERS={"CacheControl": "max-age=86400"},
    )
    def test_no_kms_fields_when_no_key_is_configured(self):
        """No SSE fields/conditions at all when SSEKMSKeyId is absent."""
        fields, conditions = self._call()

        self.assertNotIn("x-amz-server-side-encryption", fields)
        self.assertNotIn("x-amz-server-side-encryption-aws-kms-key-id", fields)
        for condition in conditions:
            if isinstance(condition, dict):
                self.assertNotIn("x-amz-server-side-encryption", condition)

    @override_settings(
        STORAGES=_S3_STORAGES,
        AWS_STORAGE_BUCKET_NAME="civicos-docs",
        AWS_S3_OBJECT_PARAMETERS={"SSEKMSKeyId": _KMS_ARN},
    )
    def test_kms_fields_and_conditions_stay_in_sync(self):
        """
        An S3 POST policy rejects any submitted field not covered by a signed
        condition, and any unsatisfied condition. Every SSE field must therefore
        have a matching condition entry and vice versa.
        """
        fields, conditions = self._call()

        sse_fields = {
            k: v for k, v in fields.items() if k.startswith("x-amz-server-side-encryption")
        }
        sse_conditions = [
            c
            for c in conditions
            if isinstance(c, dict) and any(k.startswith("x-amz-server-side-encryption") for k in c)
        ]
        self.assertEqual(len(sse_fields), 2)
        self.assertEqual(len(sse_conditions), 2)
        for key, value in sse_fields.items():
            self.assertIn({key: value}, sse_conditions)


class ClamavTimeoutSettingTests(TestCase):
    """CIVICOS['CLAMAV_TIMEOUT'] must actually exist and reach pyclamd."""

    def test_clamav_timeout_is_defined_in_base_settings(self):
        from config.settings import base

        self.assertIn(
            "CLAMAV_TIMEOUT",
            base.CIVICOS,
            "CLAMAV_TIMEOUT is read by _scan_with_clamav() and must be defined "
            "in config/settings/base.py's CIVICOS dict so operators can tune it.",
        )
        self.assertIsInstance(base.CIVICOS["CLAMAV_TIMEOUT"], int)
        self.assertGreater(base.CIVICOS["CLAMAV_TIMEOUT"], 0)

    def test_configured_timeout_is_passed_to_the_clamd_socket(self):
        from apps.documents.tasks import _scan_with_clamav

        fake_clamd = MagicMock()
        fake_clamd.scan_stream.return_value = None  # clean
        fake_pyclamd = MagicMock()
        fake_pyclamd.ClamdNetworkSocket.return_value = fake_clamd

        with patch.dict("sys.modules", {"pyclamd": fake_pyclamd}):
            with patch(
                "django.core.files.storage.default_storage.open",
                return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()),
            ):
                result = _scan_with_clamav(
                    storage_key="documents/quarantine/x/y.bin",
                    civicos={
                        "CLAMAV_HOST": "clamav",
                        "CLAMAV_PORT": 3310,
                        "CLAMAV_TIMEOUT": 77,
                    },
                )

        self.assertEqual(result, "OK")
        _, kwargs = fake_pyclamd.ClamdNetworkSocket.call_args
        self.assertEqual(kwargs.get("timeout"), 77)


class ClamavUploadCapCoherenceTests(TestCase):
    """
    The app's own staff upload cap must stay below the StreamMaxLength the
    compose files configure for clamd (128M) — otherwise a clean file between
    the two limits is refused by clamd and permanently quarantined as a bogus
    "scan failure". This test fails loudly if the two ever drift apart.
    """

    _COMPOSE_STREAM_MAX_BYTES = 128 * 1024 * 1024  # CLAMD_CONF_StreamMaxLength: 128M

    def test_staff_upload_cap_is_below_clamav_stream_max_length(self):
        from config.settings import base

        staff_cap = base.CIVICOS["DOCUMENT_MAX_STAFF_UPLOAD_BYTES"]
        self.assertLess(
            staff_cap,
            self._COMPOSE_STREAM_MAX_BYTES,
            "DOCUMENT_MAX_STAFF_UPLOAD_BYTES exceeds the StreamMaxLength set on "
            "the clamav service in docker-compose.yml / docker-compose.prod.yml. "
            "Raise CLAMD_CONF_StreamMaxLength in BOTH compose files.",
        )

    def test_compose_files_configure_stream_max_length(self):
        """Both compose files must actually set the directive."""
        import pathlib

        repo_root = pathlib.Path(__file__).resolve().parents[3]
        for name in ("docker-compose.yml", "docker-compose.prod.yml"):
            content = (repo_root / name).read_text(encoding="utf-8")
            self.assertIn(
                "CLAMD_CONF_StreamMaxLength",
                content,
                f"{name} must raise clamd's StreamMaxLength above the app's own "
                f"upload cap (upstream default is 25M).",
            )
