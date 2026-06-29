# About This Project

## What We Are Building

Govstack is a **modular digital government services platform** — a production-grade, open-source foundation that enables municipalities and public sector organizations to deliver citizen-facing digital services.

At its core it is a Django + Wagtail application structured as a set of composable "building blocks". Each block is a Django app with a well-defined scope: content management, dynamic forms, citizen portal, workflow routing, audit logging. Blocks can be deployed individually or assembled into a full platform.

The platform ships with everything a municipal IT team needs to go live: accessible templates, bilingual support, secure authentication, audit trails, and a staff-friendly CMS — all configured to meet Canadian public sector compliance requirements out of the box.

---

## Who It's For

### Primary clients: Canadian municipalities
Small-to-medium municipalities (population 5,000–500,000) that need to modernize their digital presence and service delivery but lack the budget for custom development or enterprise SaaS platforms. They need something their small IT team can own and their non-technical staff can operate.

### Secondary: provincial agencies and non-profits
Any public-sector or government-adjacent organization subject to similar accessibility, privacy, and bilingual requirements.

---

## Target Users (Personas)

**Marie — Municipal Communications Coordinator**
Marie manages the municipal website. She publishes news, updates service pages, and manages events. She needs a CMS that doesn't require a developer to add a page, and that enforces the city's branding automatically. She works in French.

**David — IT Administrator**
David is the one-person IT department for a small municipality. He needs a platform he can deploy to the city's cloud provider, keep secure with minimal effort, and hand off to staff without writing documentation from scratch.

**Sophie — Citizen / Resident**
Sophie needs to submit a permit application, check its status, and receive updates without calling the office. She uses a screen reader and expects the website to work with it. She prefers to interact in French.

**Karim — By-law Officer (Internal Staff)**
Karim receives service requests assigned to him, reviews attachments, adds notes, and updates the status. He needs a clear queue of his work items and a simple interface to take action.

---

## Problems We're Solving

1. **Municipal websites are inaccessible.** Most are static HTML or outdated CMS installs that fail basic WCAG checks and are difficult for staff to update.

2. **Service delivery is still paper-based or phone-based.** Citizens can't submit requests online, check status, or receive updates digitally.

3. **Off-the-shelf SaaS is too expensive or too generic.** Platforms like Salesforce Government Cloud or ServiceNow are priced for large governments and require expensive implementation partners.

4. **Custom builds don't share learnings.** Every municipality rebuilds the same things (forms, portals, workflows) from scratch. Govstack gives them a shared foundation to build on.

5. **Compliance is treated as an afterthought.** Accessibility, bilingual support, privacy, and audit logging are hard to retrofit. Govstack bakes them in.

---

## Long-Term Vision

Govstack becomes the **go-to open-source platform for Canadian municipal digital services** — with a library of pre-built, reusable building blocks (permit applications, council agendas, public consultations, service request tracking) that municipalities can install like plugins.

Over time we want to:
- Build a **marketplace of civic building blocks** — vetted, compliant, drop-in modules for common municipal use cases
- Establish a **shared components library** so municipalities can contribute and benefit from each other's work
- Align with the **GovStack global initiative** to enable knowledge transfer across jurisdictions
- Support **multi-tenancy** so a regional shared-services organization can run one Govstack instance for multiple municipalities

---

## What Success Looks Like

- A municipality can go from zero to a live, accessible, bilingual website with at least one transactional service in **under 3 months**
- A communications coordinator can publish a new page without opening a support ticket
- A citizen can submit a service request, receive a confirmation email, and track status — without a phone call
- An auditor can produce a full log of all access to citizen data for a given period
- A developer can add a new building block without breaking existing ones
