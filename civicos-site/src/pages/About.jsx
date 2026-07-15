import { Link } from 'react-router-dom'

const ROADMAP = [
  { status: 'active', label: 'Consent submission package', desc: 'Public website, documentation page, and submission language aligned around the Consent building block implementation already present in the repo.' },
  { status: 'active', label: 'testing.govstack.global entry', desc: 'Use the homepage as the software website and the /docs/consent page as the public documentation URL for the first building block submission.' },
  { status: 'active', label: 'Consent implementation hardening', desc: 'Consent remains the current assessment target while the rest of the platform stays visible as broader context.' },
  { status: 'active', label: 'CMS public platform support', desc: 'The Wagtail content layer remains part of CivicOS even though it is not the active submission target on this site.' },
  { status: 'planned', label: 'Identity building block', desc: 'Citizen identity, verifiable credentials, and federated SSO.' },
  { status: 'planned', label: 'Payments building block', desc: 'Government payment collection and benefits disbursement.' },
  { status: 'planned', label: 'Messaging and scheduler', desc: 'Secure notifications, citizen messaging, and appointment workflows.' },
]

const dotStyle = {
  active: 'bg-sky-300 ring-4 ring-sky-300/20',
  planned: 'bg-white/20 border-2 border-white/20',
}

const VALUES = [
  { icon: '🛡️', title: 'Consent lifecycle', desc: 'Consent records, revisions, signatures, verification flows, and audit history are treated as explicit lifecycle concepts instead of ad hoc flags.' },
  { icon: '🔐', title: 'Data handling discipline', desc: 'Consent sits close to forms, retention settings, export flows, and right-to-be-forgotten behavior rather than being isolated from real service workflows.' },
  { icon: '🌍', title: 'Government context', desc: 'The platform is built for multilingual, public-sector content and service delivery rather than for generic startup marketing sites.' },
  { icon: '🧩', title: 'Modular architecture', desc: 'The website, CMS, forms, and consent work all live in the same Django platform, which makes staged GovStack submissions practical.' },
  { icon: '🔍', title: 'Evidence over slogans', desc: 'This public site now tries to say only what the repo can support with code, models, and documented behavior.' },
  { icon: '📚', title: 'Submission clarity', desc: 'The first public documentation set is focused on one building block so reviewers do not have to untangle mixed claims across multiple domains.' },
]

