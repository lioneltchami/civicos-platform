import { Link } from 'react-router-dom'

const SUBMISSION_FACTS = [
  {
    value: '6',
    label: 'page types',
    note: 'Home, generic, service, and news models in the Wagtail CMS layer.',
  },
  {
    value: '6',
    label: 'content blocks',
    note: 'Heading, rich text, image, CTA, accordion, and alert blocks.',
  },
  {
    value: '2',
    label: 'reusable site objects',
    note: 'Site alerts and navigation menus managed as reusable content.',
  },
  {
    value: '1',
    label: 'extended forms layer',
    note: 'Consent-aware forms with retention controls and PII redaction hooks.',
  },
]

const FOCUS_AREAS = [
  {
    label: 'First public submission',
    title: 'Content Management System',
    desc: 'The current website and documentation package are tuned for the GovStack CMS building block because the repo already proves the publishing models, content blocks, media handling, and citizen-facing forms.',
    cta: 'Review submission docs',
    to: '/docs',
    tone: 'sky',
  },
  {
    label: 'Adjacent implemented module',
    title: 'Consent',
    desc: 'Consent workflows exist in the same Django platform, but this website no longer treats that module as the first certification claim. It now sits on a separate review and hardening track.',
    cta: 'See module roadmap',
    to: '/building-blocks',
    tone: 'slate',
  },
  {
    label: 'Platform foundation',
    title: 'Municipal-ready delivery layer',
    desc: 'Reusable navigation, alerts, service pages, news pages, document models, and form submissions give the CMS a credible public-sector operating surface instead of a thin demo shell.',
    cta: 'Read platform context',
    to: '/about',
    tone: 'slate',
  },
]

const DELIVERY_NOTES = [
  {
    title: 'Structured publishing',
    desc: 'The CMS layer defines distinct page models for home, service, generic, and news content so editors are not forced into a single generic page template.',
  },
  {
    title: 'Accessible authoring',
    desc: 'Custom images carry alt text, heading blocks prevent h1 misuse, and alert and accordion blocks are designed for accessible rendering patterns.',
  },
  {
    title: 'Citizen service forms',
    desc: 'Form pages add consent text, retention windows, PII flags, and redaction support, which is much closer to government operations than a plain brochure site.',
  },
]

function FocusCard({ area }) {
  return (
    <article className={`group relative overflow-hidden rounded-[28px] border p-7 transition-all duration-300 hover:-translate-y-1 hover:border-white/20 hover:shadow-2xl hover:shadow-black/30 ${
      area.tone === 'sky'
        ? 'border-sky-400/25 bg-[linear-gradient(145deg,rgba(14,165,233,0.12),rgba(8,17,28,0.85)_55%,rgba(8,17,28,0.96))]'
        : 'border-white/10 bg-white/[0.04]'
    }`}>
      <div className="mb-4 flex items-center justify-between gap-4">
        <span className={`inline-flex rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${
          area.tone === 'sky'
            ? 'border-sky-400/20 bg-sky-400/10 text-sky-200'
            : 'border-white/10 bg-white/5 text-white/50'
        }`}>
          {area.label}
        </span>
      </div>
      <h3 className="max-w-xs text-2xl font-semibold text-white">{area.title}</h3>
      <p className="mt-4 max-w-md text-sm leading-7 text-white/60">{area.desc}</p>
      <Link
        to={area.to}
        className={`mt-7 inline-flex items-center gap-2 text-sm font-semibold transition-colors ${
          area.tone === 'sky' ? 'text-sky-300 hover:text-sky-200' : 'text-white/70 hover:text-white'
        }`}
      >
        {area.cta}
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
        </svg>
      </Link>
    </article>
  )
}

