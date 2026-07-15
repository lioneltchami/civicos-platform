const SPEC_URL =
  "https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml";
const GOVSTACK_OVERVIEW_URL =
  "https://consent.govstack.global/con-23q4/2-description";
const GOVSTACK_FUNCTIONAL_URL =
  "https://consent.govstack.global/con-23q4/6-functional-requirements";
const GOVSTACK_SERVICE_APIS_URL =
  "https://consent.govstack.global/con-23q4/8-service-apis";
const GOVSTACK_TESTING_URL =
  "https://testing.govstack.global/requirements/form";
const PUBLIC_SITE_URL = "https://civicosbb.pages.dev";
const PUBLIC_DOCS_URL = "https://civicosbb.pages.dev/docs/consent";
const API_RUNTIME_URL = "https://api.civicosbb.ca";
const API_SCHEMA_URL = "https://api.civicosbb.ca/api/v1/consent/schema/";
const API_DOCS_URL = "https://api.civicosbb.ca/api/v1/consent/docs/";
const DJANGO_RUNTIME_NOTE =
  "The live CivicOS backend is public at https://api.civicosbb.ca, with GovStack-facing consent schema at /api/v1/consent/schema/ and generated API docs at /api/v1/consent/docs/.";

const QUICK_FACTS = [
  { label: "Building block", value: "Consent" },
  { label: "Target spec", value: "GovStack Consent BB v23Q4" },
  { label: "API base path", value: "/api/v1/consent/" },
  { label: "Namespaces", value: "config, service, audit" },
  {
    label: "Documentation URL",
    value: PUBLIC_DOCS_URL,
    href: PUBLIC_DOCS_URL,
  },
  {
    label: "Website URL",
    value: PUBLIC_SITE_URL,
    href: PUBLIC_SITE_URL,
  },
  {
    label: "Live API schema",
    value: API_SCHEMA_URL,
    href: API_SCHEMA_URL,
  },
];

const STATUS_FACTS = [
  { label: "Current status", value: "Consent submission documentation page" },
  {
    label: "Tested scope",
    value: "GovStack config, service, verification, and audit route families",
  },
  { label: "Last reviewed", value: "2026-07-14" },
  { label: "Public site role", value: "Website, docs, and assessment context" },
];

const TARGET_AND_EVIDENCE = [
  {
    title: "GovStack target contract",
    items: [
      "The official GovStack Consent v23Q4 OpenAPI file remains the benchmark contract CivicOS is being assessed against.",
      "This page does not rewrite or sanitize that target specification.",
      "Reviewers should use the official GovStack links below when they want the normative contract text itself.",
    ],
  },
  {
    title: "CivicOS implementation evidence",
    items: [
      "CivicOS mounts GovStack consent routes under /api/v1/consent/ inside the Django application runtime.",
      "The live Django runtime is publicly reachable at https://api.civicosbb.ca and exposes GovStack-facing generated schema routes at /api/v1/consent/schema/ and /api/v1/consent/docs/.",
      "This Pages site is the public documentation surface; the backend runtime is a separate live service that reviewers can inspect directly.",
    ],
  },
];

const IMPLEMENTATION_EVIDENCE = [
  {
    title: "Route families",
    body: "Config policy/data-agreement/webhook flows, service consent-record and draft flows, verification endpoints, and audit reads are all mounted beneath /api/v1/consent/.",
  },
  {
    title: "Authentication shape",
    body: "The Django API is configured for Bearer JWT authentication in DRF, and the GovStack consent views apply separate permission classes for config, service, verification, and audit surfaces.",
  },
  {
    title: "Runtime schema",
    body: "The application runtime includes GovStack-facing public generated schema and API docs at api.civicosbb.ca, which gives reviewers direct implementation evidence instead of a docs-only claim.",
  },
];

