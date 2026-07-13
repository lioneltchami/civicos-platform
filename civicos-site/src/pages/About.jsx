import { Link } from 'react-router-dom'

const ROADMAP = [
  { status: 'done',    label: 'Consent BB v23Q4', desc: '42 endpoints, 234 tests, GovStack certification in progress.' },
  { status: 'active',  label: 'GovStack Certification', desc: 'Formal submission to testing.govstack.global for official certification badge.' },
  { status: 'active',  label: 'CMS Building Block', desc: 'Government CMS and citizen portal built on Wagtail — targeting municipal clients.' },
  { status: 'planned', label: 'Identity Building Block', desc: 'Citizen identity, verifiable credentials, federated SSO.' },
  { status: 'planned', label: 'Payments Building Block', desc: 'Government payment collection and benefits disbursement.' },
  { status: 'planned', label: 'Messaging Building Block', desc: 'Secure government-to-citizen multi-channel messaging.' },
]

const dotStyle = {
  done:    'bg-emerald-400',
  active:  'bg-blue-400 ring-4 ring-blue-400/20',
  planned: 'bg-white/20 border-2 border-white/20',
}

const VALUES = [
  { icon: '🔒', title: 'Security First', desc: 'Every endpoint authenticated. Audit entries immutable. HMAC-signed webhooks. Secrets never leave the server.' },
  { icon: '♿', title: 'Accessible by Default', desc: 'WCAG 2.1 AA is a hard requirement. Government software must work for every citizen.' },
  { icon: '🔍', title: 'Auditable', desc: 'Every action produces a tamper-proof audit entry, hash-chained with SHA-256.' },
  { icon: '🧩', title: 'Interoperable', desc: 'Strict GovStack spec adherence means our BBs compose with any other compliant system worldwide.' },
  { icon: '🛡️', title: 'Privacy by Design', desc: 'Data minimization, consent-first architecture, and RTBF are baked in — not bolted on.' },
  { icon: '📖', title: 'Open Source', desc: 'Full source published openly. Fork it, audit it, deploy it. No vendor lock-in, ever.' },
]

