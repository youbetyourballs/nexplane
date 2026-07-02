# Nexplane Backlog

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
