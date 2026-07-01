# Government & Accessibility Standards

This document details the specific standards, laws, and guidelines that govern how CivicOS must be built for Canadian municipal and public sector clients.

---

## Accessibility

### WCAG 2.1 Level AA
**Web Content Accessibility Guidelines 2.1, Level AA** is the baseline requirement for all citizen-facing interfaces.

Key success criteria we must satisfy (non-exhaustive):

| Criterion | Level | Description |
|---|---|---|
| 1.1.1 Non-text Content | A | All images have text alternatives |
| 1.3.1 Info and Relationships | A | Structure conveyed through semantics, not just visual formatting |
| 1.3.3 Sensory Characteristics | A | Instructions don't rely solely on shape, size, colour, or location |
| 1.4.1 Use of Color | A | Color not used as the only way to convey information |
| 1.4.3 Contrast (Minimum) | AA | 4.5:1 for text, 3:1 for large text |
| 1.4.4 Resize Text | AA | Text can be resized up to 200% without loss of content |
| 1.4.10 Reflow | AA | Content reflows at 320px width without horizontal scrolling |
| 1.4.11 Non-text Contrast | AA | UI components have 3:1 contrast against adjacent colours |
| 2.1.1 Keyboard | A | All functionality available by keyboard |
| 2.1.2 No Keyboard Trap | A | Focus can always be moved away from a component |
| 2.4.3 Focus Order | A | Focus order preserves meaning and operability |
| 2.4.4 Link Purpose | A | Link purpose can be determined from link text or context |
| 2.4.7 Focus Visible | AA | Keyboard focus indicator is visible |
| 3.1.1 Language of Page | A | Default language of page is programmatically determined |
| 3.1.2 Language of Parts | AA | Language of passages that differ from default is identified |
| 3.2.2 On Input | A | Changing a setting doesn't cause unexpected context change |
| 3.3.1 Error Identification | A | Errors are identified in text |
| 3.3.2 Labels or Instructions | A | Labels or instructions provided for user input |
| 3.3.3 Error Suggestion | AA | Error correction suggestions provided where possible |
| 4.1.2 Name, Role, Value | A | All UI components have accessible name, role, and state |
| 4.1.3 Status Messages | AA | Status messages can be determined programmatically |

### AODA (Ontario)
The *Accessibility for Ontarians with Disabilities Act* (AODA) requires WCAG 2.0 Level AA compliance for public sector organizations. CivicOS targets WCAG 2.1 AA, which is a superset of this requirement.

### Accessible Canada Act
Federal legislation requiring the identification and removal of barriers to accessibility. For digital services, this means WCAG 2.1 AA compliance and ongoing accessibility monitoring.

---

## Privacy Legislation

### PIPEDA — Federal
*Personal Information Protection and Electronic Documents Act* (S.C. 2000, c. 5) applies to federal organizations and any organization engaged in commercial activities across provincial borders.

Key principles (Fair Information Principles):
1. **Accountability** — designate a Privacy Officer
2. **Identifying purposes** — state why data is collected before or at collection
3. **Consent** — obtain meaningful consent
4. **Limiting collection** — collect only what is necessary
5. **Limiting use, disclosure, and retention** — use data only for stated purposes; retain only as long as necessary
6. **Accuracy** — keep personal information accurate
7. **Safeguards** — protect with security appropriate to sensitivity
8. **Openness** — make privacy policies publicly available
9. **Individual access** — allow individuals to access their own information
10. **Challenging compliance** — provide a process for complaints

### Quebec Law 25 (Bill 64)
*An Act to modernize legislative provisions as regards the protection of personal information* introduces GDPR-like requirements for Quebec-based organizations:

- Privacy Impact Assessments (PIAs) required before new data collection
- Privacy by default as a design principle
- Right to data portability
- Right to be forgotten (within retention limits)
- Breach notification to the *Commission d'accès à l'information* (CAI) within 72 hours
- Published privacy policy in clear language

### Municipal Freedom of Information and Protection of Privacy Act (MFIPPA — Ontario)
Governs how Ontario municipalities collect, use, and disclose personal information. Key requirements:
- Notice of collection at the point of collection
- Use limited to the stated purpose or a consistent purpose
- Citizens have the right to access and correct their records
- Retention and disposal must follow municipal records schedules

---

## Official Languages

### Official Languages Act (Federal)
Requires federal institutions to communicate with the public in both English and French. While municipalities are not federally subject, francophone municipalities and those in bilingual regions (e.g., eastern Ontario, New Brunswick) have equivalent requirements under provincial law.

### Implementation Requirements for CivicOS
- All UI strings: translated via Django i18n (`gettext_lazy`)
- All page content: managed as separate EN/FR Wagtail page trees
- All email templates: available in both languages; language selected by recipient preference
- Language switcher: visible on every page, persistent across sessions for authenticated users
- Wagtail admin: available in French (`LANGUAGE_CODE` in Wagtail settings)
- Date/time formats: locale-aware (`{% localize %}` in templates, `USE_L10N = True`)

---

## Records Management

### Municipal Records Retention Schedules
Municipal records are subject to retention schedules set by the municipality and provincial records management legislation. Common retention periods:

| Record Type | Typical Retention |
|---|---|
| Service requests / permits | 7 years after file closure |
| Citizen correspondence | 2–7 years depending on subject matter |
| Financial records | 7 years |
| Meeting minutes | Permanent |
| Audit logs | 7 years minimum |
| Security logs | 1–2 years |

CivicOS must allow per-deployment configuration of retention periods and must enforce automated expiry.

### Legal Holds
Records subject to litigation, ATIP requests, or regulatory investigation must not be altered or destroyed. A legal hold mechanism must prevent automated purge from deleting held records.

---

## Security Standards

### Government of Canada IT Security Risk Management (ITSG-33)
Framework for IT security risk management in the federal government. Applicable to federally contracted software and adopted as a reference by many provinces and municipalities.

### NIST SP 800-53
*Security and Privacy Controls for Information Systems and Organizations*. A comprehensive control catalogue used as a reference for municipal IT security programs.

### OWASP Top 10 (2021)
All development must protect against:
1. Broken Access Control
2. Cryptographic Failures
3. Injection
4. Insecure Design
5. Security Misconfiguration
6. Vulnerable and Outdated Components
7. Identification and Authentication Failures
8. Software and Data Integrity Failures
9. Security Logging and Monitoring Failures
10. Server-Side Request Forgery

---

## Plain Language

### Government of Canada Plain Language Guidelines
All citizen-facing copy must be written at approximately a Grade 8 reading level. Specific guidance:
- Use active voice
- Use short sentences (target under 20 words)
- Use common words; avoid jargon, acronyms, and legal language
- Use "you" to address the reader directly
- Put the most important information first
- Use headings and white space to break up text

The plain language requirement applies to all body copy, form labels, error messages, help text, email notifications, and UI labels.
