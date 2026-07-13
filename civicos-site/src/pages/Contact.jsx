import { useState } from 'react'

const TOPICS = [
  'Deploy the Consent Building Block',
  'CivicOS CMS for my municipality',
  'Partner / contribute',
  'GovStack certification questions',
  'Request a building block',
  'Other',
]

export default function Contact() {
  const [sent, setSent] = useState(false)
  const [form, setForm] = useState({ name: '', org: '', email: '', topic: '', message: '' })

  const set = k => e => setForm(f => ({ ...f, [k]: e.target.value }))

  const handleSubmit = e => {
    e.preventDefault()
    // In production, POST to a form endpoint (Formspree / Cloudflare Worker / etc.)
    const mailto = `mailto:contact@civicosbb.ca?subject=${encodeURIComponent(`[CivicOS] ${form.topic || 'Inquiry'} — ${form.org}`)}&body=${encodeURIComponent(`Name: ${form.name}\nOrganization: ${form.org}\nEmail: ${form.email}\n\n${form.message}`)}`
    window.location.href = mailto
    // Small delay so the mailto navigation fires before re-render
    setTimeout(() => setSent(true), 100)
  }

  return (
    <div className="bg-[#060d1f] pt-16">
      {/* Header */}
      <section className="relative py-24 px-6 lg:px-8 border-b border-white/8 overflow-hidden">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[500px] h-[280px] bg-blue-600/8 rounded-full blur-3xl" />
        <div className="relative max-w-2xl mx-auto text-center">
          <p className="text-blue-400 font-semibold text-sm uppercase tracking-widest mb-4">Contact</p>
          <h1 className="text-5xl md:text-6xl font-black text-white mb-6">
            Let's talk
          </h1>
          <p className="text-white/55 text-lg leading-relaxed">
            Whether you're a government agency evaluating the Consent BB, a municipality interested in CivicOS CMS, or a developer who wants to contribute — we want to hear from you.
          </p>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8">
        <div className="max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-5 gap-12">

          {/* Left info */}
          <div className="lg:col-span-2 space-y-8">
            <div>
              <p className="text-xs font-semibold text-white/30 uppercase tracking-widest mb-4">Direct contact</p>
              <a href="mailto:contact@civicosbb.ca"
                className="flex items-center gap-3 text-white hover:text-blue-400 transition-colors group">
                <div className="w-10 h-10 rounded-xl bg-white/8 group-hover:bg-blue-500/15 flex items-center justify-center transition-colors">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                  </svg>
                </div>
                <span className="font-medium text-sm">contact@civicosbb.ca</span>
              </a>
            </div>

            <div>
              <p className="text-xs font-semibold text-white/30 uppercase tracking-widest mb-4">Open source</p>
              <a href="https://github.com/civicosbb" target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-3 text-white hover:text-blue-400 transition-colors group">
                <div className="w-10 h-10 rounded-xl bg-white/8 group-hover:bg-blue-500/15 flex items-center justify-center transition-colors">
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
                  </svg>
                </div>
                <span className="font-medium text-sm">github.com/civicosbb</span>
              </a>
            </div>

            <div className="pt-4 border-t border-white/8">
              <p className="text-xs font-semibold text-white/30 uppercase tracking-widest mb-4">What we can help with</p>
              <ul className="space-y-2">
                {[
                  'Evaluating or deploying the Consent BB',
                  'CivicOS CMS for municipalities',
                  'GovStack certification questions',
                  'Custom building block development',
                  'Partnership & contribution',
                ].map(item => (
                  <li key={item} className="flex items-start gap-2 text-sm text-white/45">
                    <svg className="w-3.5 h-3.5 text-blue-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 8 8">
                      <circle cx="4" cy="4" r="3" />
                    </svg>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Form */}
          <div className="lg:col-span-3">
            {sent ? (
              <div className="h-full flex items-center justify-center">
                <div className="text-center py-16">
                  <div className="w-16 h-16 rounded-full bg-emerald-500/15 flex items-center justify-center mx-auto mb-6">
                    <svg className="w-8 h-8 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                  </div>
                  <h3 className="text-2xl font-black text-white mb-3">Message sent</h3>
                  <p className="text-white/50">Your email client should have opened. We'll get back to you soon.</p>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div>
                    <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Name *</label>
                    <input required value={form.name} onChange={set('name')} type="text" placeholder="Jane Smith"
                      className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-blue-500/60 focus:bg-white/8 transition-all" />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Organization *</label>
                    <input required value={form.org} onChange={set('org')} type="text" placeholder="City of Ottawa"
                      className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-blue-500/60 focus:bg-white/8 transition-all" />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Email *</label>
                  <input required value={form.email} onChange={set('email')} type="email" placeholder="jane@example.gov"
                    className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-blue-500/60 focus:bg-white/8 transition-all" />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Topic</label>
                  <select value={form.topic} onChange={set('topic')}
                    className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white text-sm focus:outline-none focus:border-blue-500/60 focus:bg-white/8 transition-all appearance-none"
                    style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='rgba(255,255,255,0.3)' stroke-width='2'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 1rem center', backgroundSize: '1rem' }}>
                    <option value="" className="bg-[#0a1628]">Select a topic…</option>
                    {TOPICS.map(t => <option key={t} value={t} className="bg-[#0a1628]">{t}</option>)}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Message *</label>
                  <textarea required value={form.message} onChange={set('message')} rows={5}
                    placeholder="Tell us about your use case, organization size, timeline, or anything else relevant…"
                    className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-blue-500/60 focus:bg-white/8 transition-all resize-none" />
                </div>

                <button type="submit"
                  className="w-full py-4 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-blue-500/30 active:scale-98 text-sm">
                  Send Message →
                </button>
                <p className="text-xs text-white/25 text-center">This opens your email client. We respond within 1–2 business days.</p>
              </form>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
