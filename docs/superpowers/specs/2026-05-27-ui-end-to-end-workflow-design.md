# UI End-to-End Workflow Design

**Date:** 2026-05-27

## Goal

Ensure the full user-facing workflow is coherent, covers the core use cases a security operator would actually use, and presents the platform's value clearly — from first login through daily proactive hardening.

---

## Navigation Structure

Persistent left sidebar with top-level sections:

| Section | Purpose |
|---|---|
| **Dashboard** | Command center: exposure summary, pending approvals, in-flight CRs with rollback status, recent activity |
| **Projects** | AI-assisted campaigns: chat interface, plan review, CR generation |
| **Assets** | Inventory explorer: browse by environment/type/risk, one-off CR creation, batch actions |
| **Change Requests** | Unified tracker: pending approval, executing, completed, rolled back (absorbs ApprovalsQueue) |
| **Operations** | Planned/repeatable work: Runbooks, Scheduled Operations, Maintenance Windows |
| **Connectors** | Connector setup and management (top-level, not buried in Settings) |
| **Settings** | Org, users, approval policies, risk thresholds |

**VulnerabilityRemediation** is not a standalone nav item — it is a saved filter preset inside Assets called "Unmitigated Criticals."

**Maintenance Windows** live under Operations for day-to-day scheduling; the config for allowed windows also appears in Settings.

---

## New User Onboarding

Until the first connector is live, the sidebar shows a setup checklist pinned above all nav items:

1. Connect your first data source (Connectors)
2. Wait for initial asset sync
3. Review your asset inventory
4. Create your first project or one-off CR

Once at least one connector is live and assets have synced, the checklist collapses permanently and the full Dashboard takes over as the landing page.

---

## Dashboard

The home screen for daily operators. Answers: "what needs my attention right now?"

**Panels:**
- **Exposure summary** — asset count by risk tier (critical/high/medium/low), trend vs. last 7 days
- **Pending approvals** — CRs awaiting my approval or awaiting someone else's (two tabs)
- **In-flight CRs** — executing now, with rollback status indicator on each (green = rollback available, amber = executing, red = rollback needed)
- **Recent activity** — last 10 CR completions/rollbacks across projects and one-off actions
- **Top unmitigated risks** — 5 highest-priority findings: asset name, vuln class, severity, exploit availability (when data present)

Rollback status is always visible on every CR surface throughout the application — not a detail view, not a tooltip. A persistent indicator.

---

## Projects (AI-Assisted Campaigns)

For operators who want to define a goal and let the AI figure out what CRs are needed.

**Workflow:**
1. Operator clicks "New Project," names it, optionally describes the goal
2. Chat interface opens — operator describes the goal in natural language (e.g., "harden all Windows servers in production that have unmitigated criticals")
3. Operator provides asset context (can paste asset IDs, filter results, or attach a saved asset view)
4. AI returns a structured plan: list of proposed CRs with change type, target asset, parameters, estimated risk level
5. Operator reviews the plan — can remove, reorder, or edit individual CRs before accepting
6. Operator submits the project — CRs are created and enter the approval workflow based on risk level
7. Project view tracks all CRs: pending approval, executing, completed, rolled back

**Project states:** Draft → Active → Completed / Partially Rolled Back

**Rollback:** Each CR in a project has its own rollback status. A project-level rollback action triggers rollback on all completed CRs in reverse order (where supported).

---

## Assets

Primary surface for proactive hardening and one-off actions.

**Views:**
- List view (default) — sortable by risk score, asset type, environment, finding count
- Detail view — single asset: properties, findings, CR history, rollback history

**Filters:**
- Severity tier (critical/high/medium/low)
- Unmitigated findings only (default on in vuln preset)
- Vulnerability class (RCE, command injection, LPE, auth bypass, info disclosure, DoS)
- Public exploit available — shown when scanner data includes this field; sourced from scanner findings and/or CISA KEV cross-reference (KEV enrichment is a backlog item)
- Asset exposure (public-facing vs. internal)
- Environment (prod/staging/dev)
- Asset type (Windows server, Linux, cloud resource, AD user, container, etc.)
- CVE search
- CVSS score range
- Finding age (unmitigated for > N days)

**Saved filter presets** (ship as defaults):
- "Unmitigated Criticals" — severity=critical, unmitigated=true
- "Public Exploit Available" — public exploit=true, unmitigated=true (only visible if connector provides exploit data)
- "Public-Facing High+" — exposure=public, severity=critical|high, unmitigated=true

**One-off CR creation:** From any asset detail view, a prominent "Create Change Request" button opens a minimal form: change type, parameters, confirm. Zero friction — sensible defaults, operator refines as needed.

**Batch actions:** Operator selects multiple assets from the list and applies the same change type across all. Creates one CR per asset (or a grouped CR if the executor supports batch). Submitted together and tracked as a group.

---

## Change Requests

Unified tracker for all CRs regardless of origin (project, one-off, runbook execution, scheduled operation).

**Columns:** CR name, origin (project name or "one-off"), asset, change type, status, approver, rollback status, created at

**Status tabs:** Pending Approval | Executing | Completed | Rolled Back | All

**Approval flow (risk-based):**
- Low-risk CRs: operator who created it can self-approve and execute
- High-risk CRs: requires a second approver (different user, approver role)
- Risk level is determined by change type definition and asset exposure (configurable in Settings)

**Rollback:** Every completed CR shows rollback status. One-click rollback available where supported. Rollback produces a new CR in the tracker with its own audit trail.

---

## Operations

Planned and repeatable work — distinct from the bespoke AI-generated project.

**Runbooks:** Pre-defined playbooks (sequences of change types) validated by the operator team. Executing a runbook against a set of assets creates CRs in the Change Requests tracker. Runbooks are authored in a step editor (RunbookEditor) and versioned.

**Scheduled Operations:** Recurring CRs — e.g., "rotate credentials on these 10 servers every 90 days." Each scheduled operation produces a CR at trigger time that goes through normal approval flow.

**Maintenance Windows:** Time-based rules that gate when CRs can execute (e.g., "no production changes Friday 5pm–Monday 9am"). Configured here and in Settings.

---

## Connectors

Top-level nav. Operators add, edit, and test data source connections here.

Each connector card shows: connector type, status (connected/error/syncing), last sync time, asset count, and a "Test Connection" action.

New connectors are added via a type-selector wizard that collects credentials and stores them in the platform database. Credentials never leave the backend.

---

## Approval Policies & Risk Thresholds

Configured in Settings. Determines:
- Which change types are low-risk (self-approve) vs. high-risk (peer approval required)
- Whether asset exposure (public-facing) escalates risk tier
- Whether certain change types require a maintenance window

---

## Emotional Design Goals

The UI should simultaneously deliver:
- **Confidence** — "I know exactly what's running and what state it's in" (asset inventory, exposure summary)
- **Control** — "I can act quickly and undo anything I do" (rollback always visible, one-click rollback)
- **Clarity** — "I understand my exposure and what the right next action is" (filter presets, risk tiering, AI plan review)
- **Accountability** — "I have a full record of what happened and who approved it" (CR tracker, rollback audit trail)

---

## Backlog Items (Out of Scope for This Design)

- **CISA KEV enrichment** — cross-reference CVEs against the KEV feed independently of scanner data to provide exploit-in-the-wild signal even when scanner doesn't include it
- **EPSS score integration** — probabilistic exploit likelihood scoring
- **Project-level rollback** — coordinated rollback across all CRs in a project in reverse order
- **Batch CR grouping** — grouped CR for executors that support native batch operations
