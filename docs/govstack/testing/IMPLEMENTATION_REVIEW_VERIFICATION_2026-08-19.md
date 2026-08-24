# GovStack Testing Preparation — Implementation and Review Verification

**Date:** 2026-08-19  
**Scope:** Local-only CivicOS candidate preparation and pinned official-source test evidence. This record does **not** assert testing-site submission, external certification, staging validation, or cross-version wire conformance.

## Independent implementation workflow

A source-isolated implementation pass examined the CivicOS candidate packages against pinned official GovStack repositories for Payments, Scheduler, and File Management. The initial implementation corrected archive-based launcher provenance: a candidate can now record `archive-no-git` when launched from a verified source archive without pretending that a Git revision is available.

A separate blind reviewer received only the candidate source and authoritative pinned sources, rather than the implementation report. It identified three issues: caller-controlled service selection, caller-controlled management-command seeding, and a lack of regression coverage for those boundaries. A third, separate correction pass received only the review implementation brief and source state. Its two justified corrections are now integrated: fixed per-Building-Block service selection and an exact seed-command allowlist. Two other items were appropriately deferred because no official-suite evidence justified speculative protocol changes.

| Control | Final source state |
|---|---|
| Candidate target | The launcher refuses every target except `local`. |
| Compose services | The launcher uses a fixed mapping: Consent uses `db redis web consent-adapter`; Payments and Scheduler use `db redis web`; File Management uses `db redis clamav web`. An incompatible `GOVSTACK_CANDIDATE_SERVICES` override fails closed. |
| Fixture command | Only Consent can invoke `seed_govstack_consent_candidate`; all unallowlisted `GOVSTACK_CANDIDATE_SEED_COMMAND` values fail closed. |
| Archive provenance | A regular Git checkout records its exact `HEAD`; a source archive records `archive-no-git`. |
| Candidate scope | The non-document Payments and Scheduler overlays explicitly reset the unavailable development ClamAV dependency. The File Management candidate retains its scanner dependency because document scanning is within its scope. |

## Validation completed

The shared launcher and all four entrypoints passed Bash syntax validation. `tests/test_candidate_launcher_allowlist.sh` passed. The structural preparation validator passed and all four candidate Compose overlays passed `docker compose config -q` with a generated local secret. All archived evidence checksum manifests validated successfully, and `git diff --check` passed.

| Building Block | Pinned local result | Evidence location |
|---|---|---|
| Consent | **Passed** after the shared-launcher corrections: 2 features, 4 scenarios, and 16 steps passed using the official suite’s supported local `CONSENTBB_API_HOST=host.docker.internal:8888` configuration. | `runs/consent-2026-08-19-revalidated-pinned-7af4b62/` |
| Payments | **Not passing.** The unmodified official OpenAPI suite ran and produced 487 `PASSED`, 74 `FAILED`, and 225 `SKIPPED` status records. Observed failures include redirect and application-level response mismatches. | `runs/payments-2026-08-19-pinned-4b63a6b/` |
| Scheduler | **Harness blocked.** The unmodified pinned Docker harness reached its health check but used a published dependency-confusion placeholder instead of executable suite code, then exited zero without scenario results. | `runs/scheduler-2026-08-19-pinned-d425be5/` |
| File Management | **Candidate blocked.** The connected ARM64 host cannot pull `clamav/clamav:1.4`, so no official suite has been run. | `runs/file-management-2026-08-19-pinned-cf50bf4/` |

## Required next work

The current evidence supports no testing-site submission for Payments, Scheduler, or File Management. Payments requires a request-by-request review of the pinned API specification and raw failures before a narrowly scoped adapter or product change can be approved. Scheduler requires an official-maintainer-supported, pinned harness dependency solution; CivicOS must not silently replace the suite. File Management requires a reviewed local scanner strategy compatible with the target architecture before its official suite can begin.

Consent has local pinned-suite evidence but still requires completion of its functional-requirements matrix, approved product metadata and documentation links, authorised testing-site access, and a human-approved submission decision. The official testing procedure treats the API result report as a prerequisite for the API portion of the self-assessment; it does not make a local run an automatic submission.[1] [2]

## References

[1]: [GovStack — Steps to check compliance against a GovStack API spec](https://govstack-global.atlassian.net/wiki/spaces/GH/pages/221085697/Steps+to+check+compliance+against+a+GovStack+API+spec)  
[2]: [GovStack Testing — Software Requirements Compliance](https://testing.govstack.global/requirements)
