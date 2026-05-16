# Change Types

A **change type** is a named, parameterized operation that a connector can perform. Every change type in Nexplane has three required components:

1. **Parameter schema** — typed input fields with validation rules
2. **Execution logic** — the steps to apply the change, including before-state capture
3. **Rollback logic** — the steps to reverse the change using the captured snapshot

## Finding available change types

In the UI, navigate to **Settings → Change Types** to see the full list of registered change types, their connector, parameter schema, and rollback behavior.

When creating a change request, the change type dropdown is filtered to types supported by the connector accounts you have configured.

## Change type reference by connector

| Change Type | Connector | Description |
|---|---|---|
| `disable_iam_user` | AWS | Disable an IAM user's console and API access |
| `enable_iam_user` | AWS | Re-enable a disabled IAM user |
| `rotate_iam_key` | AWS | Rotate an IAM user's access key |
| `block_s3_public_access` | AWS | Enable all S3 bucket public access blocks |
| `attach_iam_policy` | AWS | Attach a managed IAM policy to a user or role |
| `detach_iam_policy` | AWS | Detach a managed IAM policy from a user or role |
| `disable_service_account` | GCP | Disable a GCP service account |
| `enable_service_account` | GCP | Re-enable a GCP service account |
| `remove_iam_binding` | GCP | Remove a project IAM role binding |
| `disable_entra_user` | Azure | Disable an Entra ID user |
| `enable_entra_user` | Azure | Re-enable an Entra ID user |
| `remove_role_assignment` | Azure | Remove an Azure RBAC role assignment |
| `patch_resource` | Kubernetes | Strategic merge patch a Kubernetes resource |
| `delete_cluster_role_binding` | Kubernetes | Delete a ClusterRoleBinding |
| `delete_role_binding` | Kubernetes | Delete a namespaced RoleBinding |
| `rotate_secret` | Vault | Rotate a KV v2 secret |
| `disable_user` | LDAP | Disable an LDAP/AD user account |
| `lock_role` | PostgreSQL | Prevent a PostgreSQL role from logging in |
| `revoke_privilege` | PostgreSQL | Revoke a privilege from a PostgreSQL role |
| `lock_linux_user` | SSH | Lock a local Linux user account |
| `disable_local_user` | WinRM | Disable a local Windows user account |

## Rollback guarantee

Every change type that modifies state must implement a rollback handler. If a change type cannot provide a rollback guarantee (e.g. because the operation is inherently irreversible), this is documented on the connector page and the CR detail will show **Rollback: Not available**.
