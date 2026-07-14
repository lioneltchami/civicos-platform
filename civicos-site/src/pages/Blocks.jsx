import { Link } from 'react-router-dom'

const BLOCKS = [
  {
    id: 'cms',
    name: 'Content Management System',
    version: 'First public submission target',
    tagline: 'Government websites, services, and content operations',
    description: 'This is the current lead building block for CivicOS. The repo already contains page hierarchies, reusable StreamField blocks, custom image and document models, reusable site objects, and a forms layer that fits government publishing better than a generic brochure stack.',
    status: 'submission',
    statusLabel: 'Submission in preparation',
    stack: ['Django 5.2', 'Wagtail', 'PostgreSQL', 'Wagtail forms'],
    features: [
      '6 page models for home, service, generic, and news content',
      '6 reusable content blocks for structured publishing',
      'Custom image alt text and accessible authoring patterns',
      'Integrated forms with consent and retention controls',
    ],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 01-2.25 2.25M16.5 7.5V18a2.25 2.25 0 002.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 002.25 2.25h13.5M6 7.5h3v3H6v-3z" />
      </svg>
    ),
  },
  {
    id: 'consent',
    name: 'Consent',
    version: 'Implemented in platform',
    tagline: 'Adjacent module on a separate compliance track',
    description: 'Consent functionality exists in the same codebase, but this site no longer treats it as the first public certification claim. It remains important platform work, with a separate hardening and compliance review path.',
    status: 'implemented',
    statusLabel: 'Implemented in codebase',
    stack: ['Django 5.2', 'DRF', 'PostgreSQL', 'Webhook dispatch'],
    features: [
      'Config, service, and audit namespaces are present',
      'Revision, signature, and audit concepts exist in the domain model',
      'Consent record lifecycle is implemented in the service layer',
      'Public marketing claims are narrower until compliance is re-verified',
    ],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.955 11.955 0 003 12.034C3 16.976 6.58 21.152 11.25 22v-.03a8.75 8.75 0 10.5-17.5v.008l-.75-.508z" />
      </svg>
    ),
  },
  {
    id: 'identity',
    name: 'Identity',
    version: 'Planned',
    tagline: 'Digital identity & authentication',
    description: 'Citizen identity verification, credential management, and federated SSO integration aligned with the GovStack Identity BB specification. Enables secure, privacy-preserving digital identity for any government service.',
    status: 'planned',
    statusLabel: 'Planned',
    stack: ['Django 5.2', 'OpenID Connect', 'W3C DID', 'PostgreSQL'],
    features: ['Federated identity (OIDC)', 'Verifiable credentials', 'KYC/identity proofing', 'Privacy by design'],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
      </svg>
    ),
  },
  {
    id: 'payments',
    name: 'Payments',
    version: 'Planned',
    tagline: 'Government payments & disbursements',
    description: 'Secure payment collection, benefits disbursement, and financial reconciliation aligned with GovStack Payments BB specification. Supports multiple payment rails and full audit compliance.',
    status: 'planned',
    statusLabel: 'Planned',
    stack: ['Django 5.2', 'Stripe / Open Banking', 'ISO 20022', 'PostgreSQL'],
    features: ['Multi-rail payments', 'Benefits disbursement', 'Reconciliation & reporting', 'GovStack Payments BB'],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 004.5 19.5z" />
      </svg>
    ),
  },
  {
    id: 'messaging',
    name: 'Messaging',
    version: 'Planned',
    tagline: 'Secure government-to-citizen messaging',
    description: 'Multi-channel secure messaging from government to citizens — notifications, alerts, and inbox management with delivery receipts, read receipts, and full audit trail.',
    status: 'planned',
    statusLabel: 'Planned',
    stack: ['Django 5.2', 'Celery', 'SMTP / SMS / Push', 'PostgreSQL'],
    features: ['Multi-channel delivery', 'Delivery & read receipts', 'Message threading', 'GovStack Messaging BB'],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
      </svg>
    ),
  },
  {
    id: 'scheduler',
    name: 'Scheduler',
    version: 'Planned',
    tagline: 'Appointment & workflow scheduling',
    description: 'Citizen-facing appointment booking, government workflow scheduling, and capacity management — aligned with GovStack Scheduler BB specification.',
    status: 'planned',
    statusLabel: 'Planned',
    stack: ['Django 5.2', 'Celery Beat', 'FullCalendar', 'PostgreSQL'],
    features: ['Citizen booking portal', 'Staff calendar management', 'Capacity & queue management', 'GovStack Scheduler BB'],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
      </svg>
    ),
  },
]

