import { useState } from 'react'

const TOPICS = [
  'Consent building block assessment',
  'Municipal website rollout',
  'Documentation question',
  'Consent module follow-up',
  'Implementation partner inquiry',
  'Other',
]

export default function Contact() {
  const [sent, setSent] = useState(false)
  const [form, setForm] = useState({ name: '', org: '', email: '', topic: '', message: '' })

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const handleSubmit = (e) => {
    e.preventDefault()
    const mailto = `mailto:lioneltchami@gmail.com?subject=${encodeURIComponent(`[CivicOS] ${form.topic || 'Inquiry'} — ${form.org}`)}&body=${encodeURIComponent(`Name: ${form.name}\nOrganization: ${form.org}\nEmail: ${form.email}\n\n${form.message}`)}`
    window.location.href = mailto
    setTimeout(() => setSent(true), 100)
  }

  return (
    <div className="bg-[#060d1f] pt-16">
      <section className="relative py-24 px-6 lg:px-8 border-b border-white/8 overflow-hidden">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[500px] h-[280px] bg-sky-600/8 rounded-full blur-3xl" />
        <div className="relative max-w-2xl mx-auto text-center">
          <p className="text-sky-300 font-semibold text-sm uppercase tracking-widest mb-4">Point of contact</p>
          <h1 className="text-5xl md:text-6xl font-black text-white mb-6">
            Contact CivicOS
          </h1>
          <p className="text-white/55 text-lg leading-relaxed">
            Use this page as the public point of contact for CivicOS. It is written for GovStack reviewers, municipal teams, community operators, and implementation partners who need a real human contact instead of a placeholder inbox.
          </p>
        </div>
      </section>

      <section className="py-20 px-6 lg:px-8">
        <div className="max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-5 gap-12">
          <div className="lg:col-span-2 space-y-8">
            <div>
              <p className="text-xs font-semibold text-white/30 uppercase tracking-widest mb-4">Direct contact</p>
              <a href="mailto:lioneltchami@gmail.com"
                className="flex items-center gap-3 text-white hover:text-sky-300 transition-colors group">
                <div className="w-10 h-10 rounded-xl bg-white/8 group-hover:bg-sky-500/15 flex items-center justify-center transition-colors">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                  </svg>
                </div>
                <span className="font-medium text-sm">lioneltchami@gmail.com</span>
              </a>
            </div>

            <div>
              <p className="text-xs font-semibold text-white/30 uppercase tracking-widest mb-4">Reference links</p>
              <a href="https://testing.govstack.global" target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-3 text-white hover:text-sky-300 transition-colors group">
                <div className="w-10 h-10 rounded-xl bg-white/8 group-hover:bg-sky-500/15 flex items-center justify-center transition-colors">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                  </svg>
                </div>
                <span className="font-medium text-sm">testing.govstack.global</span>
              </a>
            </div>

            <div className="pt-4 border-t border-white/8">
              <p className="text-xs font-semibold text-white/30 uppercase tracking-widest mb-4">What we can help with</p>
              <ul className="space-y-2">
                {[
                  'Consent building block submission language',
                  'GovStack assessment questions',
                  'Municipal website and service-content rollout',
                  'Documentation questions from reviewers',
                  'Consent module follow-up after the current assessment',
                  'Implementation partnership discussions',
                ].map((item) => (
                  <li key={item} className="flex items-start gap-2 text-sm text-white/45">
                    <svg className="w-3.5 h-3.5 text-sky-400 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 8 8">
                      <circle cx="4" cy="4" r="3" />
                    </svg>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>

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
                  <p className="text-white/50">Your email client should have opened. We&apos;ll get back to you soon.</p>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div>
                    <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Name *</label>
                    <input required value={form.name} onChange={set('name')} type="text" placeholder="Jane Smith"
                      className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-sky-500/60 focus:bg-white/8 transition-all" />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Organization *</label>
                    <input required value={form.org} onChange={set('org')} type="text" placeholder="City of Ottawa"
                      className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-sky-500/60 focus:bg-white/8 transition-all" />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Email *</label>
                  <input required value={form.email} onChange={set('email')} type="email" placeholder="jane@example.gov"
                    className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-sky-500/60 focus:bg-white/8 transition-all" />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Topic</label>
                  <select value={form.topic} onChange={set('topic')}
                    className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white text-sm focus:outline-none focus:border-sky-500/60 focus:bg-white/8 transition-all appearance-none"
                    style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='rgba(255,255,255,0.3)' stroke-width='2'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 1rem center', backgroundSize: '1rem' }}>
                    <option value="" className="bg-[#0a1628]">Select a topic…</option>
                    {TOPICS.map((t) => <option key={t} value={t} className="bg-[#0a1628]">{t}</option>)}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-white/40 uppercase tracking-widest mb-2">Message *</label>
                  <textarea required value={form.message} onChange={set('message')} rows={5}
                    placeholder="Tell us about your use case, organization size, timeline, or anything else relevant…"
                    className="w-full bg-white/6 border border-white/12 rounded-xl px-4 py-3 text-white placeholder-white/25 text-sm focus:outline-none focus:border-sky-500/60 focus:bg-white/8 transition-all resize-none" />
                </div>

                <button type="submit"
                  className="w-full py-4 bg-sky-600 hover:bg-sky-500 text-white font-bold rounded-xl transition-all hover:shadow-xl hover:shadow-sky-500/30 active:scale-95 text-sm">
                  Send Message →
                </button>
                <p className="text-xs text-white/25 text-center">This opens your email client and addresses the message to the current public submission contact.</p>
              </form>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
