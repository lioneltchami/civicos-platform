# CivicOS Ship Secret Triage — 2026-08-23

> **Triage result: all listed reviewed matches are `FALSE_POSITIVE_SAFE`.**
>
> This conclusion permits only the strict allowlist recorded in `config/ship-secret-scan-allowlist.json`. It does not disable or weaken the full-range secret scan, and it does not authorize deployment, staging, release, submission, provider activation, or secret access.

## Reviewed findings

| Triage ID | Path and line context | Classification | Rationale | Human disposition |
|---|---|---|---|---|
| SHIP-SEC-001 | `scripts/validate_file_management_level_a.sh:75`; SHA-256 `c2d34928d653b7811ed13fac198a29ddad02fcefb087ce469ad5833c10b51f9e` | `FALSE_POSITIVE_SAFE` | The matched line is a detector expression used to reject obvious sensitive values in retained local File Management evidence. It is security-control code, not a credential value, and the validator fails closed if the detector finds a value. | Safe to ship as reviewed security-control code. |
| SHIP-SEC-002 | `scripts/validate_payments_rb02_level_a.sh:49`; SHA-256 `75084b7c109bc4b577515b42fb803a26596a61df8ca7c81c7838ff8f0c4e0f89` | `FALSE_POSITIVE_SAFE` | The matched line is a detector expression applied to the canonical RB-02 artifact payload. It rejects live-provider prefixes, private-key markers, credential-like assignments, authorization headers, and excluded workstream paths. It is security-control code, not a credential. | Safe to ship as reviewed security-control code. |
| SHIP-SEC-003 | `tests/test_medium_priority_contracts.py:66`; SHA-256 `213188d435c0b9c2fe8d00cea6df3129524b0b9797896de89792d7bc53f39c3d` | `FALSE_POSITIVE_SAFE` | The matched line is a fixed local test-fixture password assignment for a non-routable `.invalid` address. It is not a reusable credential, provider secret, production configuration value, or secret-store reference. | Safe to ship while it remains test-only. |
| SHIP-SEC-004 | `apps/appointments/tests/test_services_availability.py:1285`; SHA-256 `dc1cc45514b5875a278f228e72a71fdb23cea592e1f511adfc1c8155953b20e7` | `FALSE_POSITIVE_SAFE` | Local appointment-test user fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-005 | `apps/auth_extension/tests/test_models.py:73`; SHA-256 `7ed54185f98d657eac2b9948ae683604288a36e9b51f51293eb47f37bd998fb6` | `FALSE_POSITIVE_SAFE` | Local model-test fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-006 | `apps/auth_extension/tests/test_models.py:94`; SHA-256 `5236e1e06ee5b19819d591ee80f20f7c55f4dd984d589333155e74a29a8f2cbd` | `FALSE_POSITIVE_SAFE` | Local model-test fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-007 | `apps/auth_extension/tests/test_security.py:47`; SHA-256 `175148c94b424b5690cfe9af75537d19b3d8a24154c2df88db9dabdfc5ad1777` | `FALSE_POSITIVE_SAFE` | Local security-test user fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-008 | `apps/auth_extension/tests/test_security.py:58`; SHA-256 `735ae16a1415781651b9c94efc15cd0a4bcee03c66b0931f16d9a9b7e974a857` | `FALSE_POSITIVE_SAFE` | Local password-hashing test fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-009 | `apps/backoffice/tests/test_dashboard.py:74`; SHA-256 `46d51ad62e17cb2fde9ff970ec30c287e7a98c3a2a3df2afb556a12cb21f362e` | `FALSE_POSITIVE_SAFE` | Local dashboard-access test fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-010 | `apps/backoffice/tests/test_dashboard.py:88`; SHA-256 `f44155e9204d2a1eb0db159ac0eb677187ce0fbcc8c44e61065401d240c8d4f9` | `FALSE_POSITIVE_SAFE` | Local staff dashboard-access test fixture. | Safe only for this exact test-only line. |
| SHIP-SEC-011 | `apps/volunteers/tests/test_services_screening.py:581`; SHA-256 `98f5ec8d8b9a5e17eb11e956e38a272504a904f3832bede3056fec9956731734` | `FALSE_POSITIVE_SAFE` | Local screening-test user fixture. | Safe only for this exact test-only line. |

## Ruff Increment Exact-Record Review — 2026-08-24

Two fresh independent reviewers re-reviewed all eight additional scanner findings. Each was classified `FALSE_POSITIVE_SAFE` as a pre-existing local test fixture, and every approval is restricted to the exact path, line, and SHA-256 listed above. The scanner policy now remains cardinality-neutral but fail-closed: its full-range observed finding set must equal the reviewed allowlist record set exactly. No scanner pattern, wildcard, directory exemption, line range, unmatched-finding bypass, workflow behavior, or secret access was changed.

## Allowlist policy

The allowlist has exactly eleven entries. An entry is usable only when all three values match the current worktree exactly: repository-relative path, one-based line number, and SHA-256 of the complete line. The scanner computes candidate findings from added lines in the full `origin/main..HEAD` range, re-computes each approved source-line digest, and requires the observed finding set to equal the eleven reviewed records exactly.

> A changed line, moved line, path-only match, missing baseline, malformed allowlist, missing reviewed match, duplicate entry, unreviewed finding, or scanner error is a failure. There are no wildcard, directory, detector-wide, range-wide, or global scanner exemptions.

## Reconsideration rule

Any change to an approved path, line, or digest invalidates its disposition. The change must be reviewed as a new candidate and receives no automatic approval from this record. Any new suspect match in the full range is an immediate ship stop pending human security review.

## Boundaries and non-claims

This triage is a local source-review record only. It makes no claim about external secret stores, staging, production, official GovStack validation, certification, conformance, release, submission, or deployment. The GovStack/staging campaign remains closed out/parked; SCH-02.2 remains open/out of scope; and money/provider rails remain off by default.
