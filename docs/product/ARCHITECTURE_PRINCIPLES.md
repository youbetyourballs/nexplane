# Nexplane Architecture Principles

These principles govern product and engineering decisions for Nexplane. They are intentionally opinionated so Claude Code, Codex, and human contributors can make consistent implementation choices.

## 1. Nexplane Must Operate Nexplane

Nexplane should dogfood its own capabilities wherever safe.

New capabilities should eventually be able to help deploy, test, monitor, update, or recover Nexplane-managed infrastructure. A feature that cannot be exercised by Nexplane itself should explain why.

## 2. No Mock-Only Features

Unit tests are required, but they are not enough.

A feature is not complete until it has:

- unit tests
- integration tests where appropriate
- a smoke test against real, sandbox, or Nexplane-owned infrastructure when the feature touches infrastructure behavior

Mock-only completion is not acceptable for infrastructure-changing features.

## 3. Real Infrastructure Smoke Tests Required

When a feature claims to discover, change, verify, remediate, deploy, or recover infrastructure, it must include a smoke path against a controlled real environment.

The smoke path should be safe, repeatable, documented, and explicitly scoped to sandbox resources.

## 4. Every Change Must Be Recoverable

Every execution path should move toward this lifecycle:

1. capture pre-state
2. execute change
3. verify post-state
4. rollback or compensate if verification fails
5. record audit evidence

If rollback is not possible, the system must say so before execution.

## 5. Verification Before Success

A command completing is not the same as a change succeeding.

Nexplane should treat successful execution as provisional until post-state validation confirms the intended outcome.

## 6. Tenant Isolation First

Every data model, query, background job, graph traversal, connector sync, and agent result must be scoped to an organization or tenant boundary.

Cross-tenant leakage is a release blocker.

## 7. Approval Before Automation

Automation should not bypass governance.

Risk, blast radius, affected assets, rollback confidence, and policy constraints should determine whether a change can be auto-approved, requires human approval, or must be blocked.

## 8. AI Plans, Nexplane Executes

AI should propose plans, alternatives, risks, and explanations.

The deterministic Nexplane control plane should own authorization, approval, execution, verification, rollback, and audit logging.

## 9. Prefer Boring Foundations

Use simple, inspectable, well-tested primitives before introducing specialized infrastructure.

For example, start with PostgreSQL-backed graph adjacency tables before adopting a dedicated graph database unless scale or query requirements force the move.

## 10. Auditability Is Product Surface

Every important decision and infrastructure change should leave evidence:

- who requested it
- what was proposed
- what was approved
- what executed
- what changed
- what verification proved
- whether rollback was possible or performed

Audit evidence is not a secondary compliance artifact. It is part of the core product value.
