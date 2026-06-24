# Nexplane Site Redesign — Design Spec
# 2026-06-24

---

## Summary

Complete rewrite of nexplane.ai public homepage. Reposition Nexplane from "security control plane" to "control plane for infrastructure change." Keep existing static HTML/CSS/JS stack, dark theme design tokens, and form backend (Google Apps Script). No new frameworks or build steps.

Strategic thesis: **Infrastructure changes are inevitable. Outages are optional.**

---

## Stack

- Static HTML/CSS/JS, Netlify-hosted
- Inter + JetBrains Mono fonts
- Form backend: Google Apps Script → Google Sheets (unchanged)
- No build step, no framework

---

## Approach

Option A: Complete HTML rewrite. Keep CSS design tokens. Add new component styles to style.css. form.js and google_apps_script.js untouched except for form field label updates.

---

## Navigation

Links: Platform · Integrations · Docs · Request demo (primary button)
- "Docs" → nexplane-docs GitHub repo
- "Request demo" → scrolls to #request-demo form anchor

---

## Section Structure (12 sections)

### 1. Hero

Badge: `Control plane for infrastructure change`

Headline:
> Infrastructure changes are inevitable.
> Outages are optional.

Subhead:
> Plan, approve, execute, and roll back infrastructure changes across cloud, network, identity, Kubernetes, endpoints, and security systems — from one control plane.

CTAs:
- Primary: Request demo (scrolls to form)
- Secondary: Read docs → (links to nexplane-docs)

Positioning line (small text below CTAs):
> Nexplane is the control plane for infrastructure change.

Visual: Code window showing a change request lifecycle — Discover → Plan → Approve → Execute → Observe → Rollback — with target infrastructure listed (AWS · k8s · Active Directory · Vault · firewall).

Stats row:
- 80+ change types
- 11 integration domains
- 100% rollback coverage

---

### 2. Problem

Eyebrow: The Problem

Title: `Every infrastructure team has tools. Nobody owns the change.`

Three cards (Terraform / ServiceNow / Datadog) showing each tool owns a fragment. Closing copy: no system owns end-to-end change execution — the gaps live between the tools.

---

### 3. Solution / Lifecycle

Eyebrow: How It Works

Title: `One workflow for every infrastructure change.`

8-step horizontal flow:
Discover → Understand → Plan → Approve → Execute → Observe → Rollback → Document

Each step: name + 1-line description. Connected node visual using existing border/surface tokens. Scrollable on mobile.

Closing copy: Nexplane turns scattered infrastructure work into controlled, auditable, reversible change workflows.

---

### 4. Four Questions

Eyebrow: The Platform

Title: `Four questions. One answer.`

2×2 card grid:

| Question | Capability |
|---|---|
| What do I have? | Infrastructure graph |
| What should I change? | Recommendations engine |
| What will happen if I change it? | Impact simulation |
| Can I safely undo it? | Rollback center |

Each card: icon + question (large) + capability name + monospace tag.

---

### 5. Infrastructure Memory

Eyebrow: Infrastructure Memory

Title: `Ask why anything exists.`

Dark terminal block showing 5 example queries + responses:
- Why is port 8443 open?
- Who approved this firewall rule?
- Which applications depend on this certificate?
- Can this VM be deleted?
- What changed yesterday?

Copy: Nexplane preserves context, provenance, approvals, dependency chains, timelines, and rollback history behind every change. Every enterprise has forgotten why infrastructure exists. Nexplane remembers.

---

### 6. Simulate Before You Change

Eyebrow: Impact Simulation

Title: `Know what breaks before it breaks.`

Terminal-style cascade:
```
Remove firewall rule fw-0a4b
→ 12 assets affected
→ 4 services at risk
→ approval required
→ rollback available
```

Explains: blast radius, dependency chains, confidence scores, prechecks, postchecks, rollback strategies.

---

### 7. Infrastructure Graph

Eyebrow: Infrastructure Graph

Title: `Inventory is flat. Infrastructure is connected.`

Relationship tag cloud showing interconnected entity types:
apps ↔ users ↔ certificates ↔ secrets ↔ DNS ↔ cloud resources ↔ IAM ↔ firewalls ↔ routes ↔ k8s clusters ↔ monitoring ↔ source control ↔ tickets

Copy: Relationships are what make changes safe or dangerous. Nexplane maps them so you understand dependencies before you act.

---

### 8. Recommendations

Eyebrow: Recommendations

Title: `Dependabot for infrastructure.`

