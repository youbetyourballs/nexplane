# Onboarding Wizard FILO Rollback + §2 Fix — Design Spec

## Goal

Fix the `provision_instance` executor so it only marks customers `STATE_ACTIVE` after the provisioned instance is verified reachable, then prove the full onboarding wizard (create_customer → provision_instance → generate_setup_token) works end-to-end with FILO rollback via a live smoke test against real AWS infrastructure.

## Background

The onboarding wizard already submits a single `catalog_workflow` CR with three steps. All three catalog JSON files have `rollback_action` declarations. All three executors have `rollback()` methods. The gap is:

1. **§2 bug**: `provision_instance/execute.py` calls `_record_provisioned()` (marking the customer `STATE_ACTIVE`) immediately after `provisioner.provision()` returns, without verifying the EC2 instance is actually serving. If the instance never becomes healthy, the customer is permanently stuck in `STATE_ACTIVE` with a dead instance and rollback is the only recovery path — but rollback won't fire automatically because the CR already succeeded from the executor's perspective.

2. **No live smoke**: The full wizard flow (forward + FILO rollback) has never run live. The rollback guarantee is unverified.

## Architecture

### §2 Fix: Health Check in provision_instance Executor

**File:** `nexplane-deploy/executors/provision_instance/execute.py`

After `provisioner.provision()` returns an `instance_id`, the executor stashes `instance_id` in CR step state (so rollback has it), then enters a poll loop:

- Probe: TCP connect to port 443 on the instance's Tailscale IP (or public IP for managed instances), OR `GET /api/v1/health` if the instance exposes it
- Interval: 10 seconds
- Timeout: 300 seconds (configurable via `PROVISION_HEALTH_TIMEOUT_SECS` env var)
- On success (probe responds): call `_record_provisioned()`, CR step completes
- On timeout: raise `ProvisioningHealthCheckTimeout`; CR step fails; FILO rollback fires `terminate_instance` automatically

**Only applies when `delivery_model == "managed"`** — self-hosted instances are not EC2-provisioned and have no Tailscale IP to probe until the customer configures it manually.

The `instance_id` must be written to CR step state before the poll loop begins, so that even if the health check times out and rollback fires, `terminate_instance` can find the instance to terminate.

### Smoke Test

**File:** `nexplane-deploy/tests/smoke/test_onboarding_wizard_smoke.py`

Three phases, all using the platform's existing AWS connector (no manual infra setup):

#### Phase 1 — Happy path

1. Submit a `catalog_workflow` CR with steps: `create_customer` → `provision_instance` → `generate_setup_token`. Use a unique slug per run (`wizard-smoke-{timestamp}`).
2. Approve via `POST /change-requests/{id}/approve`.
3. Poll until `STATE_COMPLETE`.
4. Assert:
   - Customer record exists and is in `STATE_ACTIVE`
   - EC2 instance with tag `ManagedBy=nexplane` and `ClientId={slug}` is running and reachable on port 443
   - Setup token row exists (verified via CR result or GET /setup-tokens)

#### Phase 2 — FILO rollback

1. Submit a second wizard CR (different slug), approve it, wait until `provision_instance` step completes (customer in `STATE_ACTIVE`, EC2 running).
2. Trigger rollback: `POST /change-requests/{id}/rollback`.
3. Poll until rollback completes.
4. Assert rollback order was FILO (reverse of forward):
   - Setup token revoked (if it was generated before rollback was triggered — check token is invalid)
   - EC2 instance terminated (verify via AWS `describe_instances`)
   - Customer record deleted or purged

#### Phase 3 — §2 regression guard

1. Submit a wizard CR using a valid AMI but with `PROVISION_HEALTH_TIMEOUT_SECS=30` (set in the smoke environment to keep the test fast) and provision the instance into a security group that blocks inbound port 443 from the platform. The instance launches but the health check can never succeed.
2. Approve and poll.
3. Assert:
   - CR reaches `STATE_FAILED` (not `STATE_COMPLETE`)
   - Customer is NOT in `STATE_ACTIVE`
   - Any EC2 instance created is terminated by automatic rollback

**Cleanup:** A `finally` block in each phase terminates any leaked EC2 instances (by tag) and deletes the test customer record, so a failed run doesn't leave debris.

## Data Flow

```
Wizard CR submitted (catalog_workflow)
  └─ Step 1: create_customer       → customer row in DB (STATE_PENDING)
  └─ Step 2: provision_instance    → EC2 launched, health check polls 443
       └─ health check passes      → customer STATE_ACTIVE, step completes
       └─ health check times out   → step fails, rollback fires:
            └─ rollback Step 2: terminate_instance  → EC2 terminated
            └─ rollback Step 1: delete_customer     → customer row deleted
  └─ Step 3: generate_setup_token  → token row in setup_tokens

Rollback (FILO):
  └─ rollback Step 3: revoke_setup_token  → token invalidated
  └─ rollback Step 2: terminate_instance  → EC2 terminated
  └─ rollback Step 1: delete_customer     → customer row deleted
```

## Error Handling

- **Health check timeout**: Raise `ProvisioningHealthCheckTimeout`. CR step fails. Platform's existing FILO rollback machinery fires `terminate_instance` automatically.
- **`terminate_instance` rollback fails**: Log and surface as rollback error; do not swallow. The EC2 instance ID is in CR state for manual cleanup.
- **Smoke infra leak**: `finally` blocks in smoke terminate tagged EC2 instances and delete test customer records regardless of phase outcome.

## Testing

- Phase 1 verifies forward execution
- Phase 2 verifies FILO rollback
- Phase 3 is the regression guard for §2 specifically

All phases run against live AWS. No mocks. Smoke runs from EC2 runner on Tailscale.

## Files Changed

| File | Change |
|------|--------|
| `nexplane-deploy/executors/provision_instance/execute.py` | Add health check loop before `_record_provisioned()` |
| `nexplane-deploy/tests/smoke/test_onboarding_wizard_smoke.py` | New file — 3-phase wizard smoke |

## Out of Scope

- Changes to `Customers.tsx` (frontend already uses `catalog_workflow`)
- Changes to catalog JSON rollback_action fields (already in place)
- Changes to `create_customer` or `generate_setup_token` executors (rollback methods already correct)
- Self-hosted delivery model health checking (separate concern)