export default function Home() {
  return (
    <div className="bg-[#060d1f]">
      <section className="relative overflow-hidden border-b border-white/8 pt-24">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute left-1/2 top-24 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="absolute inset-0 opacity-[0.06]" style={{
          backgroundImage: 'linear-gradient(rgba(226,232,240,0.4) 1px,transparent 1px),linear-gradient(90deg,rgba(226,232,240,0.4) 1px,transparent 1px)',
          backgroundSize: '72px 72px',
        }} />

        <div className="relative mx-auto grid max-w-7xl gap-16 px-6 pb-24 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,420px)] lg:px-8">
          <div className="max-w-4xl py-12">
            <div className="inline-flex items-center gap-2 rounded-full border border-sky-400/20 bg-sky-400/10 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-200">
              <span className="h-2 w-2 rounded-full bg-sky-300" />
              First GovStack submission in preparation
            </div>

            <h1 className="mt-8 max-w-4xl text-5xl font-semibold leading-[1.02] tracking-[-0.04em] text-white md:text-6xl lg:text-7xl">
              A government website platform with a real CMS core
            </h1>

            <p className="mt-8 max-w-3xl text-lg leading-8 text-white/64 md:text-xl">
              CivicOS is a Django 5.2 and Wagtail platform. This public site is now aligned with the codebase we can prove today: a GovStack Content Management System submission built on structured page models, reusable content blocks, accessible media, and citizen-facing forms.
            </p>

            <div className="mt-10 flex flex-wrap gap-4">
              <Link to="/docs"
                className="inline-flex items-center gap-2 rounded-2xl bg-sky-600 px-7 py-3.5 text-sm font-semibold text-white transition-all hover:bg-sky-500 hover:shadow-xl hover:shadow-sky-500/25 active:scale-95">
                Submission docs
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </Link>
              <Link to="/building-blocks"
                className="inline-flex items-center gap-2 rounded-2xl border border-white/12 bg-white/6 px-7 py-3.5 text-sm font-semibold text-white transition-all hover:bg-white/10">
                Building block roadmap
              </Link>
            </div>

            <div className="mt-14 flex flex-wrap items-center gap-x-6 gap-y-3">
              {['Django 5.2', 'Wagtail CMS', 'Bilingual structure', 'Accessible authoring', 'Consent-aware forms'].map((item) => (
                <span key={item} className="text-sm font-medium text-white/45">
                  {item}
                </span>
              ))}
            </div>
          </div>

          <aside className="relative self-end rounded-[32px] border border-white/10 bg-white/[0.04] p-6 shadow-2xl shadow-black/20 backdrop-blur-sm">
            <div className="absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-sky-300/50 to-transparent" />
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-200/80">Submission snapshot</p>
            <div className="mt-6 grid gap-4">
              {SUBMISSION_FACTS.map((fact) => (
                <div key={fact.label} className="rounded-2xl border border-white/8 bg-[#091321] p-4">
                  <div className="flex items-end gap-3">
                    <span className="text-3xl font-semibold tracking-[-0.04em] text-white">{fact.value}</span>
                    <span className="pb-1 text-sm font-medium text-white/55">{fact.label}</span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-white/45">{fact.note}</p>
                </div>
              ))}
            </div>
          </aside>
        </div>
      </section>

      <section className="px-6 py-24 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-14 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">Current focus</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white md:text-5xl">
              A tighter public story for the first building block
            </h2>
            <p className="mt-5 max-w-2xl text-lg leading-8 text-white/55">
              The website now leads with what the codebase can substantiate line by line. That keeps the public narrative consistent with the submission package and makes reviewer verification easier.
            </p>
          </div>

          <div className="grid gap-5 lg:grid-cols-[1.15fr_0.95fr_0.95fr]">
            {FOCUS_AREAS.map((area) => (
              <FocusCard key={area.title} area={area} />
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-white/8 px-6 py-24 lg:px-8">
        <div className="mx-auto grid max-w-7xl gap-16 lg:grid-cols-[0.95fr_1.05fr]">
          <div className="max-w-xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">Why this works</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white md:text-5xl">
              Submission-ready because the implementation is already broad enough
            </h2>
            <p className="mt-6 text-lg leading-8 text-white/55">
              CivicOS is more than a landing page wrapped around a spec. The repo already contains editorial structure, reusable blocks, media models, and an extended forms layer, which gives the Content Management System claim operational depth.
            </p>
            <Link to="/about"
              className="mt-8 inline-flex items-center gap-2 text-sm font-semibold text-sky-300 transition-colors hover:text-sky-200">
              Read the platform context
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
              </svg>
            </Link>
          </div>

          <div className="grid gap-4">
            {DELIVERY_NOTES.map((item) => (
              <article key={item.title} className="rounded-[24px] border border-white/10 bg-white/[0.04] p-6">
                <h3 className="text-lg font-semibold text-white">{item.title}</h3>
                <p className="mt-3 text-sm leading-7 text-white/55">{item.desc}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}
