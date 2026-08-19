# CivicOS GovStack API-Test Candidates

These packages prepare **local, disposable candidates** for the GovStack API-suite workflow. They implement the public testing procedure's candidate convention—`examples/<candidate>/test_entrypoint.sh --config api-suite`—without creating a testing-site account, contacting an external target, opening an official-repository pull request, submitting a compliance form, or claiming conformance.

## Candidate packages

| Package | CivicOS area | Pinned official source | Local harness port |
|---|---|---|---:|
| `civicos-consent` | Consent | `bb-consent@7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` | 8888 |
| `civicos-payments` | Payments | `bb-payments@4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` | 3333 |
| `civicos-scheduler` | Scheduler through Appointments | `bb-scheduler@d425be5cc0d6c606f351e5bf89be6d5c6c83c468` | 3333 |
| `civicos-file-management` | File Management through Documents | `bb-file-management@cf50bf4952491bd1ede775aa3c90a228319c3977` | 3003 |

Each entrypoint accepts **only** `--config api-suite`, refuses any target other than `GOVSTACK_TEST_TARGET=local`, generates an ephemeral Django secret, starts an isolated Docker Compose project on loopback, waits for `/health/`, and writes a `result/preflight.json` record. It does not invoke an official suite itself because the official suite owns the test invocation and report format.

## Local preparation

1. Obtain a reviewed checkout of the exact official Building Block revision recorded in the candidate manifest.
2. From the CivicOS repository root, run the relevant entrypoint. For example:

   ```bash
   ./examples/civicos-consent/test_entrypoint.sh --config api-suite
   ```

3. Confirm the candidate health endpoint on its package-specific loopback port.
4. From the pinned official repository's test directory, invoke its documented test entrypoint and preserve raw test output in the candidate `result/` directory. Do not edit the official test suite to make CivicOS appear compliant.
5. Stop and remove the local candidate after the run:

   ```bash
   docker compose --project-name civicos-consent-testing down --volumes
   ```

## Boundaries

A candidate package is **preparation evidence only**. It is not an API Compliance result, a functional self-assessment, a test-site submission, a production deployment, or a GovStack certification. External staging URLs, credentials, network exposure, test-site onboarding, official repository pull requests, form verification, and submission remain authorized-owner actions.

See [`docs/govstack/testing/TESTING_SITE_PREPARATION.md`](../docs/govstack/testing/TESTING_SITE_PREPARATION.md) for the authoritative preparation matrix and evidence record.