export default function About() {
  return (
    <div className="bg-[#060d1f] pt-16">
      <section className="relative py-24 px-6 lg:px-8 border-b border-white/8 overflow-hidden">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-sky-600/8 rounded-full blur-3xl" />
        <div className="relative max-w-4xl mx-auto">
          <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-4">About</p>
          <h1 className="text-5xl md:text-6xl font-black text-white mb-6 leading-tight">
            The platform is broader.<br />The first submission is narrower.
          </h1>
          <p className="text-white/55 text-xl max-w-2xl leading-relaxed">
            CivicOS is a modular digital government platform built on Django and Wagtail for governments, municipalities, and communities. This website now leads with the Consent building block because that is the capability currently being documented and submitted first.
          </p>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-4xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-16">
          <div>
            <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-4">Why this positioning</p>
            <h2 className="text-3xl font-black text-white mb-6">Start where the evidence is strongest</h2>
            <p className="text-white/55 leading-relaxed mb-4">
              The CivicOS repo contains more than one product surface, but not every surface should be marketed the same way at the same time. A GovStack assessment works best when the website, documentation, and implementation evidence all tell the same story.
            </p>
            <p className="text-white/55 leading-relaxed mb-4">
              In this submission path, that story is Consent. The codebase already includes GovStack-style consent routes, revision and signature concepts, webhook support, audit surfaces, and consent-aware service integrations.
            </p>
            <p className="text-white/55 leading-relaxed">
              The broader platform vision still matters. It belongs in the roadmap and implementation context, not buried under certification-heavy language. That is the difference between a site that reads like a real platform and one that reads like a temporary submission wrapper.
            </p>
          </div>
          <div>
            <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-4">What is already in the repo</p>
            <div className="space-y-4">
              <div className="p-5 rounded-2xl border border-sky-400/25 bg-sky-950/25">
                <h3 className="text-white font-bold text-sm mb-2 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-sky-300" />
                  Consent API layer
                </h3>
                <p className="text-white/50 text-sm leading-relaxed">
                  Config, service, and audit namespaces make the Consent claim concrete instead of reducing it to generic privacy language.
                </p>
              </div>
              <div className="p-5 rounded-2xl border border-emerald-500/20 bg-emerald-950/20">
                <h3 className="text-white font-bold text-sm mb-2 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  Platform integration
                </h3>
                <p className="text-white/50 text-sm leading-relaxed">
                  Forms, retention settings, consent text, PII flags, and export-related behavior connect the Consent layer to actual service delivery needs.
                </p>
              </div>
            </div>

            <div className="mt-6 p-5 rounded-2xl border border-white/10 bg-white/4">
              <p className="text-xs text-white/30 uppercase tracking-widest font-semibold mb-3">External framing</p>
              {['GovStack Consent building block specification', 'Open standards and interoperability goals', 'Auditability and signature expectations', 'Government data-sharing and approval context'].map((item) => (
                <div key={item} className="flex items-center gap-2 py-1.5">
                  <svg className="w-3.5 h-3.5 text-sky-300 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  <span className="text-sm text-white/55">{item}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-14">
            <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-3">Principles</p>
            <h2 className="text-4xl font-black text-white">How we build</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {VALUES.map((v) => (
              <div key={v.title} className="p-6 rounded-2xl border border-white/10 bg-white/4 hover:bg-white/6 transition-colors">
                <span className="text-3xl block mb-4">{v.icon}</span>
                <h3 className="text-white font-bold mb-2">{v.title}</h3>
                <p className="text-white/50 text-sm leading-relaxed">{v.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-3xl mx-auto">
          <div className="mb-12">
            <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-3">Roadmap</p>
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

      <section className="py-20 px-6 lg:px-8 border-b border-white/8">
        <div className="max-w-4xl mx-auto">
          <div className="rounded-2xl border border-sky-400/20 bg-sky-950/20 p-10">
            <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-4">GovStack context</p>
            <h2 className="text-3xl font-black text-white mb-4">Why the Consent building block fits the current platform</h2>
            <p className="text-white/55 leading-relaxed mb-4">
              GovStack's Consent building block is about enabling individuals to approve the use of personal data through an auditable and interoperable consent lifecycle. CivicOS fits that need as a platform for public institutions, municipalities, and communities rather than as a generic privacy banner.
            </p>
            <p className="text-white/55 leading-relaxed mb-6">
              The Consent framing lets this site show actual code-backed implementation details: config, service, and audit routes, consent records, signatures, revisions, verification flows, and right-to-be-forgotten handling. Those are the ingredients reviewers can verify without guessing.
            </p>
            <div className="flex flex-wrap gap-4">
              <a href="https://govstack.gitbook.io/bb-consent/con-23q4/2-description" target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm font-semibold text-sky-300 hover:text-sky-200 transition-colors">
                Read the Consent specification
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                </svg>
              </a>
              <Link to="/contact" className="inline-flex items-center gap-2 text-sm font-semibold text-white/50 hover:text-white transition-colors">
                Point of contact →
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl font-black text-white mb-4">Need a cleaner first submission?</h2>
          <p className="text-white/50 mb-8">Lead with the Consent documentation now, then expand the public story as other building blocks are ready to stand up to review.</p>
          <div className="flex flex-wrap gap-4 justify-center">
            <Link to="/contact"
              className="px-7 py-3.5 bg-sky-600 hover:bg-sky-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-sky-500/30">
              Point of contact
            </Link>
            <Link to="/docs/consent"
              className="px-7 py-3.5 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
              Consent docs
            </Link>
            <Link to="/docs"
              className="px-7 py-3.5 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
              Docs hub
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
