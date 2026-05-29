# Credential Revocation Live Smoke Test + AWS Reconstitution Rollback Design

## Goal

Add a live smoke phase that proves credential revocation CRs execute and roll back correctly across all 5 supported connectors (GCP, Azure AD, LDAP/AD, OCI, AWS), using throwaway accounts created and destroyed within the test. Simultaneously enhance the AWS `revoke_exposed_credential` executor to support reconstitution rollback — saving user state before deleting the key and re-provisioning equivalent access on rollback.

## Architecture

Two deliverables implemented together:

1. **AWS executor enhancement** — `revoke_exposed_credential.py` gains reconstitution rollback for the `aws_iam_key` credential type
2. **Smoke phase** — `phase_credential_revocation_live` added to `test_feature_smoke_live.py`

The smoke phase validates both deliverables end-to-end. All 5 sub-phases follow the same pattern: create throwaway account → submit CR via `NexplaneClient` → auto-approve → execute → verify disabled → rollback → verify restored → delete throwaway.

Existing connector credentials are never modified. Connector IDs are referenced by value; credentials are loaded internally by the platform via `_attach_credentials`.

## Deliverable 1: AWS Reconstitution Rollback

**File:** `backend/app/connectors/executors/aws/revoke_exposed_credential.py`

**Current behavior (aws_iam_key path):** Calls `delete_access_key(AccessKeyId=...)`, returns `rollback_available: False`.

**New behavior:**

Before deletion, call `GetAccessKeyLastUsed` to resolve the owning IAM username. Save into `rollback_params`:
```python
rollback_params = {
    "credential_type": "aws_iam_key",
    "username": username,        # IAM username owning the key
    "user_arn": user_arn,        # full ARN for audit trail
}
```

Return from execute: `rollback_available: True`, `rollback_type: "reconstitution"`.

**Rollback function:** Call `create_access_key(UserName=rollback_params["username"])`. Return new `access_key_id` and `secret_access_key` in result. The new key is functional immediately; the compromised key is permanently gone. This is intentional — "restored access" not "undone deletion."

**Other credential types** (`vault_token`, `gcp_service_account_key`, `azure_client_secret`, `ldap_account`) remain `rollback_available: False` — reconstitution for those is either handled by their own connector's dedicated executors or is out of scope for this phase.

## Deliverable 2: Smoke Phase

**File:** `backend/tests/smoke/test_feature_smoke_live.py`

New phase: `phase_credential_revocation_live`

### Connector IDs (live, verified)
- GCP: `c91d563c-...`
- Azure AD: `356cc5eb-...`
- AWS: `666e237d-...`
- OCI: `0b3cf029-...`
- AD/LDAP: `d9fd42c5-...`

### Sub-phase: GCP

**Executor:** `disable_service_account` (rollback: native ✅)

1. Create temp SA via GCP SDK: `nexplane-smoke-{uuid}@{project}.iam.gserviceaccount.com`
2. Submit CR: `action_id="disable_service_account"`, `connector_id=GCP_CONNECTOR_ID`, `parameters={"service_account_id": temp_sa}`
3. Auto-approve via `client.approve_change_request(cr_id)`
4. Poll until `status == "completed"`
5. Verify: `serviceAccounts.get()` returns `disabled: true`
6. Rollback: `client.rollback_change_request(cr_id)`
7. Poll until `status == "rolled_back"`
8. Verify: `serviceAccounts.get()` returns `disabled: false`
9. Cleanup: `serviceAccounts.delete(temp_sa)`

### Sub-phase: Azure AD

**Executor:** `azure_ad_disable_user` (rollback: native ✅)

1. Create temp user via MS Graph API: `nexplane-smoke-{uuid}@{tenant}`
2. Submit CR: `action_id="azure_ad_disable_user"`, `connector_id=AZURE_CONNECTOR_ID`, `parameters={"user_id": temp_user_id}`
3. Auto-approve → execute → poll
4. Verify: Graph `GET /users/{id}` returns `accountEnabled: false`
5. Rollback → poll
6. Verify: `accountEnabled: true`
7. Cleanup: Graph `DELETE /users/{id}`

### Sub-phase: LDAP/AD

**Executor:** `ldap_disable_user` (rollback: native ✅)

1. Create temp user via `LDAPClient.create_user(username="nexplane-smoke-{uuid}", ...)`
2. Submit CR: `action_id="ldap_disable_user"`, `connector_id=AD_CONNECTOR_ID`, `parameters={"username": temp_username}`
3. Auto-approve → execute → poll
4. Verify: `LDAPClient.verify_bind(username, password)` raises auth error
5. Rollback → poll
6. Verify: `verify_bind` succeeds
7. Cleanup: `LDAPClient.delete_user(temp_username)`

### Sub-phase: OCI

**Executor:** `disable_iam_user` (rollback: native ✅ — captures `previous_can_use_console_password` / `previous_can_use_api_keys` before disabling)

1. Create temp IAM user via OCI SDK: `nexplane-smoke-{uuid}`
2. Submit CR: `action_id="disable_iam_user"`, `connector_id=OCI_CONNECTOR_ID`, `parameters={"user_id": temp_user_ocid}`
3. Auto-approve → execute → poll
4. Verify: `identity_client.get_user(user_ocid).data.lifecycle_state == "INACTIVE"`
5. Rollback → poll
6. Verify: `lifecycle_state == "ACTIVE"`
7. Cleanup: `identity_client.delete_user(temp_user_ocid)`

### Sub-phase: AWS

**Executor:** `revoke_exposed_credential` / `aws_iam_key` (rollback: reconstitution ♻️)

1. Create temp IAM user: `nexplane-smoke-{uuid}`
2. Create access key for temp user → capture `access_key_id`
3. Submit CR: `action_id="revoke_exposed_credential"`, `connector_id=AWS_CONNECTOR_ID`, `parameters={"credential_type": "aws_iam_key", "access_key_id": access_key_id}`
4. Auto-approve → execute → poll
5. Verify: `list_access_keys(UserName=temp_username)` shows key status `Inactive` or key absent
6. Rollback → poll
7. Verify: `list_access_keys(UserName=temp_username)` shows a new active key (different `access_key_id`)
8. Cleanup: `delete_user` (detach policies, delete keys, then delete user)

## Unit Tests

**File:** `backend/app/tests/test_revoke_exposed_credential.py` (new) or appended to existing executor tests

- `test_revoke_aws_iam_key_captures_rollback_params` — mock boto3, verify `rollback_params` contains `username` and `user_arn` after execute
- `test_revoke_aws_iam_key_rollback_creates_new_key` — mock boto3, verify `create_access_key` called with correct username, result contains new key ID
- `test_revoke_aws_iam_key_rollback_type_is_reconstitution` — verify execute result has `rollback_available: True`, `rollback_type: "reconstitution"`

## Rollback Semantics Note

The AWS sub-phase intentionally demonstrates reconstitution rollback behavior: after rollback, the user has a *new* access key, not the original. This is by design — the compromised credential is permanently deleted. Rollback restores the working state (authenticated access) not the byte-level state (original key). This matches the platform's core rollback guarantee: "system returns to a working state."

## Testing

Smoke phase passes = both deliverables proven live. Unit tests cover rollback logic in isolation. No mocks in smoke — must run against live AWS, GCP, Azure, OCI, and AD connectors on EC2 runner.
