import { Link } from 'react-router-dom'

const BLOCKS = [
  {
    id: 'consent',
    name: 'Consent BB',
    version: 'v23Q4',
    tagline: 'Citizen consent management',
    description: 'Full lifecycle consent management aligned with GovStack Consent BB v23Q4. Covers policy management, citizen data agreements, cryptographic signatures, right to be forgotten, real-time webhook notifications, and a tamper-proof SHA-256 hash-chained audit trail.',
    status: 'certified',
    statusLabel: 'GovStack Certified',
    endpoints: 42,
    tests: 234,
    stack: ['Django 5.2', 'Wagtail', 'PostgreSQL', 'OpenAPI 3.0'],
    namespaces: [
      { name: '/config/', ops: 14, desc: 'Policy & data agreement management' },
      { name: '/service/', ops: 21, desc: 'Citizen consent records & RTBF' },
      { name: '/audit/', ops: 7, desc: 'Tamper-proof audit trail' },
    ],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.955 11.955 0 003 12.034C3 16.976 6.58 21.152 11.25 22v-.03a8.75 8.75 0 10.5-17.5v.008l-.75-.508z" />
      </svg>
    ),
  },
  {
    id: 'cms',
    name: 'CMS BB',
    version: 'Coming soon',
    tagline: 'Content management & citizen portal',
    description: 'A full-featured government content management system and citizen-facing portal. Designed for municipalities and public agencies that need accessible, multi-language content publishing with role-based editorial workflows.',
    status: 'development',
    statusLabel: 'In Development',
    stack: ['Django 5.2', 'Wagtail', 'PostgreSQL', 'Elasticsearch'],
    features: ['Multi-language content', 'Role-based editorial workflow', 'Accessible UI (WCAG 2.1 AA)', 'GovStack CMS BB alignment'],
    icon: (
      <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.7}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 01-2.25 2.25M16.5 7.5V18a2.25 2.25 0 002.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 002.25 2.25h13.5M6 7.5h3v3H6v-3z" />
      </svg>
    ),
  },
  {
    id: 'identity',
    name: 'Identity BB',
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
    name: 'Payments BB',
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
    name: 'Messaging BB',
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
    name: 'Scheduler BB',
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
  certified:   { bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', text: 'text-emerald-400', dot: 'bg-emerald-400', ring: 'ring-emerald-500/20' },
  development: { bg: 'bg-blue-500/10',    border: 'border-blue-500/30',    text: 'text-blue-400',   dot: 'bg-blue-400',   ring: 'ring-blue-500/20' },
  planned:     { bg: 'bg-white/5',        border: 'border-white/15',       text: 'text-white/35',   dot: 'bg-white/25',   ring: 'ring-white/10' },
}

function BlockCard({ block }) {
  const s = statusStyle[block.status]
  const isCertified = block.status === 'certified'
  const isDev = block.status === 'development'

  return (
    <div className={`relative rounded-2xl border transition-all duration-300 overflow-hidden
      ${isCertified
        ? 'border-blue-500/40 bg-gradient-to-br from-blue-950/50 via-[#0a1628] to-[#0a1628]'
        : 'border-white/10 bg-[#0a1628]/70'
      }`}>
      {isCertified && (
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-blue-500/60 to-transparent" />
      )}

      <div className="p-8">
        <div className="flex items-start justify-between mb-6">
          <div className={`w-14 h-14 rounded-2xl flex items-center justify-center
            ${isCertified ? 'bg-blue-500/15 text-blue-400' : isDev ? 'bg-white/8 text-white/60' : 'bg-white/5 text-white/30'}`}>
            {block.icon}
          </div>
          <div className="text-right">
            <span className={`inline-flex items-center gap-1.5 text-xs font-semibold rounded-full px-2.5 py-1 border ${s.bg} ${s.border} ${s.text}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${s.dot} ${isCertified ? 'animate-pulse' : ''}`} />
              {block.statusLabel}
            </span>
            <p className="text-xs text-white/25 mt-1.5">{block.version}</p>
          </div>
        </div>

        <h3 className={`text-xl font-black mb-1 ${isCertified ? 'text-white' : isDev ? 'text-white/80' : 'text-white/45'}`}>
          {block.name}
        </h3>
        <p className={`text-sm font-medium mb-3 ${isCertified ? 'text-blue-300' : 'text-white/35'}`}>
          {block.tagline}
        </p>
        <p className={`text-sm leading-relaxed mb-6 ${isCertified ? 'text-white/60' : 'text-white/35'}`}>
          {block.description}
        </p>

        {/* Certified: namespace breakdown */}
        {block.namespaces && (
          <div className="space-y-2 mb-6">
            {block.namespaces.map(ns => (
              <div key={ns.name} className="flex items-center gap-3 p-3 rounded-xl bg-white/5 border border-white/8">
                <code className="text-xs font-mono text-blue-300 w-24 flex-shrink-0">{ns.name}</code>
                <div className="flex items-center gap-2 flex-1">
                  <span className="text-white/40 text-xs">{ns.desc}</span>
                </div>
                <span className="text-xs font-bold text-white/50 flex-shrink-0">{ns.ops} ops</span>
              </div>
            ))}
          </div>
        )}

        {/* Dev / planned: feature list */}
        {block.features && (
          <div className="grid grid-cols-2 gap-2 mb-6">
            {block.features.map(f => (
              <div key={f} className="flex items-center gap-2 text-xs text-white/35">
                <svg className="w-3 h-3 text-white/20 flex-shrink-0" fill="currentColor" viewBox="0 0 8 8">
                  <circle cx="4" cy="4" r="3" />
                </svg>
                {f}
              </div>
            ))}
          </div>
        )}

        {/* Stack tags */}
        <div className="flex flex-wrap gap-2 mb-6">
          {block.stack.map(t => (
            <span key={t} className={`text-xs px-2.5 py-1 rounded-lg font-medium
              ${isCertified ? 'bg-white/8 text-white/55' : 'bg-white/4 text-white/25'}`}>
              {t}
            </span>
          ))}
        </div>

        {/* Stats row for certified */}
        {isCertified && (
          <div className="grid grid-cols-2 gap-4 pt-5 border-t border-white/10 mb-6">
            <div>
              <p className="text-3xl font-black text-white">{block.endpoints}</p>
              <p className="text-xs text-white/40 mt-0.5">API endpoints</p>
            </div>
            <div>
              <p className="text-3xl font-black text-white">{block.tests}</p>
              <p className="text-xs text-white/40 mt-0.5">Tests passing</p>
            </div>
          </div>
        )}

        {isCertified && (
          <div className="flex gap-3">
            <Link to="/docs"
              className="flex-1 text-center py-3 text-sm font-bold text-white bg-blue-600 hover:bg-blue-500 rounded-xl transition-all hover:shadow-lg hover:shadow-blue-500/25">
              API Documentation →
            </Link>
            <Link to="/contact"
              className="flex-1 text-center py-3 text-sm font-semibold text-white/70 hover:text-white bg-white/8 hover:bg-white/12 rounded-xl transition-all">
              Get in touch
            </Link>
          </div>
        )}

        {isDev && (
          <Link to="/contact"
            className="block text-center py-3 text-sm font-semibold text-blue-400 hover:text-blue-300 bg-blue-500/10 hover:bg-blue-500/15 rounded-xl border border-blue-500/20 transition-all">
            Notify me when it's ready
          </Link>
        )}
      </div>
    </div>
  )
}

export default function Blocks() {
  const certified = BLOCKS.filter(b => b.status === 'certified')
  const inDev = BLOCKS.filter(b => b.status === 'development')
  const planned = BLOCKS.filter(b => b.status === 'planned')

  return (
    <div className="bg-[#060d1f] pt-16">
      {/* Header */}
      <section className="relative py-24 px-6 lg:px-8 overflow-hidden border-b border-white/8">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-blue-600/8 rounded-full blur-3xl" />
        <div className="relative max-w-4xl mx-auto text-center">
          <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-4">The Platform</p>
          <h1 className="text-5xl md:text-6xl font-black text-white mb-6 tracking-tight">
            Building Blocks
          </h1>
          <p className="text-white/55 text-lg max-w-2xl mx-auto leading-relaxed">
            Independently deployable, GovStack-certified modules for digital government. Each block implements an official GovStack specification and interoperates with any other compliant system.
          </p>
          <div className="flex flex-wrap justify-center gap-8 mt-12">
            {[
              { label: 'Certified', value: '1', color: 'text-emerald-400' },
              { label: 'In Development', value: '1', color: 'text-blue-400' },
              { label: 'On Roadmap', value: '4', color: 'text-white/50' },
            ].map(s => (
              <div key={s.label} className="text-center">
                <p className={`text-4xl font-black ${s.color}`}>{s.value}</p>
                <p className="text-xs text-white/30 mt-1 uppercase tracking-widest">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="max-w-7xl mx-auto px-6 lg:px-8">
        {/* Certified */}
        <section className="py-16">
          <div className="flex items-center gap-3 mb-8">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
            <h2 className="text-sm font-bold text-emerald-400 uppercase tracking-widest">Certified & Deployed</h2>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-1 gap-6">
            {certified.map(b => <BlockCard key={b.id} block={b} />)}
          </div>
        </section>

        {/* In Development */}
        <section className="py-8 border-t border-white/8">
          <div className="flex items-center gap-3 mb-8">
            <span className="w-2.5 h-2.5 rounded-full bg-blue-400" />
            <h2 className="text-sm font-bold text-blue-400 uppercase tracking-widest">In Development</h2>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {inDev.map(b => <BlockCard key={b.id} block={b} />)}
          </div>
        </section>

        {/* Planned */}
        <section className="py-8 border-t border-white/8 pb-24">
          <div className="flex items-center gap-3 mb-8">
            <span className="w-2.5 h-2.5 rounded-full bg-white/25" />
            <h2 className="text-sm font-bold text-white/35 uppercase tracking-widest">Roadmap</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 gap-6">
            {planned.map(b => <BlockCard key={b.id} block={b} />)}
          </div>
        </section>
      </div>

      {/* CTA */}
      <section className="border-t border-white/8 py-24 px-6 lg:px-8">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl font-black text-white mb-4">Missing a building block?</h2>
          <p className="text-white/50 mb-8 text-lg">If there's a GovStack BB you need, reach out. We prioritize based on real-world demand from governments and municipalities.</p>
          <Link to="/contact"
            className="inline-flex items-center gap-2 px-7 py-3.5 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-blue-500/30">
            Request a Building Block
          </Link>
        </div>
      </section>
    </div>
  )
}