export default function About() {
  return (
    <div className="bg-[#060d1f] pt-16">
      {/* Header */}
      <section className="relative py-24 px-6 lg:px-8 border-b border-white/8 overflow-hidden">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-blue-600/8 rounded-full blur-3xl" />
        <div className="relative max-w-4xl mx-auto">
          <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-4">About</p>
          <h1 className="text-5xl md:text-6xl font-black text-white mb-6 leading-tight">
            Government software<br />should be open
          </h1>
          <p className="text-white/55 text-xl max-w-2xl leading-relaxed">
            CivicOS builds production-grade digital government infrastructure — certified to the GovStack international standard, deployable anywhere, owned by no one.
          </p>
        </div>
      </section>

      {/* Mission */}
      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-4xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-16">
          <div>
            <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-4">Mission</p>
            <h2 className="text-3xl font-black text-white mb-6">One standard. Every function. Every country.</h2>
            <p className="text-white/55 leading-relaxed mb-4">
              Governments around the world are rebuilding the same software over and over — consent management, identity, payments, messaging — each time from scratch, each time with a different vendor, each time at enormous cost and risk.
            </p>
            <p className="text-white/55 leading-relaxed mb-4">
              CivicOS changes that. We build each function once, to the highest standard of security, accessibility, and auditability — then publish it openly so any team anywhere can deploy it, audit it, and build on it.
            </p>
            <p className="text-white/55 leading-relaxed">
              Our building blocks implement the <a href="https://govstack.global" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:text-blue-300 font-medium transition-colors">GovStack international specification</a> — a standard backed by ITU, DIAL, GIZ, and the Estonian government. That means any two GovStack-compliant systems can compose without custom integration, today or ten years from now.
            </p>
          </div>
          <div>
            <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-4">Two Tracks</p>
            <div className="space-y-4">
              <div className="p-5 rounded-2xl border border-blue-500/30 bg-blue-950/30">
                <h3 className="text-white font-bold text-sm mb-2 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-blue-400" />
                  Track 1 — Open Building Blocks
                </h3>
                <p className="text-white/50 text-sm leading-relaxed">
                  GovStack-certified, open source, interoperable. Any government or agency can deploy these blocks independently. Free forever.
                </p>
              </div>
              <div className="p-5 rounded-2xl border border-emerald-500/20 bg-emerald-950/20">
                <h3 className="text-white font-bold text-sm mb-2 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  Track 2 — CivicOS CMS
                </h3>
                <p className="text-white/50 text-sm leading-relaxed">
                  A commercial government CMS and citizen portal built for municipalities. Full-service, supported, and ready to deploy for any city or agency.
                </p>
              </div>
            </div>

            <div className="mt-6 p-5 rounded-2xl border border-white/10 bg-white/4">
              <p className="text-xs text-white/30 uppercase tracking-widest font-semibold mb-3">Aligned with</p>
              {['GovStack Global (ITU / DIAL / GIZ)', 'Estonian Government Digital Standards', 'WCAG 2.1 AA Accessibility', 'Privacy by Design Principles'].map(item => (
                <div key={item} className="flex items-center gap-2 py-1.5">
                  <svg className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  <span className="text-sm text-white/55">{item}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Values */}
      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-14">
            <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-3">Principles</p>
            <h2 className="text-4xl font-black text-white">How we build</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {VALUES.map(v => (
              <div key={v.title} className="p-6 rounded-2xl border border-white/10 bg-white/4 hover:bg-white/6 transition-colors">
                <span className="text-3xl block mb-4">{v.icon}</span>
                <h3 className="text-white font-bold mb-2">{v.title}</h3>
                <p className="text-white/50 text-sm leading-relaxed">{v.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Roadmap */}
      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-3xl mx-auto">
          <div className="mb-12">
            <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-3">Roadmap</p>
            <h2 className="text-4xl font-black text-white">What's next</h2>
          </div>
          <div className="relative">
            <div className="absolute left-[5px] top-2 bottom-2 w-px bg-white/10" />
            <div className="space-y-6">
              {ROADMAP.map((item, i) => (
                <div key={i} className="flex gap-6">
                  <div className="flex-shrink-0 mt-1">
                    <div className={`w-3 h-3 rounded-full ${dotStyle[item.status]}`} />
                  </div>
                  <div>
                    <h3 className={`font-bold text-sm mb-1 ${item.status === 'planned' ? 'text-white/40' : 'text-white'}`}>
                      {item.label}
                      {item.status === 'done' && <span className="ml-2 text-emerald-400">✓</span>}
                    </h3>
                    <p className={`text-sm leading-relaxed ${item.status === 'planned' ? 'text-white/25' : 'text-white/50'}`}>
                      {item.desc}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* GovStack */}
      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-4xl mx-auto">
          <div className="rounded-2xl border border-blue-500/25 bg-blue-950/30 p-10">
            <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-4">The Standard</p>
            <h2 className="text-3xl font-black text-white mb-4">What is GovStack?</h2>
            <p className="text-white/55 leading-relaxed mb-4">
              GovStack is an initiative by ITU, DIAL, GIZ, and the Estonian government to define a set of reusable, interoperable digital government "building blocks" — standardized software components covering the most common functions governments need.
            </p>
            <p className="text-white/55 leading-relaxed mb-6">
              Each building block is defined by an open API specification. Any software that implements the spec is interchangeable with any other compliant implementation — so governments can switch providers without rewriting citizen-facing systems.
            </p>
            <div className="flex flex-wrap gap-4">
              <a href="https://govstack.global" target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm font-semibold text-blue-400 hover:text-blue-300 transition-colors">
                govstack.global
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                </svg>
              </a>
              <Link to="/contact" className="inline-flex items-center gap-2 text-sm font-semibold text-white/50 hover:text-white transition-colors">
                Work with us →
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6 lg:px-8">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl font-black text-white mb-4">Ready to build on CivicOS?</h2>
          <p className="text-white/50 mb-8">Deploy the Consent BB today, or talk to us about what your organization needs.</p>
          <div className="flex flex-wrap gap-4 justify-center">
            <Link to="/contact"
              className="px-7 py-3.5 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-blue-500/30">
              Get in Touch
            </Link>
            <Link to="/building-blocks"
              className="px-7 py-3.5 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
              Explore Building Blocks
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
