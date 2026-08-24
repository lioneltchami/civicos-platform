# CivicOS PR #1 — Production Dependency Security Remediation

**Date:** 2026-08-24
**Scope:** PR #1 post-public CI remediation, dependency/security increment only
**Status:** Local resolver, audit, and application regression verification complete; pending focused commit, normal push, and CI confirmation.

## Remediation

The production requirement inputs and generated manifests now pin the audit-clearing compatible releases identified from CI run `32705353937`: Django 5.2.17, Wagtail 7.4.3, django-allauth 65.14.1, Pillow 12.3.0, Brotli 1.2.0, sqlparse 0.6.0, urllib3 2.7.0, cryptography 50.0.0, and cffi 2.0.0. The change retains the existing `pip-audit -r requirements/production.txt` job unchanged and introduces no ignores, suppressions, broad exemptions, or workflow changes.

## Local Evidence

| Verification | Result |
|---|---|
| Clean Python 3.12 resolver | Resolved the complete production dependency set |
| Production requirements audit | `No known vulnerabilities found` without suppressions |
| Upgraded Django/Wagtail runtime | Django 5.2.17 and Wagtail 7.4.3 imported successfully |
| CI-equivalent parallel Django suite | 6,457 tests passed; 18 skipped |
| `git diff --check` | Passed |

The full-suite process generated ephemeral test-only credential values in process output. They were not retained in repository evidence, documentation, commits, or user-facing materials.

## Boundaries

This increment changes only requirement pins and the generated manifests. It does not authorize merge, deployment, staging, production operations, secret access, release, submission, provider or payment-rail activation, or any GovStack certification or conformance claim.
