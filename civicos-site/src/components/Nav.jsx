import { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'

export default function Nav() {
  const [scrolled, setScrolled] = useState(false)
  const [open, setOpen] = useState(false)
  const loc = useLocation()

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 20)
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])

  useEffect(() => setOpen(false), [loc])

  const links = [
    { to: '/docs', label: 'Docs' },
    { to: '/building-blocks', label: 'Roadmap' },
    { to: '/about', label: 'About' },
  ]

  const isActive = (to) => {
    if (to === '/docs') {
      return loc.pathname === '/docs' || loc.pathname.startsWith('/docs/')
    }
    return loc.pathname === to
  }

  return (
    <nav
      style={{ backgroundColor: scrolled ? 'rgba(6,13,31,0.95)' : '#060d1f' }}
      className={`fixed top-0 w-full z-50 transition-all duration-300 ${
        scrolled ? 'backdrop-blur-md border-b border-white/10 shadow-xl' : ''
      }`}
      aria-label="Main navigation"
    >
      <div className="max-w-7xl mx-auto px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <Link to="/" className="flex items-center gap-3" aria-label="CivicOS home">
            <img
              src="/civicos-logo-wordmark.svg"
              alt="CivicOS"
              className="hidden h-11 w-auto md:block lg:h-12"
            />
            <img
              src="/civicos-mark-square.svg"
              alt="CivicOS"
              className="h-10 w-10 rounded-lg md:hidden"
            />
          </Link>

          <div className="hidden md:flex items-center gap-1" role="list">
            {links.map((l) => (
              <Link
                key={l.to}
                to={l.to}
                role="listitem"
                aria-current={isActive(l.to) ? 'page' : undefined}
                className={`px-4 py-2 text-sm font-medium transition-colors rounded-lg ${
                  isActive(l.to) ? 'text-white bg-white/10' : 'text-white/70 hover:text-white hover:bg-white/8'
                }`}
              >
                {l.label}
              </Link>
            ))}
          </div>

          <div className="hidden md:flex items-center gap-3">
            <Link
              to="/contact"
              className="px-4 py-2 text-sm font-semibold text-white bg-sky-600 hover:bg-sky-500 rounded-lg transition-all hover:shadow-lg hover:shadow-sky-500/25 active:scale-95"
            >
              Point of contact
            </Link>
          </div>

          <button
            onClick={() => setOpen(!open)}
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
            aria-controls="mobile-menu"
            className="md:hidden p-2 text-white/70 hover:text-white rounded-lg hover:bg-white/8 transition-colors"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              {open
                ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />}
            </svg>
          </button>
        </div>

        {open && (
          <div id="mobile-menu" className="md:hidden bg-[#0a1628]/95 backdrop-blur-md border border-white/10 rounded-2xl mb-4 p-4 flex flex-col gap-1">
            {links.map((l) => (
              <Link
                key={l.to}
                to={l.to}
                aria-current={isActive(l.to) ? 'page' : undefined}
                className={`px-4 py-3 text-sm font-medium rounded-xl transition-colors ${
                  isActive(l.to) ? 'text-white bg-white/12' : 'text-white/70 hover:text-white hover:bg-white/8'
                }`}
              >
                {l.label}
              </Link>
            ))}
            <Link to="/contact" className="mt-2 px-4 py-3 text-sm font-semibold text-white bg-sky-600 hover:bg-sky-500 rounded-xl text-center transition-colors">
              Point of contact
            </Link>
          </div>
        )}
      </div>
    </nav>
  )
}
