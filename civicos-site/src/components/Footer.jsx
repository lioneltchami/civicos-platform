import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="bg-[#060d1f] border-t border-white/8">
      <div className="max-w-7xl mx-auto px-6 lg:px-8 py-16">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-10">
          {/* Brand */}
          <div className="md:col-span-2">
            <div className="flex items-center gap-2.5 mb-4">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center">
                <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
                </svg>
              </div>
              <span className="font-bold text-white text-[17px]">Civic<span className="text-blue-400">OS</span></span>
            </div>
            <p className="text-white/50 text-sm leading-relaxed max-w-xs">
              Open government building blocks built to the GovStack international standard. Certified, interoperable, production-ready.
            </p>
            <div className="mt-5 flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-400 bg-emerald-400/10 border border-emerald-400/20 rounded-full px-3 py-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                GovStack Certified
              </span>
            </div>
          </div>

          {/* Platform */}
          <div>
            <h4 className="text-white font-semibold text-sm mb-4">Platform</h4>
            <ul className="space-y-3">
              {[
                { to: '/building-blocks', label: 'Building Blocks' },
                { to: '/building-blocks', label: 'Consent BB' },
                { to: '/docs', label: 'API Docs' },
              ].map(l => (
                <li key={l.label}>
                  {l.href
                    ? <a href={l.href} target="_blank" rel="noopener noreferrer" className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</a>
                    : <Link to={l.to} className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</Link>
                  }
                </li>
              ))}
            </ul>
          </div>

          {/* Company */}
          <div>
            <h4 className="text-white font-semibold text-sm mb-4">Company</h4>
            <ul className="space-y-3">
              {[
                { to: '/about', label: 'About' },
                { to: '/contact', label: 'Contact' },
                { href: 'https://govstack.global', label: 'GovStack Global' },
              ].map(l => (
                <li key={l.label}>
                  {l.href
                    ? <a href={l.href} target="_blank" rel="noopener noreferrer" className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</a>
                    : <Link to={l.to} className="text-white/50 hover:text-white text-sm transition-colors">{l.label}</Link>
                  }
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="mt-12 pt-8 border-t border-white/8 flex flex-col md:flex-row items-center justify-between gap-4">
          <p className="text-white/30 text-sm">© {new Date().getFullYear()} CivicOS. All rights reserved.</p>
          <p className="text-white/30 text-sm">
            Built for <a href="https://govstack.global" target="_blank" rel="noopener noreferrer" className="text-white/50 hover:text-white transition-colors">GovStack</a>
          </p>
        </div>
      </div>
    </footer>
  )
}
