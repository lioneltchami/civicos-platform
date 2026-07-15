import { Link } from "react-router-dom";

const AVAILABLE_DOCS = [
  {
    title: "Consent",
    path: "/docs/consent",
    status: "Current public documentation",
    desc: "GovStack Consent target-spec links, CivicOS implementation notes, and reviewer-facing submission guidance.",
  },
];

const FUTURE_DOCS = [
  {
    title: "Content Management System",
    state: "Roadmap only",
    desc: "The CMS remains part of CivicOS, but it does not yet have a dedicated public documentation route under /docs.",
  },
  {
    title: "Identity",
    state: "Roadmap only",
    desc: "Identity should receive its own evidence-backed documentation page when the implementation and review path are ready.",
  },
  {
    title: "Payments, Messaging, Scheduler",
    state: "Roadmap only",
    desc: "These capabilities stay visible as future building blocks rather than being presented as current documentation targets.",
  },
];

export default function Docs() {
  return (
    <div className="bg-[#060d1f] pt-16">
      <section className="relative overflow-hidden border-b border-white/8 px-6 py-20 md:py-24 lg:px-8">
        <div className="absolute inset-0 gradient-mesh" />
        <div className="absolute left-1/2 top-0 h-[28rem] w-[28rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="relative mx-auto max-w-6xl">
          <span className="inline-flex rounded-full border border-sky-400/20 bg-sky-400/10 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-200">
            Documentation hub
          </span>
          <h1 className="mt-8 max-w-4xl text-5xl font-semibold tracking-[-0.04em] text-white md:text-6xl">
            CivicOS building block documentation
          </h1>
          <p className="mt-6 max-w-4xl text-lg leading-8 text-white/60">
            This hub separates current public documentation from future building
            block plans. Right now, Consent is the only active documentation
            route under <span className="text-white">/docs</span>; future
            building blocks should only appear here once they have their own
            evidence-backed pages.
          </p>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Available documentation
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Current public docs routes
            </h2>
          </div>

          <div className="grid gap-5 lg:grid-cols-[1.2fr]">
            {AVAILABLE_DOCS.map((section) => (
              <article
                key={section.title}
                className="rounded-[28px] border border-sky-400/25 bg-[linear-gradient(145deg,rgba(14,165,233,0.12),rgba(8,17,28,0.85)_55%,rgba(8,17,28,0.96))] p-7"
              >
                <span className="inline-flex rounded-full border border-sky-400/20 bg-sky-400/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-sky-200">
                  {section.status}
                </span>
                <h3 className="mt-5 text-2xl font-semibold text-white">
                  {section.title}
                </h3>
                <p className="mt-4 text-sm leading-7 text-white/60">
                  {section.desc}
                </p>
                <Link
                  to={section.path}
                  className="mt-7 inline-flex items-center gap-2 text-sm font-semibold text-sky-300 transition-colors hover:text-sky-200"
                >
                  Open Consent docs
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3"
                    />
                  </svg>
                </Link>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="border-b border-white/8 px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-10 max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Future documentation
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              Roadmap, not docs yet
            </h2>
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            {FUTURE_DOCS.map((item) => (
              <article
                key={item.title}
                className="rounded-[28px] border border-white/10 bg-white/[0.04] p-7"
              >
                <span className="inline-flex rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/50">
                  {item.state}
                </span>
                <h3 className="mt-5 text-2xl font-semibold text-white">
                  {item.title}
                </h3>
                <p className="mt-4 text-sm leading-7 text-white/60">
                  {item.desc}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="px-6 py-16 md:py-20 lg:px-8">
        <div className="mx-auto grid max-w-7xl gap-12 lg:grid-cols-[1fr_0.9fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-sky-300">
              Submission guidance
            </p>
            <h2 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
              How to use this hub correctly
            </h2>
            <div className="mt-8 space-y-4">
              {[
                "Keep the homepage as the stable CivicOS product website.",
                "Use the exact per-building-block page, not the hub itself, as the software documentation URL in the GovStack form.",
                "Add future pages such as /docs/cms, /docs/identity, or /docs/payments only after those building blocks have their own evidence and review story.",
              ].map((item) => (
                <div
                  key={item}
                  className="flex gap-3 text-sm leading-7 text-white/58"
                >
                  <span className="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-sky-300" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </div>

          <article className="rounded-[32px] border border-sky-400/20 bg-sky-950/20 p-7">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-200/80">
              Current target
            </p>
            <h3 className="mt-4 text-2xl font-semibold text-white">
              Use /docs/consent right now
            </h3>
            <p className="mt-4 text-sm leading-7 text-white/58">
              The current active documentation route is the Consent page. This
              hub exists for navigation and future structure, but the GovStack
              form should point directly to the Consent route rather than to
              /docs itself.
            </p>
            <Link
              to="/docs/consent"
              className="mt-6 inline-flex items-center gap-2 rounded-2xl bg-sky-600 px-5 py-3 text-sm font-semibold text-white transition-all hover:bg-sky-500 hover:shadow-xl hover:shadow-sky-500/25"
            >
              Open /docs/consent
            </Link>
          </article>
        </div>
      </section>
    </div>
  );
}
