"""
Regression tests for documents/citizen/upload_presign.html.

Audit finding (HIGH, MASTER_BB_CERTIFIABILITY_REPORT.md → Documents Management BB):
the template unconditionally injected a ``success_action_redirect`` hidden field
whenever ``confirm_url`` was in the context — including when SITE_URL is unset and
the service layer therefore did NOT sign that field into the S3 POST policy.

S3 rejects an ENTIRE presigned POST that carries any form field not covered by the
signed policy ("Extra input fields"), so in that configuration every citizen upload
failed with AccessDenied. This is the same failure class as the Content-Type
field/condition bug, one layer up.

Invariant enforced here: the rendered form contains exactly the hidden inputs the
service layer signed (``upload_fields``) and nothing else.
"""

from __future__ import annotations

import re

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase


def _render(**context) -> str:
    request = RequestFactory().get("/documents/upload/")
    request.user = None
    base = {
        "upload_url": "https://bucket.s3.ca-central-1.amazonaws.com/",
        "upload_fields": {},
        "confirm_url": "/documents/upload/confirm/abc/",
    }
    base.update(context)
    return render_to_string(
        "documents/citizen/upload_presign.html", base, request=request
    )


def _hidden_input_names(html: str) -> list[str]:
    """
    Hidden input names inside the S3 POST form ONLY.

    The surrounding base template renders unrelated forms (language switcher,
    CSRF) that never reach S3, so the assertion must be scoped to the form whose
    action is the bucket endpoint.
    """
    start = html.index('id="s3-upload-form"')
    end = html.index("</form>", start)
    return re.findall(r'<input type="hidden" name="([^"]+)"', html[start:end])


class UploadPresignTemplateFieldTests(TestCase):
    def test_no_unsigned_success_action_redirect_when_absent_from_upload_fields(self):
        """
        SITE_URL unset → service omits success_action_redirect from the signed
        policy AND from upload_fields → the template must not add it back.
        """
        html = _render(
            upload_fields={"key": "documents/quarantine/x/y.bin", "policy": "abc"},
            confirm_url="/documents/upload/confirm/abc/",
        )
        self.assertNotIn("success_action_redirect", _hidden_input_names(html))

    def test_signed_success_action_redirect_is_rendered_exactly_once(self):
        """
        SITE_URL set → the service signs success_action_redirect into the policy
        and returns it in upload_fields → it must be rendered once, from the loop.
        """
        html = _render(
            upload_fields={
                "key": "documents/quarantine/x/y.bin",
                "policy": "abc",
                "success_action_redirect": "https://portal.example.gov.ca/confirm/",
            },
        )
        names = _hidden_input_names(html)
        self.assertEqual(names.count("success_action_redirect"), 1)

    def test_rendered_hidden_inputs_match_upload_fields_exactly(self):
        """
        The form must POST exactly the signed field set — any extra hidden field
        invalidates the S3 POST policy for every upload.
        """
        upload_fields = {
            "key": "documents/quarantine/x/y.bin",
            "Content-Type": "application/pdf",
            "x-amz-server-side-encryption": "aws:kms",
            "policy": "base64policy",
            "x-amz-signature": "deadbeef",
        }
        html = _render(upload_fields=upload_fields)
        self.assertEqual(sorted(_hidden_input_names(html)), sorted(upload_fields))
