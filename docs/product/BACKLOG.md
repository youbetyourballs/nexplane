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

## Ongoing

Every backlog item should be evaluated against:

1. Does it make infrastructure changes safer?
2. Does it improve recoverability?
3. Does it improve verification?
4. Can Nexplane dogfood it?
5. Can it be validated against real infrastructure?
