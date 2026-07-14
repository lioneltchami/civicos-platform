const TOOL_DESCRIPTION = 'CivicOS is a Django 5.2 and Wagtail-based government content platform prepared for GovStack Content Management System submission. It provides multilingual page management, reusable content blocks, accessible media and document handling, site alerts, navigation menus, and integrated forms with consent and retention controls.'

const QUICK_FACTS = [
  { label: 'Software name', value: 'CivicOS' },
  { label: 'Submission target', value: 'Content Management System' },
  { label: 'Suggested version', value: '1.0.0' },
  { label: 'Documentation URL', value: 'Use this page URL' },
]

const EVIDENCE = [
  {
    title: 'Structured page hierarchy',
    items: [
      'HomePage, GenericPage, ServiceIndexPage, ServicePage, NewsIndexPage, and NewsPage are implemented in the CMS app.',
      'The model layer explicitly separates public information, services, and news instead of collapsing everything into one page type.',
    ],
  },
  {
    title: 'Reusable publishing blocks',
    items: [
      'Heading, rich text, image, call to action, accordion, and alert blocks are exposed through the main ContentStreamBlock.',
      'Heading levels are constrained and alt text is handled on the image model, which improves authoring discipline.',
    ],
  },
  {
    title: 'Reusable site objects',
    items: [
      'SiteAlert and NavigationMenu models provide reusable government-wide content objects.',
      'Custom image and document models give the CMS a more realistic public-sector media layer.',
    ],
  },
  {
    title: 'Citizen-facing forms',
    items: [
      'FormPage extends Wagtail forms with consent text, retention days, and custom submission handling.',
      'FormSubmission supports PII flags, redaction, consent recording, and expiry metadata.',
    ],
  },
]

const ALIGNMENT = [
  {
    title: 'Government websites and information access',
    desc: 'The official GovStack CMS description centers public information and service delivery through government websites. CivicOS already has dedicated models for service pages, news, and general public content.',
  },
  {
    title: 'Open standards and maintainability',
    desc: 'A Django and Wagtail implementation with explicit content models is a better fit for maintainable government publishing than a one-off static marketing site.',
  },
  {
    title: 'Interoperable building-block positioning',
    desc: 'The CMS layer sits alongside forms and consent work in the same platform, which makes later GovStack-aligned expansion credible without forcing all claims into the first submission.',
  },
]

const REFERENCES = [
  { label: 'GovStack CMS description', href: 'https://specs.govstack.global/content-management-system/2-description' },
  { label: 'GovStack CMS synergies', href: 'https://specs.govstack.global/content-management-system/8-synergies-with-other-govstack-building-blocks' },
  { label: 'GovStack CMS working group repository', href: 'https://github.com/GovStackWorkingGroup/bb-cms' },
  { label: 'testing.govstack.global', href: 'https://testing.govstack.global' },
]

export default function Docs() {
  return (
    <div className="bg-[#060d1f] pt-16">
      <section className="relative overflow-hidden border-b border-white/8 px-6 py-24 lg:px-8">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute left-1/2 top-0 h-[26rem] w-[26rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="relative mx-auto max-w-5xl">
          <span className="inline-flex rounded-full border border-sky-400/20 bg-sky-400/10 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-200">
            Submission documentation
          </span>
          <h1 className="mt-8 text-5xl font-semibold tracking-[-0.04em] text-white md:text-6xl">
            Content Management System submission dossier
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-white/60">
            This page replaces the old consent API embed and gives you a cleaner public documentation URL for a first submission. It is intentionally written around the CivicOS CMS implementation that is already visible in this repo.
          </p>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">Use on testing.govstack.global</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">Copy-ready submission details</h2>
          </div>

          <div className="grid gap-5 lg:grid-cols-[0.95fr_1.05fr]">
            <div className="grid gap-4 sm:grid-cols-2">
              {QUICK_FACTS.map((fact) => (
                <article key={fact.label} className="rounded-[24px] border border-white/10 bg-white/[0.04] p-5">
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/35">{fact.label}</p>
                  <p className="mt-4 text-xl font-semibold text-white">{fact.value}</p>
                </article>
              ))}
            </div>

            <article className="rounded-[28px] border border-sky-400/20 bg-sky-950/20 p-6">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-200/80">Suggested tool description</p>
                  <p className="mt-2 text-sm text-white/45">325 characters, within the 400-character limit shown on the form.</p>
                </div>
                <span className="rounded-full border border-sky-400/20 bg-sky-400/10 px-3 py-1 text-xs font-semibold text-sky-200">CMS first</span>
              </div>
              <p className="mt-5 rounded-2xl border border-white/8 bg-[#091321] p-5 text-sm leading-7 text-white/72">
                {TOOL_DESCRIPTION}
              </p>
              <p className="mt-4 text-sm leading-7 text-white/45">
                For the website field, use the deployed homepage URL. For the documentation field, use this page&apos;s deployed URL.
              </p>
            </article>
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">Code-backed evidence</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">What the repo already proves</h2>
          </div>

          <div className="grid gap-5 md:grid-cols-2">
            {EVIDENCE.map((section) => (
              <article key={section.title} className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6">
                <h3 className="text-2xl font-semibold text-white">{section.title}</h3>
                <ul className="mt-5 space-y-3">
                  {section.items.map((item) => (
                    <li key={item} className="flex gap-3 text-sm leading-7 text-white/58">
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

      <section className="border-b border-white/8 px-6 py-20 lg:px-8">
        <div className="mx-auto grid max-w-7xl gap-16 lg:grid-cols-[0.9fr_1.1fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">Why CMS first</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">A stronger first review story</h2>
            <p className="mt-6 text-lg leading-8 text-white/58">
              The public site is no longer trying to present every CivicOS capability as equally mature. This first pass keeps the submission surface focused on the building block with the clearest website, clearest documentation, and clearest implementation trace inside the codebase.
            </p>
            <p className="mt-6 text-base leading-7 text-white/45">
              Consent remains relevant in the platform, but it should not dominate the website if the immediate goal is a clean first CMS submission.
            </p>
          </div>

          <div className="grid gap-4">
            {ALIGNMENT.map((item) => (
              <article key={item.title} className="rounded-[24px] border border-white/10 bg-white/[0.04] p-6">
                <h3 className="text-lg font-semibold text-white">{item.title}</h3>
                <p className="mt-3 text-sm leading-7 text-white/55">{item.desc}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="px-6 py-20 lg:px-8">
        <div className="mx-auto grid max-w-7xl gap-12 lg:grid-cols-[1fr_0.9fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">References</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">External links to support the submission</h2>
            <div className="mt-8 grid gap-3">
              {REFERENCES.map((ref) => (
                <a
                  key={ref.href}
                  href={ref.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/[0.04] px-5 py-4 text-sm text-white/70 transition-colors hover:border-white/20 hover:text-white"
                >
                  <span>{ref.label}</span>
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                  </svg>
                </a>
              ))}
            </div>
          </div>

          <article className="rounded-[32px] border border-sky-400/20 bg-sky-950/20 p-7">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-200/80">Practical note</p>
            <h3 className="mt-4 text-2xl font-semibold text-white">This page is the documentation URL now</h3>
            <p className="mt-4 text-sm leading-7 text-white/58">
              Before these edits, `/docs` was an embedded Consent API viewer. After the update, it is a CMS submission dossier built for reviewers and procurement screens. That makes it far better suited to the public documentation field in the GovStack testing form.
            </p>
          </article>
        </div>
      </section>
    </div>
  )
}
