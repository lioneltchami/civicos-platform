# Local File Management Runner — Level A Evidence Summary

> **Local File Management runner Level A only.** This evidence does not claim equivalence to the official GovStack Content Management System or a distinct official File Management Building Block. It does not claim official-harness execution, staging, certification, conformance, release, submission, or production authorisation.

| Layer | Executed result | Evidence |
|---|---:|---|
| All | 1,159 passed; exit 0 | `all/pytest-all.log`, `all/runner-all.log`, JUnit and coverage XML |
| Unit | 317 passed; exit 0 | `unit/pytest-unit.log`, `unit/runner-unit.log` |
| Integration | 637 passed; exit 0 | `integration/pytest-integration.log`, `integration/runner-integration.log` |
| End-to-end | 205 passed; exit 0 | `e2e/pytest-e2e.log`, `e2e/runner-e2e.log` |
| Runner contract | 5 passed; exit 0 | `runner-contract.log` |

The passing all-layer evidence covers the manifest-scoped `apps/documents/tests` suite, including upload/content gating, scan and quarantine paths, download/access-token boundaries, retention/legal-hold/quarantine controls, PIPEDA/redaction, and audit test surfaces. No optional dependency skip was observed in the successful current run. Real ClamAV daemon, production cloud storage/KMS, staging, and the official CMS/File Management harness are not exercised by this local evidence and remain out of scope.

The retained evidence was scanned for common token, secret-key, AWS credential, password, and signed-URL patterns before storage. No matching value was retained.
