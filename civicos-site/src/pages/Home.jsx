import { Link } from 'react-router-dom'

const BLOCKS = [
  {
    id: 'consent',
    name: 'Consent BB',
    tagline: 'Citizen consent management',
    description: 'Full lifecycle consent — policy management, data agreements, cryptographic signatures, right to be forgotten, real-time webhooks, and tamper-proof audit trail.',
    status: 'certified',
    statusLabel: 'GovStack Certified',
    endpoints: 42,
    tests: 234,
    icon: (
      <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.955 11.955 0 003 12.034C3 16.976 6.58 21.152 11.25 22v-.03a8.75 8.75 0 10.5-17.5v.008l-.75-.508z" />
      </svg>
    ),
  },
  {
    id: 'cms',
    name: 'CMS BB',
    tagline: 'Content & citizen portal',
    description: 'A full-featured government content management system and citizen portal. Built for municipalities and public sector organizations of any size.',
    status: 'development',
    statusLabel: 'In Development',
    icon: (
      <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 01-2.25 2.25M16.5 7.5V18a2.25 2.25 0 002.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 002.25 2.25h13.5M6 7.5h3v3H6v-3z" />
      </svg>
    ),
  },
  {
    id: 'identity',
    name: 'Identity BB',
    tagline: 'Digital identity & authentication',
    description: 'Citizen identity verification, credential management, and single sign-on integration aligned with GovStack Identity BB specification.',
    status: 'planned',
    statusLabel: 'Planned',
    icon: (
      <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
      </svg>
    ),
  },
  {
    id: 'payments',
    name: 'Payments BB',
    tagline: 'Government payments & disbursements',
    description: 'Secure payment collection, benefits disbursement, and financial reconciliation aligned with the GovStack Payments BB specification.',
    status: 'planned',
    statusLabel: 'Planned',
    icon: (
      <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 004.5 19.5z" />
      </svg>
    ),
  },
]

