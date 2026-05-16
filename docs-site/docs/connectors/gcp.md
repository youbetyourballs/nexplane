# GCP Connector

The GCP connector uses the Google Cloud Python SDK to manage service account state and IAM bindings within a GCP project.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `project_id` | Yes | GCP project ID |
| `service_account_key_json` | Yes | Full contents of a GCP service account key JSON file |

### Minimum IAM roles

Assign the following predefined roles to the Nexplane service account at the project level:

- `roles/iam.serviceAccountAdmin` — to disable/enable service accounts
- `roles/resourcemanager.projectIamAdmin` — to manage IAM policy bindings

For least-privilege deployments, create a custom role containing only the specific permissions needed.

## Supported change types

### `disable_service_account`

Disables a GCP service account, preventing it from authenticating to any Google API.

| Parameter | Type | Description |
|---|---|---|
| `email` | string | Service account email (e.g. `sa-name@project.iam.gserviceaccount.com`) |

**Rollback**: Re-enables the service account.

---

### `enable_service_account`

Re-enables a disabled GCP service account.

| Parameter | Type | Description |
|---|---|---|
| `email` | string | Service account email |

**Rollback**: Disables the service account again.

---

### `remove_iam_binding`

Removes a member from a role binding on the project IAM policy.

| Parameter | Type | Description |
|---|---|---|
| `member` | string | Member identifier (e.g. `user:alice@example.com`, `serviceAccount:sa@project.iam.gserviceaccount.com`) |
| `role` | string | IAM role to remove (e.g. `roles/editor`) |

**Rollback**: Restores the binding.