const SUBMISSION_REFERENCES = [
  {
    title: "Software website",
    href: PUBLIC_SITE_URL,
    body: "Use the CivicOS homepage in the testing form when asked for the product website.",
  },
  {
    title: "Software documentation",
    href: PUBLIC_DOCS_URL,
    body: "Use this Consent-specific page, not the generic docs hub, for the current building block submission.",
  },
  {
    title: "Official GovStack service APIs",
    href: GOVSTACK_SERVICE_APIS_URL,
    body: "Use the official Service APIs page when a reviewer needs the normative API chapter rather than CivicOS summary copy.",
  },
];

const REVIEWER_RUNTIME_LINKS = [
  {
    title: "Live CivicOS API schema",
    href: API_SCHEMA_URL,
    body: "Public generated OpenAPI output filtered to the GovStack consent submission surface.",
  },
  {
    title: "Live CivicOS API docs",
    href: API_DOCS_URL,
    body: "Browsable GovStack-facing consent API documentation served by the public backend runtime.",
  },
  {
    title: "Live CivicOS API host",
    href: API_RUNTIME_URL,
    body: "Public backend hostname used for the runtime and schema/docs routes.",
  },
];

const IMPLEMENTATION_AREAS = [
  {
    title: "Configuration namespace",
    items: [
      "Policy, data agreement, individual, and webhook resources are mounted under the GovStack config namespace in the Django application.",
      "The implementation includes policy and data-agreement revision concepts intended to support auditability over time.",
    ],
  },
  {
    title: "Service namespace",
    items: [
      "Consent records, draft records, signatures, verification endpoints, and data-agreement record flows are present in the service namespace.",
      "Right-to-be-forgotten handling is part of the consent service surface and is documented here as a first-class workflow.",
    ],
  },
  {
    title: "Audit and verification",
    items: [
      "Audit endpoints exist for consent records and data agreements, alongside verification-oriented service routes.",
      "The model layer includes consent revisions, signatures, and audit entries as explicit domain concepts.",
    ],
  },
  {
    title: "Platform integration",
    items: [
      "Consent is not isolated from the rest of CivicOS; it sits inside the same Django platform used for forms, workflows, and citizen-facing services.",
      "The public reviewer links stay consent-scoped even though the broader platform contains additional building blocks.",
    ],
  },
];

const OFFICIAL_REFERENCE_LINKS = [
  {
    label: "Open official GovStack Consent overview",
    href: GOVSTACK_OVERVIEW_URL,
    desc: "Human-readable description and narrative guidance for the Consent building block.",
  },
  {
    label: "Open official GovStack Service APIs page",
    href: GOVSTACK_SERVICE_APIS_URL,
    desc: "Current GovStack API chapter for the v23Q4 Consent building block.",
  },
  {
    label: "Open GovStack functional requirements",
    href: GOVSTACK_FUNCTIONAL_URL,
    desc: "Functional requirements used to understand the expected Consent behavior.",
  },
  {
    label: "Open official GovStack OpenAPI file",
    href: SPEC_URL,
    desc: "Raw v23Q4 OpenAPI source of record.",
  },
  {
    label: "Open GovStack testing portal",
    href: GOVSTACK_TESTING_URL,
    desc: "Assessment portal used for the current submission flow.",
  },
];

const WHY_NOT_EMBED = [
  "The official GovStack OpenAPI contains its own wording, spacing, and renderer behavior, including typos and sparse descriptions in some operations.",
  "Embedding that raw renderer directly into this page makes CivicOS look responsible for upstream formatting and wording issues that are not ours.",
  "Linking the official references instead keeps the reviewer path accurate while preserving a cleaner CivicOS documentation experience.",
];