const statusStyle = {
  certified:   { bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', text: 'text-emerald-400', dot: 'bg-emerald-400' },
  development: { bg: 'bg-blue-500/10',    border: 'border-blue-500/30',    text: 'text-blue-400',   dot: 'bg-blue-400' },
  planned:     { bg: 'bg-white/5',        border: 'border-white/15',       text: 'text-white/40',   dot: 'bg-white/30' },
}

function BlockCard({ block, featured }) {
  const s = statusStyle[block.status]
  return (
    <div className={`relative group rounded-2xl border transition-all duration-300 overflow-hidden
      ${featured
        ? 'border-blue-500/40 bg-gradient-to-br from-blue-950/60 via-[#0a1628] to-[#0a1628] hover:border-blue-400/60'
        : 'border-white/10 bg-[#0a1628]/60 hover:border-white/25'
      } hover:shadow-2xl hover:shadow-black/40 hover:-translate-y-1`}>

      {featured && (
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-blue-500/60 to-transparent" />
        </div>
      )}

      <div className="p-7">
        {/* Icon + status */}
        <div className="flex items-start justify-between mb-5">
          <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${
            featured ? 'bg-blue-500/20 text-blue-400' : 'bg-white/8 text-white/60'
          }`}>
            {block.icon}
          </div>
          <span className={`inline-flex items-center gap-1.5 text-xs font-semibold rounded-full px-2.5 py-1 border ${s.bg} ${s.border} ${s.text}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${s.dot} ${block.status === 'certified' ? 'animate-pulse' : ''}`} />
            {block.statusLabel}
          </span>
        </div>

        <h3 className="text-white font-bold text-xl mb-1">{block.name}</h3>
        <p className={`text-sm font-medium mb-3 ${featured ? 'text-blue-300' : 'text-white/40'}`}>{block.tagline}</p>
        <p className="text-white/55 text-sm leading-relaxed">{block.description}</p>

        {/* Stats for certified */}
        {block.status === 'certified' && (
          <div className="mt-5 pt-5 border-t border-white/10 grid grid-cols-2 gap-4">
            <div>
              <p className="text-2xl font-bold text-white">{block.endpoints}</p>
              <p className="text-xs text-white/40 mt-0.5">API endpoints</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-white">{block.tests}</p>
              <p className="text-xs text-white/40 mt-0.5">Automated tests</p>
            </div>
          </div>
        )}

        {block.status === 'certified' && (
          <div className="mt-5 flex gap-3">
            <Link to="/docs"
              className="flex-1 text-center py-2.5 text-sm font-semibold text-white bg-blue-600 hover:bg-blue-500 rounded-xl transition-all hover:shadow-lg hover:shadow-blue-500/25">
              View API Docs
            </Link>
            <Link to="/building-blocks"
              className="flex-1 text-center py-2.5 text-sm font-semibold text-white/70 hover:text-white bg-white/8 hover:bg-white/12 rounded-xl transition-all">
              Learn more
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}

export default function Home() {
  return (
    <div className="bg-[#060d1f]">
      {/* ── HERO ─────────────────────────────────────── */}
      <section className="relative min-h-screen flex items-center pt-16 overflow-hidden">
        {/* Background effects */}
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-[800px] h-[500px] bg-blue-600/8 rounded-full blur-3xl" />
        <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />

        {/* Grid overlay */}
        <div className="absolute inset-0 opacity-[0.03]" style={{
          backgroundImage: 'linear-gradient(rgba(255,255,255,.5) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.5) 1px,transparent 1px)',
          backgroundSize: '60px 60px'
        }} />

        <div className="relative max-w-7xl mx-auto px-6 lg:px-8 py-24">
          <div className="max-w-4xl">
            {/* Badge */}
            <div className="inline-flex items-center gap-2 mb-8 px-4 py-2 rounded-full border border-white/12 bg-white/5 backdrop-blur-sm">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-xs font-semibold text-white/70 tracking-wide uppercase">GovStack v23Q4 Certified</span>
            </div>

            {/* Headline */}
            <h1 className="text-5xl md:text-6xl lg:text-7xl font-black text-white leading-[1.08] tracking-tight mb-6">
              Government software{' '}
              <span className="text-gradient">built to the global standard</span>
            </h1>

            <p className="text-xl text-white/60 leading-relaxed max-w-2xl mb-10">
              CivicOS delivers certified, interoperable building blocks for digital government — aligned with the GovStack international specification so your systems work anywhere, with anyone.
            </p>

            {/* CTAs */}
            <div className="flex flex-wrap gap-4">
              <Link to="/building-blocks"
                className="inline-flex items-center gap-2 px-7 py-3.5 bg-blue-600 hover:bg-blue-500 text-white font-semibold rounded-xl transition-all hover:shadow-xl hover:shadow-blue-500/30 active:scale-95">
                Explore Building Blocks
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </Link>
              <Link to="/contact"
                className="inline-flex items-center gap-2 px-7 py-3.5 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
                Contact Us
              </Link>
            </div>

            {/* Trust strip */}
            <div className="mt-16 flex flex-wrap items-center gap-6">
              <span className="text-xs font-semibold text-white/30 uppercase tracking-widest">Aligned with</span>
              {['GovStack Global', 'ITU Standards', 'WCAG 2.1 AA', 'Privacy by Design'].map(t => (
                <span key={t} className="text-sm text-white/50 font-medium border-l border-white/15 pl-6 first-of-type:border-0 first-of-type:pl-0">
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── BUILDING BLOCKS ───────────────────────────── */}
      <section className="py-28 px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-3">The Platform</p>
            <h2 className="text-4xl md:text-5xl font-black text-white mb-5">
              One standard. Every function.
            </h2>
            <p className="text-white/50 text-lg max-w-2xl mx-auto leading-relaxed">
              Each building block is independently deployable, GovStack-certified, and designed to interoperate with any other compliant system worldwide.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {BLOCKS.map((b, i) => (
              <BlockCard key={b.id} block={b} featured={i === 0} />
            ))}
          </div>

          <div className="mt-10 text-center">
            <Link to="/building-blocks"
              className="inline-flex items-center gap-2 text-white/50 hover:text-white text-sm font-medium transition-colors">
              View full building blocks roadmap
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
              </svg>
            </Link>
          </div>
        </div>
      </section>

      {/* ── WHY CIVICOS ───────────────────────────────── */}
      <section className="py-24 px-6 lg:px-8 border-t border-white/8">
        <div className="max-w-7xl mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
            <div>
              <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-3">Why CivicOS</p>
              <h2 className="text-4xl font-black text-white mb-6 leading-tight">
                Built open.<br />Built to last.
              </h2>
              <p className="text-white/55 text-lg leading-relaxed mb-8">
                Governments shouldn't rebuild the same software over and over. CivicOS gives you a certified foundation you can deploy today and build on for years — without vendor lock-in.
              </p>
              <Link to="/about"
                className="inline-flex items-center gap-2 text-blue-400 hover:text-blue-300 font-semibold text-sm transition-colors">
                Our story
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </Link>
            </div>

            <div className="grid grid-cols-1 gap-4">
              {[
                { icon: '🔒', title: 'Security First', desc: 'Every endpoint authenticated. Audit entries immutable. HMAC-signed webhooks. Secrets never leave the server.' },
                { icon: '🌐', title: 'Interoperable by Standard', desc: 'Strict GovStack spec adherence means any two compliant systems compose without custom integration.' },
                { icon: '♿', title: 'Accessible by Default', desc: 'WCAG 2.1 AA is a requirement. Government software must work for every citizen.' },
                { icon: '📖', title: 'Open Source', desc: 'Full source code published openly. Fork it, audit it, deploy it. No vendor lock-in, ever.' },
              ].map(f => (
                <div key={f.title} className="flex gap-4 p-5 rounded-2xl bg-white/4 border border-white/8 hover:bg-white/6 transition-colors">
                  <span className="text-2xl flex-shrink-0">{f.icon}</span>
                  <div>
                    <h3 className="text-white font-bold text-sm mb-1">{f.title}</h3>
                    <p className="text-white/50 text-sm leading-relaxed">{f.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── CTA BAND ──────────────────────────────────── */}
      <section className="py-24 px-6 lg:px-8">
        <div className="max-w-4xl mx-auto text-center">
          <div className="relative rounded-3xl overflow-hidden border border-white/10 bg-gradient-to-br from-blue-950/80 via-[#0a1628] to-indigo-950/80 p-16">
            <div className="absolute inset-0 opacity-30" style={{
              backgroundImage: 'radial-gradient(ellipse 80% 60% at 50% 0%, rgba(37,99,235,0.35) 0%, transparent 70%)'
            }} />
            <div className="relative">
              <h2 className="text-4xl md:text-5xl font-black text-white mb-5">
                Ready to modernize<br />your government services?
              </h2>
              <p className="text-white/55 text-lg mb-10 max-w-xl mx-auto leading-relaxed">
                Deploy the Consent Building Block today. More blocks launching soon. Let's talk about what your organization needs.
              </p>
              <div className="flex flex-wrap gap-4 justify-center">
                <Link to="/contact"
                  className="px-8 py-4 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-blue-500/30">
                  Get in Touch
                </Link>
                <Link to="/docs"
                  className="px-8 py-4 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
                  Read the API Docs
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
