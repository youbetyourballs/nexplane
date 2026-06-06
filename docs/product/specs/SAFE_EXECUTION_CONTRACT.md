# Safe Execution Contract

## Purpose

This specification defines the minimum safety contract Nexplane should enforce before it can credibly operate as a control plane for safe infrastructure change.

It turns the audit findings into implementation requirements for Claude Code or Codex.

## Scope

This spec covers:

1. Agent job ownership and result integrity
2. Real verification instead of default mock success
3. Truthful rollback state reporting
4. Production secret fail-closed behavior
5. A unified execution service for manual, scheduled, recurring, maintenance-window, and future AI-assisted execution

## Current audit findings

### Agent result binding is too broad

Agent API calls currently authenticate with an organization-level secret. Polling validates organization and agent registration, but result submission validates the organization and job only. A stronger model should prove that the posting agent is the same agent assigned to the job.

### Verification defaults to success

The current preflight and verification services can return passing results without checking real infrastructure. This is acceptable for early development, but not for the safe execution contract.

### Rollback outcome is too coarse

Rollback step failures can be embedded in rollback results while the workflow still marks the change request as rolled back. The final state must distinguish clean rollback from partial or failed rollback.

### Production defaults should fail closed

Development secret defaults must not be accepted outside local development.

### Execution paths should be centralized

Manual execution, scheduled execution, recurring jobs, maintenance-window release, and future AI-assisted execution should all call one execution service so approval, freeze windows, verification, audit logging, and safety gates stay consistent.

## Design principles

Follow `docs/product/ARCHITECTURE_PRINCIPLES.md`.

Especially:

- Nexplane must dogfood Nexplane wherever safe.
- Mock-only validation is insufficient.
- Real infrastructure smoke tests are required for infrastructure-changing behavior.
- Every change should move toward pre-state, execution, verification, rollback, and audit evidence.
- Tenant isolation is non-negotiable.
- Approval comes before automation.
- AI proposes; Nexplane executes.

## Target lifecycle

A safe change should follow this lifecycle:

1. Request
2. Plan
3. Safety review
4. Approval
5. Preflight
6. Pre-state capture
7. Execute
8. Post-state verification
9. Complete or roll back
10. Rollback verification when rollback occurs
11. Audit evidence

A change request should not be marked completed just because commands returned without error.

A change request should not be marked rolled back unless rollback succeeded or the system records the result as partial or failed.

---

# 1. Agent job ownership and result integrity

## Requirement

Each job result must be bound to the exact agent registration assigned to that job.

## Desired model

The backend should validate:

- caller belongs to the organization
- caller identifies an agent registration
- job belongs to that exact agent registration
- job is not already terminal
- submitted result is signed or otherwise bound to that agent

## Suggested implementation

Registration may still use the organization enrollment secret, but successful registration should issue or derive an agent-specific credential.

Store or derive:

- `agent_registration_id`
- `agent_secret_hash` or encrypted agent secret
- `agent_secret_created_at`
- `agent_secret_rotated_at`
- `agent_secret_version`

Agent-specific credentials should be used for:

- polling jobs
- verifying job signatures
- posting job results

## Result submission payload

Include:

- `agent_id`
- `job_id`
- `status`
- `result`
- `error`
- `submitted_at`
- `result_signature`

The signature should cover a canonical representation of:

- job id
- agent id
- status
- result body
- error body
- submitted timestamp

## Rejections

Reject:

- job result from the wrong agent
- duplicate result for terminal job
- invalid result signature
- stale result timestamp outside tolerance
- result for job outside organization

## Acceptance criteria

- An agent cannot submit results for another agent's job.
- A job cannot be completed twice.
- Invalid result signatures are rejected.
- Cross-organization result submission is rejected.
- Tests cover all above cases.

---

# 2. Verification framework

## Requirement

No infrastructure-changing change request should reach `completed` solely through default mock verification.

## Verification statuses

Use explicit status values:

- `passed`
- `failed`
- `unsupported`
- `manual_required`
- `skipped_development_only`

## Desired model

Each change type should declare verification behavior:

- pre-state capture support
- post-state verification support
- rollback verification support
- smoke test support

Unsupported verification should not be silently treated as success.

## Completion semantics

If verification is unsupported or manual:

- CR should not be marked fully completed as if verified
- use a distinct state such as `needs_manual_verification` or equivalent execution result metadata
- audit event must explain why verification was not automatic

## Suggested implementation

Create a verification registry keyed by change type and/or executor action.

Each verifier should implement:

- `capture_pre_state(context)`
- `verify_post_state(context, execution_result)`
- `verify_rollback(context, rollback_result)`

Start with a small set of real verifiers for existing smoke-testable paths.

## Acceptance criteria

- Default verification cannot mark production infrastructure changes as verified.
- Unsupported verification is explicit.
- At least one real verifier exists for a controlled smoke-test path.
- Audit events record verification status and evidence.
- Tests cover passed, failed, unsupported, and manual-required verification outcomes.

