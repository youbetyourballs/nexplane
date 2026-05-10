# OCI Connector — Sub-project 4: Identity Design

## Scope

OCI IAM users, groups, policies, compartment lifecycle CRs, and OCI Vault secrets. Follows patterns from sub-projects 1-3.

**Key design decisions:**
- IAM users → `identity` asset type (exact fit)
- IAM groups and policies → `application` asset type with tags `oci-iam-group` / `oci-iam-policy` (no dedicated AssetType; same approach as GCP service accounts)
- Compartment management CRs (`oci_compartment_create`, `oci_compartment_delete`) — compartments were discovery-only in sub-project 1; this adds the ability to create/delete them via CR
- OCI Vault secrets → `application` asset type tagged `oci-vault-secret` (closest fit; no dedicated secrets AssetType)
- IAM key rotation (API key rotate for IAM users) included here rather than sub-project 1 to keep compute focused

---

## New Change Types (10)

```
oci_iam_user_create         Create an OCI IAM user
oci_iam_user_delete         Delete an OCI IAM user
oci_iam_user_disable        Block/disable an OCI IAM user login
oci_iam_user_enable         Re-enable an OCI IAM user login
oci_iam_group_create        Create an IAM group
oci_iam_group_delete        Delete an IAM group
oci_iam_policy_create       Create an IAM policy
oci_iam_policy_delete       Delete an IAM policy
oci_vault_secret_create     Create a secret in OCI Vault
oci_vault_secret_delete     Delete a secret from OCI Vault
```

Plus 2 compartment lifecycle types:
```
oci_compartment_create      Create a child compartment
oci_compartment_delete      Delete a compartment (must be empty)
```

Total: 12 new change types, all added to `_IMPLICIT_ROLLBACK_TYPES`.

---

## New Files

```
backend/app/connectors/executors/oci/
  discover_iam_users.py
  discover_iam_groups.py
  discover_iam_policies.py
  discover_vault_secrets.py
  create_iam_user.py
  delete_iam_user.py
  disable_iam_user.py
  enable_iam_user.py
  create_iam_group.py
  delete_iam_group.py
  create_iam_policy.py
  delete_iam_policy.py
  create_vault_secret.py
  delete_vault_secret.py
  create_compartment.py
  delete_compartment.py

backend/alembic/versions/043_add_oci_identity_change_types.py
```

New OCI SDK clients in `_client.py`:
```python
def get_vault_client(creds) -> oci.vault.VaultsClient
def get_secrets_client(creds) -> oci.secrets.SecretsClient
def get_vaults_management_client(creds) -> oci.key_management.KmsVaultClient
```

Note: OCI Vault requires a Vault resource to exist in the compartment. The executors check for an existing vault and fail with a clear message if none is found (Vault creation is a separate heavyweight operation not included in this scope).

---

## Discovery

### `discover_iam_users.py`
- Calls `identity.list_users(tenancy_id)` — IAM users are tenancy-scoped, not compartment-scoped
- Each user → `identity` asset:
  ```json
  {
    "name": "<name>",
    "asset_type": "identity",
    "asset_metadata": {
      "user_id": "ocid1.user...",
      "email": "<email>",
      "lifecycle_state": "ACTIVE",
      "is_mfa_activated": false,
      "can_use_console_password": true,
      "can_use_api_keys": true,
      "provider": "oci"
    },
    "tags": ["oci", "iam-user"]
  }
  ```
- Dedup key: `user_id`

### `discover_iam_groups.py`
- Calls `identity.list_groups(tenancy_id)`
- Each group → `application` asset tagged `oci-iam-group`
- Metadata: `group_id`, `name`, `description`, `lifecycle_state`
- Dedup key: `group_id`

### `discover_iam_policies.py`
- Calls `identity.list_policies(compartment_id)`
- Each policy → `application` asset tagged `oci-iam-policy`
- Metadata: `policy_id`, `name`, `statements` (array), `compartment_id`
- Dedup key: `policy_id`

### `discover_vault_secrets.py`
- Calls `vault.list_secrets(compartment_id)` if a vault exists
- Each secret → `application` asset tagged `oci-vault-secret`
- Metadata: `secret_id`, `secret_name`, `vault_id`, `lifecycle_state` (value never stored)
- Dedup key: `secret_id`

---

## IAM User Executors

### `create_iam_user.py` (`oci_iam_user_create`)
**Parameters (all pre-populated):**
```
name           "nexplane-user"
description    "Created by Nexplane"
email          ""
```
Note: IAM users are tenancy-scoped. Target asset is the compartment (used to resolve tenancy_id).
Creates user, optionally adds to a group if `group_id` provided.
Returns `_auto_asset` (`identity`). Rollback: `oci_iam_user_delete`.

### `delete_iam_user.py` (`oci_iam_user_delete`)
- Removes user from all groups first, then calls `identity.delete_user(user_id)`
- Parameters: `user_id` (auto-populated from target identity asset)
- Rollback: none (destructive)

