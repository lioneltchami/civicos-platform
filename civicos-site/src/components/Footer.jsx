import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="bg-[#060d1f] border-t border-white/8">
      <div className="max-w-7xl mx-auto px-6 lg:px-8 py-16">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-10">
          <div className="md:col-span-2">
            <img
              src="/civicos-logo-wordmark.svg"
              alt="CivicOS"
              className="mb-4 h-12 w-auto"
            />
            <p className="text-white/50 text-sm leading-relaxed max-w-sm">
              Django 5.2 and Wagtail-based public-sector platform for governments, municipalities, and communities. Current standards focus: the GovStack Consent building block.
            </p>
            <div className="mt-5 flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-300 bg-sky-400/10 border border-sky-400/20 rounded-full px-3 py-1">
                <span className="w-1.5 h-1.5 rounded-full bg-sky-300 animate-pulse"></span>
                Consent assessment in preparation
              </span>
            </div>
          </div>

          <div>
            <h4 className="text-white font-semibold text-sm mb-4">Submission</h4>
            <ul className="space-y-3">
              {[
                { to: '/docs', label: 'Docs hub (/docs)' },
                { to: '/docs/consent', label: 'Consent docs (/docs/consent)' },
                { to: '/building-blocks', label: 'Building block roadmap' },
                { to: '/contact', label: 'Point of contact' },
              ].map((l) => (
                <li key={l.label}>
                  <Link to={l.to} className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</Link>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h4 className="text-white font-semibold text-sm mb-4">References</h4>
            <ul className="space-y-3">
              {[
                { to: '/about', label: 'About' },
                { href: 'https://govstack.gitbook.io/bb-consent/con-23q4/2-description', label: 'GovStack Consent description' },
                { href: 'https://testing.govstack.global', label: 'testing.govstack.global' },
                { href: 'https://govstack.global', label: 'GovStack Global' },
              ].map((l) => (
                <li key={l.label}>
                  {l.href
                    ? <a href={l.href} target="_blank" rel="noopener noreferrer" className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</a>
                    : <Link to={l.to} className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</Link>}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="mt-12 pt-8 border-t border-white/8 flex flex-col md:flex-row items-center justify-between gap-4">
          <p className="text-white/30 text-sm">© {new Date().getFullYear()} CivicOS. All rights reserved.</p>
          <p className="text-white/30 text-sm">
            Documentation aligned for <a href="https://testing.govstack.global" target="_blank" rel="noopener noreferrer" className="text-white/50 hover:text-white transition-colors">testing.govstack.global</a>
          </p>
        </div>
      </div>
    </footer>
  )
}