---

# 3. Rollback state machine

## Requirement

Rollback outcomes must be truthful.

## Required terminal states or result classifications

Represent at least:

- `rolled_back`
- `rollback_partial`
- `rollback_failed`
- `manual_recovery_required`

Exact enum names may follow existing code style, but the distinction must exist.

## Rules

- If all rollback steps succeed and rollback verification passes, mark cleanly rolled back.
- If some rollback steps fail, mark partial rollback.
- If rollback cannot run, mark rollback failed or manual recovery required.
- If rollback verification is unsupported, record that explicitly.

## Acceptance criteria

- A failed rollback step cannot produce a clean rolled-back final state.
- Rollback result includes per-step outcome.
- Rollback verification status is recorded.
- Audit events distinguish clean, partial, failed, and manual recovery outcomes.
- Tests cover each rollback outcome.

---

# 4. Production secret fail-closed behavior

## Requirement

Production-like environments must not boot with development signing or webhook secrets.

## Rules

If `ENVIRONMENT` is not development/local/test, startup should reject:

- default `SECRET_KEY`
- default `WEBHOOK_SECRET`
- weak or placeholder secrets

## Acceptance criteria

- Development still works with local defaults.
- Production/staging fails fast on defaults.
- Error message identifies the invalid setting without printing secret values.
- Tests cover development allowed and production rejected cases.

---

# 5. Unified execution service

## Requirement

All execution paths should call one service.

## Proposed service

Create a service such as:

`ChangeExecutionService.start(change_request_id, actor_id, source, options)`

Sources may include:

- `manual`
- `scheduled`
- `recurring_job`
- `maintenance_window_release`
- `api`
- `ai_assisted`

## Service responsibilities

The service should enforce:

- organization scope
- current status is executable
- approvals are satisfied
- freeze windows
- maintenance windows
- recurring-job policy constraints
- execution run creation
- audit event creation
- workflow start

## Acceptance criteria

- Manual execution uses the service.
- Scheduled execution uses the service.
- Recurring jobs use the service.
- No path triggers execution by directly importing a missing or alternate workflow trigger.
- Tests prove unapproved CRs cannot execute through any path.

---

# 6. Recurring-job approval policy

## Requirement

Recurring jobs should not auto-approve arbitrary generated change requests only because a job owner exists.

## Desired model

Recurring jobs should reference a pre-approved policy with:

- approved_by
- approved_at
- expires_at
- allowed change types
- allowed assets or asset tags
- max risk level
- required verification behavior
- emergency override behavior

Each generated CR must fit the policy or return to normal approval flow.

## Acceptance criteria

- Recurring job cannot auto-approve outside its policy.
- Policy expiry is enforced.
- Critical risk changes are not auto-executed unless explicitly allowed by a future high-assurance policy.
- Audit event links generated CR to the policy used.

---

# 7. Smoke tests

## Requirement

This work must include real or controlled smoke testing, not only mocks.

## Minimum smoke tests

At least one smoke path should prove:

- a real agent can register
- agent receives only its own job
- wrong agent cannot submit result
- correct agent can submit result
- verification result affects CR final state

A second smoke path should prove either:

- rollback partial state is represented correctly, or
- production secret fail-closed behavior rejects unsafe configuration

## Safety constraints

Smoke tests must use only:

- sandbox infrastructure
- Nexplane-owned infrastructure
- local controlled services
- explicitly marked test resources

No destructive test should target production resources.

---

# Implementation order

## Phase 1: Secret fail-closed

Smallest low-risk hardening change.

## Phase 2: Agent result binding

Add per-agent result ownership and duplicate terminal result rejection.

## Phase 3: Rollback state semantics

Add truthful rollback outcomes.

## Phase 4: Verification framework

Replace mock-pass default with explicit verification statuses.

## Phase 5: Unified execution service

Route manual, scheduled, recurring, and future execution paths through one service.

## Phase 6: Recurring-job approval policy

Constrain recurring auto-approval to pre-approved policy boundaries.

---

# Claude/Codex implementation instruction

Implement this spec incrementally. Do not attempt a single giant rewrite.

For each phase:

1. Make the smallest coherent change.
2. Add tests for the phase acceptance criteria.
3. Preserve existing behavior where safe.
4. Do not weaken tenant isolation.
5. Do not mark mock-only behavior as complete.
6. Update docs if behavior changes.

If the existing code conflicts with this spec, document the conflict before choosing an implementation path.

# Definition of done

The safe execution contract is done when:

- agent job results are bound to the assigned agent
- production defaults fail closed
- rollback states distinguish clean, partial, failed, and manual recovery
- verification cannot silently mock-pass production infrastructure changes
- all execution paths use one execution service
- recurring auto-approval is policy-bound
- tests and smoke tests cover the critical safety paths