7 action cards with severity tags:
- Remove stale firewall rules (Medium)
- Rotate expiring certificates (High)
- Patch critical hosts (Critical)
- Archive inactive users (Low)
- Reduce IAM exposure (High)
- Add missing owners (Medium)
- Improve rollback coverage (Medium)

Copy: Continuous scanning surfaces prioritized, actionable recommendations. One click creates a change request.

---

### 9. Rollback

Eyebrow: Rollback Center

Title: `Infrastructure should be reversible.`

Keep existing rollback card grid (8 change type ↔ undo pairs). Update comparison table to include broader infrastructure tools (Terraform, Ansible, ServiceNow, Datadog, AWS SSM). Add live smoke test proof point paragraph.

Copy: Every change should have a rollback plan, validation checks, last-known-good state, and audit trail.

---

### 10. MCP / AI Agents

Eyebrow: MCP Server

Title: `Built for humans and AI agents.`

Two-column layout:
- Left: UI flow (same human-in-the-loop flow from current AI safety section, updated)
- Right: Terminal block showing 5 agent queries:
  - What systems depend on payroll?
  - What will break if I rotate this certificate?
  - Generate a safe change plan to patch critical Linux hosts.
  - Which assets lack rollback coverage?
  - Create a change request for this recommendation.

Copy: Everything available in the UI is accessible through the MCP server. Claude, ChatGPT, Cursor, Codex, Windsurf, and internal agents can reason about infrastructure through Nexplane — without direct cloud access.

---

### 11. Integrations

Eyebrow: Integrations

Title: `Everything connects.`

11-domain grid. Each domain: header + 3–5 tool pills.

| Domain | Examples |
|---|---|
| Cloud | AWS · GCP · Azure · OCI |
| Identity | Active Directory · Okta · Entra ID · Keycloak |
| Network | pfSense · Palo Alto · Cisco · iptables |
| Containers | Kubernetes · Docker · ECS |
| Endpoints | Linux · Windows Server · macOS |
| Secrets | HashiCorp Vault · AWS Secrets Manager · Azure Key Vault |
| Certificates | Let's Encrypt · DigiCert · Internal PKI |
| Monitoring | Datadog · Prometheus · CloudWatch |
| Source control | GitHub · GitLab · Bitbucket |
| Ticketing | Jira · ServiceNow · Linear |
| Compliance | CIS Controls · SOC 2 · FedRAMP |

---

### 12. Final CTA + Form

Section title: `Change infrastructure with confidence.`

Form heading: `Request a demo`
Form subhead: We'll walk you through the platform against a real or demo environment.

Form fields (same as current, role options updated):
- First name / Last name
- Work email
- Company
- Role: Platform Engineer · Infrastructure Engineer · Cloud Engineer · SRE · Network Engineer · Security Engineer · Technology Operations · Engineering Leadership · Other
- Fleet size (unchanged)
- What are you trying to solve? (renamed from "biggest security operations pain point")
- Submit: Request demo

Success message: ✓ Got it — we'll be in touch within 48 hours.

---

## CSS Changes

Existing tokens preserved unchanged:
- `--brand: #6366f1`, `--accent: #22d3ee`, `--surface`, `--surface2`, `--border`
- Inter / JetBrains Mono fonts
- All existing component patterns reused where possible

New component styles needed:
- `.lifecycle-flow` — horizontal connected 8-step flow, scrollable on mobile
- `.terminal-block` — dark query/response display for Memory + Simulate sections
- `.questions-grid` — 2×2 card grid with large question text
- `.recommendation-card` — action card with severity badge
- `.integration-domain` — domain header + pill cluster
- `.mcp-split` — two-column layout for MCP section
- `.graph-tags` — interconnected entity tag cloud

---

## SEO

Title: `Nexplane — The Control Plane for Infrastructure Change`

Description: `Plan, approve, execute, observe, and roll back infrastructure changes across cloud, network, identity, Kubernetes, endpoints, and security systems.`

---

## Files Changed

| File | Change |
|---|---|
| `public/index.html` | Complete rewrite |
| `public/style.css` | New component styles appended |
| `public/form.js` | Field name update (pain_point label), no logic change |
| `README.md` | Updated positioning + local dev instructions |

`src/google_apps_script.js` — unchanged.

---

## Out of Scope

- No new pages or routing
- No new fonts or external dependencies
- No build step
- No animation library
- Sub-pages (docs, changelog, blog) deferred to future iteration