const statusStyle = {
  submission: { bg: 'bg-sky-500/10', border: 'border-sky-400/25', text: 'text-sky-200', dot: 'bg-sky-300' },
  implemented: { bg: 'bg-emerald-500/10', border: 'border-emerald-400/20', text: 'text-emerald-300', dot: 'bg-emerald-300' },
  planned: { bg: 'bg-white/5', border: 'border-white/15', text: 'text-white/35', dot: 'bg-white/25' },
}

function BlockCard({ block }) {
  const s = statusStyle[block.status]
  const isSubmission = block.status === 'submission'
  const isImplemented = block.status === 'implemented'

  return (
    <div className={`relative rounded-2xl border transition-all duration-300 overflow-hidden ${
      isSubmission
        ? 'border-sky-400/25 bg-gradient-to-br from-sky-950/45 via-[#0a1628] to-[#0a1628]'
        : 'border-white/10 bg-[#0a1628]/70'
    }`}>
      {isSubmission && (
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-sky-400/60 to-transparent" />
      )}

      <div className="p-8">
        <div className="flex items-start justify-between mb-6">
          <div className={`w-14 h-14 rounded-2xl flex items-center justify-center ${
            isSubmission ? 'bg-sky-500/15 text-sky-300' : isImplemented ? 'bg-emerald-500/10 text-emerald-300' : 'bg-white/5 text-white/30'
          }`}>
            {block.icon}
          </div>
          <div className="text-right">
            <span className={`inline-flex items-center gap-1.5 text-xs font-semibold rounded-full px-2.5 py-1 border ${s.bg} ${s.border} ${s.text}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${s.dot} ${isSubmission ? 'animate-pulse' : ''}`} />
              {block.statusLabel}
            </span>
            <p className="text-xs text-white/25 mt-1.5">{block.version}</p>
          </div>
        </div>

        <h3 className={`text-xl font-black mb-1 ${isSubmission ? 'text-white' : isImplemented ? 'text-white/90' : 'text-white/45'}`}>
          {block.name}
        </h3>
        <p className={`text-sm font-medium mb-3 ${isSubmission ? 'text-sky-200' : isImplemented ? 'text-emerald-200' : 'text-white/35'}`}>
          {block.tagline}
        </p>
        <p className={`text-sm leading-relaxed mb-6 ${isSubmission || isImplemented ? 'text-white/60' : 'text-white/35'}`}>
          {block.description}
        </p>

        <div className="grid grid-cols-2 gap-2 mb-6">
          {block.features.map((f) => (
            <div key={f} className={`flex items-center gap-2 text-xs ${isSubmission || isImplemented ? 'text-white/45' : 'text-white/35'}`}>
              <svg className={`w-3 h-3 flex-shrink-0 ${isSubmission ? 'text-sky-300/50' : isImplemented ? 'text-emerald-300/40' : 'text-white/20'}`} fill="currentColor" viewBox="0 0 8 8">
                <circle cx="4" cy="4" r="3" />
              </svg>
              {f}
            </div>
          ))}
        </div>

        <div className="flex flex-wrap gap-2 mb-6">
          {block.stack.map((t) => (
            <span key={t} className={`text-xs px-2.5 py-1 rounded-lg font-medium ${
              isSubmission || isImplemented ? 'bg-white/8 text-white/55' : 'bg-white/4 text-white/25'
            }`}>
              {t}
            </span>
          ))}
        </div>

        {isSubmission && (
          <div className="flex gap-3">
            <Link to="/docs"
              className="flex-1 text-center py-3 text-sm font-bold text-white bg-sky-600 hover:bg-sky-500 rounded-xl transition-all hover:shadow-lg hover:shadow-sky-500/25">
              Submission docs
            </Link>
            <Link to="/contact"
              className="flex-1 text-center py-3 text-sm font-semibold text-white/70 hover:text-white bg-white/8 hover:bg-white/12 rounded-xl transition-all">
              Point of contact
            </Link>
          </div>
        )}

        {isImplemented && (
          <Link to="/contact"
            className="block text-center py-3 text-sm font-semibold text-emerald-300 hover:text-emerald-200 bg-emerald-500/10 hover:bg-emerald-500/15 rounded-xl border border-emerald-500/20 transition-all">
            Ask about the consent track
          </Link>
        )}
      </div>
    </div>
  )
}