### `disable_iam_user.py` (`oci_iam_user_disable`)
- Calls `identity.update_user_capabilities()` to disable console login + API keys
- Stores previous capabilities in execution result for rollback
- Rollback: `oci_iam_user_enable`

### `enable_iam_user.py` (`oci_iam_user_enable`)
- Restores console login + API key capabilities
- Rollback: `oci_iam_user_disable`

---

## IAM Group Executors

### `create_iam_group.py` (`oci_iam_group_create`)
**Parameters (all pre-populated):**
```
name           "nexplane-group"
description    "Created by Nexplane"
user_ids       []
```
Creates group, adds specified users via `identity.add_user_to_group()`.
Returns `_auto_asset` (`application`, tagged `oci-iam-group`). Rollback: `oci_iam_group_delete`.

### `delete_iam_group.py` (`oci_iam_group_delete`)
- Removes all members first, then deletes group
- Parameters: `group_id` (auto-populated)
- Rollback: none (destructive)

---

## IAM Policy Executors

### `create_iam_policy.py` (`oci_iam_policy_create`)
**Parameters (all pre-populated):**
```
compartment_id   resolved from target compartment
name             "nexplane-policy"
description      "Created by Nexplane"
statements       ["Allow group nexplane-group to read all-resources in tenancy"]
```
Returns `_auto_asset` (`application`, tagged `oci-iam-policy`). Rollback: `oci_iam_policy_delete`.

### `delete_iam_policy.py` (`oci_iam_policy_delete`)
- Calls `identity.delete_policy(policy_id)`
- Rollback: none (destructive)

---

## Vault Executors

### `create_vault_secret.py` (`oci_vault_secret_create`)
**Parameters (all pre-populated):**
```
compartment_id   resolved from target compartment
vault_id         resolved from first ACTIVE vault in compartment
key_id           resolved from first ACTIVE master encryption key in vault
secret_name      "nexplane-secret"
secret_content   "changeme"   (base64-encoded by executor)
description      "Created by Nexplane"
```
Preflight: verify vault exists. If none, error: "No ACTIVE Vault in this compartment. Create a Vault in the OCI Console first."
Returns `_auto_asset` (`application`, tagged `oci-vault-secret`). Rollback: `oci_vault_secret_delete`.

### `delete_vault_secret.py` (`oci_vault_secret_delete`)
- Schedules deletion (OCI Vault uses deferred deletion — minimum 1 day)
- Parameters: `secret_id` (auto-populated), `deletion_time_days: 1`
- Rollback: cancel deletion if still in pending-deletion state

---

## Compartment Lifecycle Executors

### `create_compartment.py` (`oci_compartment_create`)
**Parameters (all pre-populated):**
```
parent_compartment_id   resolved from target compartment asset
name                    "nexplane-compartment"
description             "Created by Nexplane"
```
Returns `_auto_asset` (`cloud_account`, tagged `oci compartment`). Rollback: `oci_compartment_delete`.

### `delete_compartment.py` (`oci_compartment_delete`)
- Preflight: verify compartment has no resources (list instances, buckets, etc.)
- Calls `identity.delete_compartment(compartment_id)`
- Rollback: none (destructive)

---

## Frontend Wiring

### `api.ts`
12 new `ChangeType` values.

### `CreateChangeRequest.tsx`
"Oracle Cloud" category extended. Identity-related CRs grouped visually within the category (same approach as AWS IAM vs EC2 grouping).

### `AssetDetail.tsx`
- `identity` assets tagged `oci-iam-user`: quick actions disable, enable, delete
- `application` assets tagged `oci-iam-group`: quick action delete
- `application` assets tagged `oci-iam-policy`: quick action delete
- `application` assets tagged `oci-vault-secret`: quick action delete

---

## Smoke Test Phases

### OCI_K — IAM
1. Fire `oci_iam_user_create` → verify `identity` asset in inventory
2. Fire `oci_iam_group_create` → verify `application` (oci-iam-group) asset in inventory
3. Fire `oci_iam_policy_create` → verify `application` (oci-iam-policy) asset in inventory
4. Fire `oci_iam_user_disable` → OCI SDK verify user capabilities disabled
5. Fire `oci_iam_user_enable` → OCI SDK verify capabilities restored
6. Rollback: delete policy → delete group → delete user

### OCI_L — Vault (conditional — skipped if no vault exists in tenancy)
1. Check for ACTIVE vault; if none, print warning and skip
2. Fire `oci_vault_secret_create` → verify `application` (oci-vault-secret) asset in inventory
3. Fire `oci_vault_secret_delete` rollback → verify scheduled for deletion

---

## DB Migration (043)

```sql
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_user_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_user_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_user_disable';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_user_enable';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_group_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_group_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_policy_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_iam_policy_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_vault_secret_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_vault_secret_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_compartment_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_compartment_delete';
```
