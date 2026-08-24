# CivicOS Ship Secret Triage — 2026-08-23

> **Triage result: all three pre-flight matches are `FALSE_POSITIVE_SAFE`.**
>
> This conclusion permits only the strict allowlist recorded in `config/ship-secret-scan-allowlist.json`. It does not disable or weaken the full-range secret scan, and it does not authorize deployment, staging, release, submission, provider activation, or secret access.

## Reviewed findings

| Triage ID | Path and line context | Classification | Rationale | Human disposition |
|---|---|---|---|---|
| SHIP-SEC-001 | `scripts/validate_file_management_level_a.sh:75`; SHA-256 `c2d34928d653b7811ed13fac198a29ddad02fcefb087ce469ad5833c10b51f9e` | `FALSE_POSITIVE_SAFE` | The matched line is a detector expression used to reject obvious sensitive values in retained local File Management evidence. It is security-control code, not a credential value, and the validator fails closed if the detector finds a value. | Safe to ship as reviewed security-control code. |
| SHIP-SEC-002 | `scripts/validate_payments_rb02_level_a.sh:49`; SHA-256 `75084b7c109bc4b577515b42fb803a26596a61df8ca7c81c7838ff8f0c4e0f89` | `FALSE_POSITIVE_SAFE` | The matched line is a detector expression applied to the canonical RB-02 artifact payload. It rejects live-provider prefixes, private-key markers, credential-like assignments, authorization headers, and excluded workstream paths. It is security-control code, not a credential. | Safe to ship as reviewed security-control code. |
| SHIP-SEC-003 | `tests/test_medium_priority_contracts.py:66`; SHA-256 `5b4bab93cd81d8c8014720085f91afee91d9fcf9f36575083e81d40b85e5b75f` | `FALSE_POSITIVE_SAFE` | The matched line is a fixed local test-fixture password assignment for a non-routable `.invalid` address. It is not a reusable credential, provider secret, production configuration value, or secret-store reference. | Safe to ship while it remains test-only. |

## Allowlist policy

The allowlist has exactly three entries. An entry is usable only when all three values match the current worktree exactly: repository-relative path, one-based line number, and SHA-256 of the complete line. The scanner computes candidate findings from added lines in the full `origin/main..HEAD` range, re-computes each approved source-line digest, and requires the observed finding set to equal the three reviewed records exactly.

> A changed line, moved line, path-only match, missing baseline, malformed allowlist, missing reviewed match, duplicate entry, unreviewed finding, or scanner error is a failure. There are no wildcard, directory, detector-wide, range-wide, or global scanner exemptions.

## Reconsideration rule

Any change to an approved path, line, or digest invalidates its disposition. The change must be reviewed as a new candidate and receives no automatic approval from this record. Any new suspect match in the full range is an immediate ship stop pending human security review.

## Boundaries and non-claims

This triage is a local source-review record only. It makes no claim about external secret stores, staging, production, official GovStack validation, certification, conformance, release, submission, or deployment. The GovStack/staging campaign remains closed out/parked; SCH-02.2 remains open/out of scope; and money/provider rails remain off by default.