export default function Blocks() {
  const inSubmission = BLOCKS.filter((b) => b.status === 'submission')
  const implemented = BLOCKS.filter((b) => b.status === 'implemented')
  const planned = BLOCKS.filter((b) => b.status === 'planned')

  return (
    <div className="bg-[#060d1f] pt-16">
      <section className="relative py-24 px-6 lg:px-8 overflow-hidden border-b border-white/8">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-sky-600/8 rounded-full blur-3xl" />
        <div className="relative max-w-4xl mx-auto text-center">
          <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-4">The platform</p>
          <h1 className="text-5xl md:text-6xl font-black text-white mb-6 tracking-tight">
            Building Blocks
          </h1>
          <p className="text-white/55 text-lg max-w-2xl mx-auto leading-relaxed">
            This page distinguishes between what CivicOS is publicly submitting first, what already exists in the codebase, and what remains on the roadmap. That keeps the site honest and makes the first certification story much easier to defend.
          </p>
          <div className="flex flex-wrap justify-center gap-8 mt-12">
            {[
              { label: 'In submission', value: String(inSubmission.length), color: 'text-sky-300' },
              { label: 'Implemented', value: String(implemented.length), color: 'text-emerald-300' },
              { label: 'On roadmap', value: String(planned.length), color: 'text-white/50' },
            ].map((s) => (
              <div key={s.label} className="text-center">
                <p className={`text-4xl font-black ${s.color}`}>{s.value}</p>
                <p className="text-xs text-white/30 mt-1 uppercase tracking-widest">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-7xl mx-auto">
          <div className="mb-10">
            <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-3">First submission</p>
            <h2 className="text-3xl font-black text-white mb-3">Publicly documented and positioned for review</h2>
            <p className="text-white/50 max-w-2xl leading-relaxed">
              The Content Management System is the cleanest first story for the current repo because the website, documentation, and implementation evidence all point to the same place.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-6">
            {inSubmission.map((block) => <BlockCard key={block.id} block={block} />)}
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-7xl mx-auto">
          <div className="mb-10">
            <p className="text-emerald-300 font-semibold text-sm uppercase tracking-widest mb-3">Implemented modules</p>
            <h2 className="text-3xl font-black text-white mb-3">Present in the platform, not the first claim</h2>
            <p className="text-white/50 max-w-2xl leading-relaxed">
              These modules matter, but the public website now stops short of calling them certified or submission-ready unless the implementation evidence and compliance review say so.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-6">
            {implemented.map((block) => <BlockCard key={block.id} block={block} />)}
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-7xl mx-auto">
          <div className="mb-10">
            <p className="text-white/35 font-semibold text-sm uppercase tracking-widest mb-3">Roadmap</p>
            <h2 className="text-3xl font-black text-white/80 mb-3">Later building blocks</h2>
            <p className="text-white/35 max-w-2xl leading-relaxed">
              Planned modules in the broader CivicOS architecture. They stay visible here without being overstated on the submission-facing homepage.
            </p>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {planned.map((block) => <BlockCard key={block.id} block={block} />)}
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl font-black text-white mb-4">Need the first submission package to read cleanly?</h2>
          <p className="text-white/50 mb-8">Use the CMS documentation page as the public documentation URL and keep the homepage focused on that first building block.</p>
          <div className="flex flex-wrap gap-4 justify-center">
            <Link to="/docs"
              className="px-7 py-3.5 bg-sky-600 hover:bg-sky-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-sky-500/30">
              Open submission docs
            </Link>
            <Link to="/contact"
              className="px-7 py-3.5 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
              Point of contact
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
