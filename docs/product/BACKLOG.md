# Nexplane Backlog

> The **P0–P2** sections below are the strategic product themes. The
> **Platform & infrastructure** section tracks shipped platform components and
> their follow-ups (component-organized). Deployment/ops and commercial-overlay
> work — release pipeline, instance provisioning, operational-model providers,
> ops→customer access, and commercial *catalog metadata* — lives in the private
> deploy repo's backlog (`nexplane-deploy/docs/BACKLOG.md`). Rule: an item
> belongs where its code change lands; core-code items are here.
>
> **Status legend** (Platform & infrastructure): ✅ done · ◑ partial · ☐ not started · ❓ decision needed

## P0 - Strategic Foundations

### Infrastructure Digital Twin

Status: Not Started

Outcome:

Represent infrastructure as a graph of assets, identities, applications, certificates, DNS, cloud resources, policies, and relationships.

Enables:

- impact simulation
- blast radius analysis
- rollback intelligence
- AI planning
- dependency analysis

Requirements:

- follow architecture principles
- organization-scoped graph
- real infrastructure smoke tests

---

### Universal Verification Framework

Status: Partially Exists

Outcome:

Every change type can define:

- pre-state capture
- execution
- verification
- rollback

Goal:

Make verification a first-class capability instead of a per-feature implementation detail.

---

### Change Impact Simulator

Status: Not Started

Outcome:

Predict likely effects of a change before execution.

Examples:

- affected assets
- affected applications
- blast radius
- rollback confidence

Depends on:

- Infrastructure Digital Twin

---

## P1 - Intelligence Layer

### Confidence Engine

Status: Not Started

Outcome:

Generate:

- execution confidence
- rollback confidence
- risk confidence

Inputs:

- historical executions
- verification coverage
- dependency graph
- asset criticality

---

### Historical State Snapshots

Status: Not Started

Outcome:

Support last-known-good recovery and historical infrastructure inspection.

Examples:

- show infrastructure state at a point in time
- compare states
- rollback targeting

---

### Dynamic Approval Engine

Status: Not Started

Outcome:

Approval requirements become risk-driven instead of static.

Inputs:

- blast radius
- criticality
- affected systems
- confidence scores

---

## P2 - Autonomous Operations

### AI Change Planner

Status: Existing foundation

Outcome:

Convert intent into executable plans with risk analysis and rollback awareness.

---

### Executive Recoverability Dashboard

Status: Not Started

Outcome:

Provide leadership-level visibility into:

- recoverability
- automation coverage
- risk posture
- change success rates

---

## Platform & infrastructure

Shipped platform components and their remaining follow-ups. Deploy/overlay
counterparts (commercial catalog metadata, provisioning executors,
ops→customer access) live in `nexplane-deploy/docs/BACKLOG.md`.

### Agent reverse tunnel

- ✅ SOCKS5 relay + programmatic `manager.dial()` shipped (`TUNNEL_SOCKS_TOKEN`).
- ☐ Short-lived per-connection tunnel tokens (+ optional mTLS) for the WS handshake — hardening beyond the reused `/agent` HMAC scheme.
- ☐ WireGuard L3 option (full-subnet routing) — Phase 3, deferred by design.
- ❓ Single shared SOCKS5 (agent chosen via credentials) vs. one listener per agent — scaling architecture.
- ❓ Where the relay terminates — in the backend process vs. a dedicated relay service (HA/scaling).
- ❓ Allowlist source of truth — agent-enrollment field (current) vs. a policy object.
- ❓ Coexistence with the agent's Tailscale awareness (`changip_tailscale`) — tunnel vs. mesh selection.

### Tunnel UI & connector routing

- ☐ Extend HTTP routing to the remaining ~40 HTTP connector types — one-line each via the shared `tunnel_http_client` helper.
- ☐ Idle forwarder-listener reaping + per-agent concurrency/rate limits (listeners persist per process today).
- ☐ Per-connector audit of routed traffic (agent / destination / bytes) — the tunnel audits dials; connector-level correlation is a follow-up.
- ☐ Routing for public cloud APIs (AWS/Azure/GCP) — public internet, one-line-addable via the shared helper.
- ❓ Should `network_tls_skip_verify` require an extra confirmation / be policy-gated for auditors?

### Commercial UI seams (edition-gated; ships in the core bundle)

- ✅ First cut: generic `catalog_action` change type + discovery/run/capabilities APIs + schema-driven `CatalogActionForm` + edition-gated Customers page (v1.1.0). Inert unless the backend reports `edition=commercial`.
- ☐ `CatalogActionForm` widget set for guided onboarding — autocomplete-with-source, enum/select, field grouping, inline validation, and a review step (the wizard is a dense free-text form today). Pairs with richer catalog `param_schema` metadata tracked in the deploy backlog.
- ☐ Richer console rendering for the Customers/fleet page (versions, health, expiring grants) over the discovery/registry data.
- ❓ Approval-workflow gate for `catalog_action` CRs — operator-executed today; can layer the existing approval machinery if governance needs it.
- ❓ `ops_instance` / fleet-as-assets asset type — none today; a core data-model decision if fleet assets become desirable.
- ☐ Billing/entitlements UI — out of first-cut scope.

### Deployment integration (upstreaming generic capability)

- ☐ Move the *generic* deployment executors into the product (`backend/app/deployment/`) so a core instance can **execute** deployment CRs (today execution requires the commercial overlay to be mounted). Commercial specifics stay in the overlay.
- ❓ Generic deployment connector-type naming (`ops` vs `deployment`) and whether deployment change types are enum values vs. a free-form escape hatch (migration churn).

---

## Pre-open-source (MUST do before making this repo public)

### Purge leaked Tailscale auth key from git history

Status: Not Started — **blocker for open-sourcing**

A real Tailscale auth key (a `tskey-auth-…` value) was committed to this repo
(in `docker-compose.override.yml` and several `docs/superpowers/plans/*.md`,
first introduced in commit `75dfced`). As of 2026-07-02 the key has been removed
from the working tree/current commits — the override now reads `${TS_AUTHKEY}`
(supplied via a gitignored `.env`), and the docs are redacted — but **it still
exists in git history**.

Before the repo goes public:

1. **Rotate the key** — revoke the old key in the Tailscale admin console and
   issue a new one. Coordinate the cutover so the active EC2 hosts (nexplane-ops,
   nexplane-dev) don't lose tailnet connectivity: set the new key as `TS_AUTHKEY`
   in each host's local `.env`, then recreate the backend container.
2. **Scrub history** — `git filter-repo --replace-text` (or BFG) to purge the key
   string from every commit, then force-push and re-sync any live clones
   (ops `/opt/nexplane-src` is rebuilt from the release bundle; dev
   `/home/ec2-user/nexplane` would need a re-clone/reset).
3. **Scan** — run a secret scanner (gitleaks/trufflehog) over the full history to
   confirm no other secrets remain before publishing.

---

## Ongoing

Every backlog item should be evaluated against:

1. Does it make infrastructure changes safer?
2. Does it improve recoverability?
3. Does it improve verification?
4. Can Nexplane dogfood it?
5. Can it be validated against real infrastructure?
