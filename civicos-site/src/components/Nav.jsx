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
    { to: '/building-blocks', label: 'Building Blocks' },
    { to: '/about', label: 'About' },
    { to: '/docs', label: 'Docs' },
  ]

  const isActive = (to) => loc.pathname === to

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
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2.5" aria-label="CivicOS home">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/30" aria-hidden="true">
              <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
              </svg>
            </div>
            <span className="font-bold text-white text-[17px] tracking-tight">
              Civic<span className="text-blue-400">OS</span>
            </span>
          </Link>

          {/* Desktop links */}
          <div className="hidden md:flex items-center gap-1" role="list">
            {links.map(l => (
              <Link key={l.to} to={l.to} role="listitem"
                aria-current={isActive(l.to) ? 'page' : undefined}
                className={`px-4 py-2 text-sm font-medium transition-colors rounded-lg ${
                  isActive(l.to) ? 'text-white bg-white/10' : 'text-white/70 hover:text-white hover:bg-white/8'
                }`}>
                {l.label}
              </Link>
            ))}
          </div>

          <div className="hidden md:flex items-center gap-3">
            <Link to="/contact"
              className="px-4 py-2 text-sm font-semibold text-white bg-blue-600 hover:bg-blue-500 rounded-lg transition-all hover:shadow-lg hover:shadow-blue-500/25 active:scale-95">
              Contact Us
            </Link>
          </div>

          {/* Mobile burger */}
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

        {/* Mobile menu */}
        {open && (
          <div id="mobile-menu" className="md:hidden bg-[#0a1628]/95 backdrop-blur-md border border-white/10 rounded-2xl mb-4 p-4 flex flex-col gap-1">
            {links.map(l => (
              <Link key={l.to} to={l.to}
                aria-current={isActive(l.to) ? 'page' : undefined}
                className={`px-4 py-3 text-sm font-medium rounded-xl transition-colors ${
                  isActive(l.to) ? 'text-white bg-white/12' : 'text-white/70 hover:text-white hover:bg-white/8'
                }`}>
                {l.label}
              </Link>
            ))}
            <Link to="/contact" className="mt-2 px-4 py-3 text-sm font-semibold text-white bg-blue-600 hover:bg-blue-500 rounded-xl text-center transition-colors">
              Contact Us
            </Link>
          </div>
        )}
      </div>
    </nav>
  )
}
