import { useEffect, useRef } from 'react'

const SPEC_URL = 'https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml'

const REDOC_THEME = {
  scrollYOffset: 116, // fixed nav (64px) + sticky intro strip (~52px)
  hideDownloadButton: false,
  disableSearch: false,
  theme: {
    colors: {
      primary: { main: '#2563eb' },
      success: { main: '#059669' },
      error:   { main: '#dc2626' },
      warning: { main: '#d97706' },
      text: {
        primary:   '#111827',
        secondary: '#4b5563',
      },
      border: { dark: '#e5e7eb', light: '#f3f4f6' },
      responses: {
        success: { color: '#065f46', backgroundColor: '#ecfdf5', tabTextColor: '#065f46' },
        error:   { color: '#991b1b', backgroundColor: '#fef2f2', tabTextColor: '#991b1b' },
      },
      http: {
        get:    '#2563eb',
        post:   '#059669',
        put:    '#d97706',
        delete: '#dc2626',
        patch:  '#7c3aed',
      },
    },
    typography: {
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
      fontSize: '14px',
      lineHeight: '1.65',
      fontWeightRegular: '400',
      fontWeightBold: '700',
      headings: {
        fontFamily: "'Inter', sans-serif",
        fontWeight: '800',
        lineHeight: '1.3',
      },
      code: {
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
        fontSize: '13px',
        lineHeight: '1.5',
        color: '#1e40af',
        backgroundColor: '#eff6ff',
        wrap: true,
      },
      links: { color: '#2563eb', visited: '#1d4ed8', hover: '#1d4ed8' },
    },
    sidebar: {
      backgroundColor: '#0a1628',
      textColor:       'rgba(255,255,255,0.6)',
      activeTextColor: '#ffffff',
      arrow:           { color: 'rgba(255,255,255,0.4)', size: '1.5em' },
    },
    rightPanel: {
      backgroundColor: '#0f1f38',
      textColor:       'rgba(255,255,255,0.85)',
      width:           '40%',
    },
    logo: { maxHeight: '36px', gutter: '16px' },
    schema: {
      nestedBackground: '#f9fafb',
      linesColor:       '#e5e7eb',
      defaultDetailsWidth: '75%',
      typeNameColor: '#2563eb',
      typeTitleColor: '#111827',
      requireLabelColor: '#dc2626',
    },
    codeBlock: { backgroundColor: '#0f1f38' },
  },
}

export default function Docs() {
  const containerRef = useRef(null)

  useEffect(() => {
    // If ReDoc is already loaded (e.g. hot-reload), init immediately
    if (window.Redoc) {
      window.Redoc.init(SPEC_URL, REDOC_THEME, containerRef.current)
      return
    }

    const script = document.createElement('script')
    script.src = 'https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js'
    script.async = true
    script.onload = () => {
      if (containerRef.current) {
        window.Redoc.init(SPEC_URL, REDOC_THEME, containerRef.current)
      }
    }
    document.body.appendChild(script)

    return () => {
      // Clean up the container so ReDoc can be re-initialised on next mount
      if (containerRef.current) containerRef.current.innerHTML = ''
    }
  }, [])

  return (
    // pt-16 = nav height (fixed). Intro strip is sticky top-16 so it stays below the nav.
    <div className="bg-white pt-16">
      {/* Intro strip — sticky, sits just below the fixed nav */}
      <div className="sticky top-16 z-40 bg-[#0a1628] border-b border-white/8 px-6 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-white font-bold text-sm leading-tight">Consent Building Block — API Reference</h1>
            <p className="text-white/40 text-xs mt-0.5">GovStack Consent BB v23Q4 · 42 endpoints · 234 tests passing</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              { label: '✓ GovStack v23Q4', green: true },
              { label: '42 endpoints' },
              { label: 'OAuth2 / JWT' },
              { label: 'OpenAPI 3.0' },
            ].map(c => (
              <span key={c.label} className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${
                c.green
                  ? 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20'
                  : 'text-blue-400 bg-blue-400/10 border-blue-400/20'
              }`}>
                {c.label}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* ReDoc mounts here. Loading spinner shown until it renders. */}
      <div ref={containerRef} style={{ minHeight: 'calc(100vh - 112px)' }}>
        <div className="flex items-center justify-center py-32 text-gray-400 text-sm gap-3">
          <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
          </svg>
          Loading API reference…
        </div>
      </div>
    </div>
  )
}
