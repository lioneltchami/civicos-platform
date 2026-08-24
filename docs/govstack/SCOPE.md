# CivicOS GovStack Scope and Evidence Boundary

CivicOS maintains **local implementation evidence** for four GovStack-oriented areas: **Consent**, **Payments**, **Scheduler through Appointments**, and **File Management through Documents**. The corresponding machine-readable records in [`authority-manifest.json`](authority-manifest.json) and [`traceability.json`](traceability.json) preserve the pinned comparison source, local evidence, and unverified official-wire boundaries.

> No document in this directory is a certification, compliance, or official-harness pass claim. A local test pass is regression evidence only. Official wire conformance requires a reviewed adapter and archived results from the pinned official test assets.

## Current claimed Building Block boundary

| Area | Current evidence position | External conformance position |
|---|---|---|
| Consent | CivicOS consent lifecycle, signatures, revisions, audit, RTBF and callback protections are implemented locally. | Official path/schema/status/audit acceptance remains unverified. |
| Payments | CivicOS has dedicated GovStack models, auth, serializers, services, tasks, views and tests. | Official asynchronous, callback, heartbeat, typed-error and replay semantics remain unverified. |
| Scheduler | CivicOS Appointments exposes GovStack-shaped entities, resources, events, subscriptions and appointments. | Official operation mapping remains unverified; deployment is limited to one government. |
| File Management | CivicOS Documents provides upload, scanning/quarantine, retention, download and access controls. | Official resource/lifecycle/status/error equivalence remains unverified. |

## Local modules that are not current GovStack Building Block claims

Messaging/Notifications, Workflow and CMS/Wagtail are CivicOS application modules. They must not be presented as GovStack Messaging, Workflow or CMS conformance unless a separate authority comparison, mapping, implementation and official-run workstream is completed.

## Official catalog work not done yet

Cloud and Infrastructure Hosting, Digital Registries, eMarketplace, eSignature, GIS, Identity, IM Connector, Information Mediator, Registration, Template, UX and Wallet are not done yet in the CivicOS Building Block scope. Their absence is a scope fact, not an inferred implementation defect.

## Scheduler deployment guard

The Scheduler/Appointments GovStack surface supports **single-government** deployment only. Set `GOVSTACK_SCHEDULER_DEPLOYMENT_SCOPE=single-government` (the default). Any other value is deliberately rejected at authentication time because multi-government registered-BB role isolation has not been implemented or validated.

## Later official evidence route

Use [`scripts/archive_govstack_run.py`](../../scripts/archive_govstack_run.py) only after a reviewed, non-production adapter and pinned official dependencies are available. The tool records metadata, command, output, return code and traceability-row references; it never marks an unexecuted run as passing.
