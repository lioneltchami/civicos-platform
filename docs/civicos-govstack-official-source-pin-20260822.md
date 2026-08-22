# CivicOS GovStack Official Source Pin

**Retrieval UTC:** `2026-08-22T22:58:50Z`
**Purpose:** Time-bounded official reference pin for portfolio reconciliation. It is not a conformance, certification, deployment, or submission record.

## Official catalog context

GovStack’s official specification catalog describes Building Blocks as composable, interoperable software modules that expose services through REST APIs.[1] The official home page presents the technical specifications as part of its technical blueprint for government digital infrastructure.[2]

| Local area | Official name | Canonical official specification | Official repository | Repository pin observed at retrieval | Neutral official scope summary |
|---|---|---|---|---|---|
| Consent | Consent | [2 Description][3] | [GovStackWorkingGroup/bb-consent][4] | `main` → `7af4b62a1c0b0d7073d42b37c71b0f08bea63dda` | The specification addresses auditable consent agreements for personal-data processing, including policy, purpose/data attributes, signatures, capture, and proof. It identifies surrounding authentication/authorization, disclosure agreements, and delegation as assumptions or outside its current scope. |
| Payments | Payments | [2 Description][5] | [GovStackWorkingGroup/bb-payments][6] | `main` → `4b63a6b5efbb20123c442e0b09fb44ec2d7e6b6a` | The specification describes common interfaces/components for tracking, evaluating, initiating, validating, processing, logging, comparing, and verifying digital payments across G2P, P2G, G2B, and B2G contexts, interoperating with external regulated financial entities. It does not define a new payment scheme; G2G is stated as outside the current specification. |
| Scheduler | Scheduler | [2 Description][7] | [GovStackWorkingGroup/bb-scheduler][8] | `main` → `d425be5cc0d6c606f351e5bf89be6d5c6c83c468` | The specification describes REST-API coordination of time-driven activities across building blocks and applications, including event planning, booking, tracking, triggering, notification, status reporting, and logs. Its examples include health and social-cash-transfer use cases while stating a general event-coordination model. |
| File Management / Document Management local area | **Content Management System** | [2 Description][9] | [GovStackWorkingGroup/bb-cms][10] | `main` → `68aaaa7a92962026252eecc77affdb981f7bcb98` | The official catalog name is **Content Management System**. The specification covers government/public-institution website CMS lifecycle and governance, including planning, development, deployment, hosting, security, maintenance, content management, accessibility, scalability, and integration. |

The published specification pages did not expose a consistent semantic version in the retrieved page content. The full repository SHAs above are time-bounded immutable repository-object pins, not claims that GitBook publication was generated from the same source commit.

## Other official catalog entries outside the CivicOS current claim scope

The retrieved catalog also lists Cloud Infrastructure, Digital Registries, E-Marketplace, E-Signature, Geographic Information System, Identity, Information Mediator, Messaging, Registration, and Wallet.[1] These are outside this four-area portfolio reconciliation. Architecture and Cross-Functional Requirements appears separately as an overarching specification rather than as a Building Block entry.[1]

The official catalog lists **Content Management System**, not a separate File Management Building Block. Therefore this record does not attribute CivicOS File Management work to a distinct official GovStack catalog Building Block.

## Pinning cautions

A rendered specification page and a repository default branch are mutable sources. This record retains exact URL, retrieval UTC time, default-branch name, and observed commit SHA, but it does not assert a permanent publication mapping, release tag, implementation conformity, legal sufficiency, operational readiness, or product endorsement.

## References

[1]: https://specs.govstack.global/technical-specifications/building-blocks "GovStack Specification — Building Blocks"
[2]: https://govstack.global "GovStack Global"
[3]: https://specs.govstack.global/consent/2-description "GovStack Consent — 2 Description"
[4]: https://github.com/GovStackWorkingGroup/bb-consent "GovStackWorkingGroup/bb-consent"
[5]: https://specs.govstack.global/payments/2-description "GovStack Payments — 2 Description"
[6]: https://github.com/GovStackWorkingGroup/bb-payments "GovStackWorkingGroup/bb-payments"
[7]: https://specs.govstack.global/scheduler/2-description "GovStack Scheduler — 2 Description"
[8]: https://github.com/GovStackWorkingGroup/bb-scheduler "GovStackWorkingGroup/bb-scheduler"
[9]: https://specs.govstack.global/content-management-system/2-description "GovStack Content Management System — 2 Description"
[10]: https://github.com/GovStackWorkingGroup/bb-cms "GovStackWorkingGroup/bb-cms"