export default function ConsentDocs() {
  return (
    <div className="bg-[#060d1f] pt-16">
      <section className="relative overflow-hidden border-b border-white/8 px-6 py-20 md:py-24 lg:px-8">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute left-1/2 top-0 h-[26rem] w-[26rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="relative mx-auto max-w-6xl">
          <span className="inline-flex rounded-full border border-sky-400/20 bg-sky-400/10 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-200">
            Consent documentation
          </span>
          <h1 className="mt-8 max-w-4xl text-5xl font-semibold tracking-[-0.04em] text-white md:text-6xl">
            CivicOS Consent Building Block documentation
          </h1>
          <p className="mt-6 max-w-4xl text-lg leading-8 text-white/60">
            This page is the public documentation route for the current Consent
            assessment cycle. It separates the upstream GovStack target
            specification from CivicOS implementation evidence so reviewers can
            see both what the building block expects and how CivicOS presents
            its current consent surface.
          </p>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Use on testing.govstack.global
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Consent submission details
            </h2>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {QUICK_FACTS.map((fact) => (
              <article
                key={fact.label}
                className="rounded-[24px] border border-white/10 bg-white/[0.04] p-5"
              >
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/35">
                  {fact.label}
                </p>
                {fact.href ? (
                  <a
                    href={fact.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-4 inline-flex break-all text-xl font-semibold text-white transition-colors hover:text-sky-200"
                  >
                    {fact.value}
                  </a>
                ) : (
                  <p className="mt-4 break-words text-xl font-semibold text-white">
                    {fact.value}
                  </p>
                )}
              </article>
            ))}
          </div>

          <article className="mt-6 rounded-[28px] border border-sky-400/20 bg-sky-950/20 p-6">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-200/80">
              Recommended form usage
            </p>
            <p className="mt-4 text-sm leading-7 text-white/72">
              Use the homepage for the software website field, this page for the
              software documentation field, and the CivicOS square mark for the
              software logo upload. This route is intentionally aligned to the
              Consent building block so reviewers land on Consent-specific
              context rather than on broader CMS or roadmap pages.
            </p>
          </article>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Submission-ready links
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Use the exact route that matches the current submission
            </h2>
            <p className="mt-5 text-lg leading-8 text-white/55">
              This site is intended to support a staged GovStack submission
              path. Right now, Consent is the active building block, so the most
              important public links should take reviewers directly to the
              CivicOS homepage, this Consent page, and the official GovStack API
              chapter.
            </p>
          </div>

          <div className="grid gap-5 md:grid-cols-3">
            {SUBMISSION_REFERENCES.map((item) => (
              <a
                key={item.href}
                href={item.href}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6 transition-colors hover:border-white/20"
              >
                <h3 className="text-2xl font-semibold text-white">
                  {item.title}
                </h3>
                <p className="mt-4 text-sm leading-7 text-white/58">
                  {item.body}
                </p>
                <p className="mt-5 break-all text-sm font-semibold text-sky-300">
                  {item.href}
                </p>
              </a>
            ))}
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Current implementation status
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              What this page is and is not
            </h2>
            <p className="mt-5 text-lg leading-8 text-white/55">
              This static page is a reviewer-facing documentation surface. It
              gives context around the Consent building block, points to the
              upstream GovStack target contract, and summarizes how the CivicOS
              Django application organizes its current implementation and public
              consent-only evidence routes.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {STATUS_FACTS.map((fact) => (
              <article
                key={fact.label}
                className="rounded-[24px] border border-white/10 bg-white/[0.04] p-5"
              >
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/35">
                  {fact.label}
                </p>
                <p className="mt-4 break-words text-lg font-semibold text-white">
                  {fact.value}
                </p>
              </article>
            ))}
          </div>

          <article className="mt-6 rounded-[28px] border border-amber-300/20 bg-amber-400/5 p-6">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-200/90">
              Known review caveats
            </p>
            <div className="mt-4 space-y-3">
              {[
                "The official GovStack API reference remains the certification benchmark; the live CivicOS runtime is implementation evidence, not the benchmark itself.",
                "The public backend lives at api.civicosbb.ca, while this site remains the reviewer-facing website and documentation surface.",
                "This page summarizes CivicOS route families and implementation shape for reviewers rather than duplicating the full backend schema output inline.",
              ].map((item) => (
                <div
                  key={item}
                  className="flex gap-3 text-sm leading-7 text-white/70"
                >
                  <span className="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-amber-200" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </article>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Target versus evidence
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Separate the benchmark from the runtime
            </h2>
          </div>

          <div className="grid gap-5 md:grid-cols-2">
            {TARGET_AND_EVIDENCE.map((section) => (
              <article
                key={section.title}
                className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6"
              >
                <h3 className="text-2xl font-semibold text-white">
                  {section.title}
                </h3>
                <ul className="mt-5 space-y-3">
                  {section.items.map((item) => (
                    <li
                      key={item}
                      className="flex gap-3 text-sm leading-7 text-white/58"
                    >
                      <span className="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-sky-300" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Implementation scope
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              What the Consent layer covers
            </h2>
          </div>

          <div className="grid gap-5 md:grid-cols-2">
            {IMPLEMENTATION_AREAS.map((section) => (
              <article
                key={section.title}
                className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6"
              >
                <h3 className="text-2xl font-semibold text-white">
                  {section.title}
                </h3>
                <ul className="mt-5 space-y-3">
                  {section.items.map((item) => (
                    <li
                      key={item}
                      className="flex gap-3 text-sm leading-7 text-white/58"
                    >
                      <span className="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-sky-300" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Implementation evidence
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Concrete CivicOS proof points
            </h2>
          </div>

          <div className="grid gap-5 md:grid-cols-3">
            {IMPLEMENTATION_EVIDENCE.map((item) => (
              <article
                key={item.title}
                className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6"
              >
                <h3 className="text-2xl font-semibold text-white">
                  {item.title}
                </h3>
                <p className="mt-4 text-sm leading-7 text-white/58">
                  {item.body}
                </p>
              </article>
            ))}
          </div>

          <article className="mt-6 rounded-[28px] border border-white/10 bg-white/[0.04] p-6">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/35">
              Runtime note
            </p>
            <p className="mt-4 text-sm leading-7 text-white/58">
              {DJANGO_RUNTIME_NOTE}
            </p>
          </article>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Live runtime links
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Public implementation endpoints
            </h2>
            <p className="mt-5 text-lg leading-8 text-white/55">
              These are the public CivicOS runtime endpoints a reviewer can open
              right now to inspect the live backend surface behind this
              submission.
            </p>
          </div>

          <div className="grid gap-5 md:grid-cols-3">
            {REVIEWER_RUNTIME_LINKS.map((item) => (
              <a
                key={item.href}
                href={item.href}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6 transition-colors hover:border-white/20"
              >
                <h3 className="text-2xl font-semibold text-white">
                  {item.title}
                </h3>
                <p className="mt-4 text-sm leading-7 text-white/58">
                  {item.body}
                </p>
                <p className="mt-5 break-all text-sm font-semibold text-sky-300">
                  {item.href}
                </p>
              </a>
            ))}
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto grid max-w-7xl gap-12 lg:grid-cols-[1fr_0.9fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Official references
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Use the official GovStack references directly
            </h2>
            <div className="mt-8 grid gap-3">
              {OFFICIAL_REFERENCE_LINKS.map((ref) => (
                <a
                  key={ref.href}
                  href={ref.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-2xl border border-white/10 bg-white/[0.04] px-5 py-4 text-sm text-white/70 transition-colors hover:border-white/20 hover:text-white"
                >
                  <div className="flex items-center justify-between gap-4">
                    <span>{ref.label}</span>
                    <svg
                      className="h-4 w-4 flex-shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
                      />
                    </svg>
                  </div>
                  <p className="mt-2 text-xs leading-6 text-white/45">
                    {ref.desc}
                  </p>
                </a>
              ))}
            </div>
          </div>

          <article className="rounded-[32px] border border-sky-400/20 bg-sky-950/20 p-7">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-200/80">
              Why the raw embed was removed
            </p>
            <h3 className="mt-4 text-2xl font-semibold text-white">
              Cleaner reviewer experience
            </h3>
            <div className="mt-4 space-y-3">
              {WHY_NOT_EMBED.map((item) => (
                <div
                  key={item}
                  className="flex gap-3 text-sm leading-7 text-white/58"
                >
                  <span className="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-sky-300" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </article>
        </div>
      </section>
    </div>
  );
}
