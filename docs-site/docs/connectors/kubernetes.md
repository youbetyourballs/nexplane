# Kubernetes Connector

The Kubernetes connector uses the official Python Kubernetes client to apply changes to cluster resources, with a focus on security-relevant operations such as RBAC management and workload patching.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `kubeconfig_yaml` | Yes | Full contents of a kubeconfig file granting access to the target cluster |
| `context` | No | Kubeconfig context to use; defaults to the current-context in the file |
| `namespace` | No | Default namespace for namespace-scoped operations; can be overridden per CR |

The kubeconfig's service account or user must have RBAC permissions appropriate for the change types you intend to use.

## Supported change types

### `patch_resource`

Applies a strategic merge patch to any Kubernetes resource.

| Parameter | Type | Description |
|---|---|---|
| `api_version` | string | Resource API version (e.g. `apps/v1`) |
| `kind` | string | Resource kind (e.g. `Deployment`) |
| `name` | string | Resource name |
| `namespace` | string | Namespace (omit for cluster-scoped resources) |
| `patch` | object | JSON/YAML patch body to apply |

**Rollback**: Restores the full resource spec captured before patching.

---

### `delete_cluster_role_binding`

Deletes a ClusterRoleBinding, removing the associated role grant from all subjects.

| Parameter | Type | Description |
|---|---|---|
| `name` | string | Name of the ClusterRoleBinding to delete |

**Rollback**: Re-creates the ClusterRoleBinding using the full spec captured before deletion.

---

### `delete_role_binding`

Deletes a namespaced RoleBinding.

| Parameter | Type | Description |
|---|---|---|
| `name` | string | Name of the RoleBinding |
| `namespace` | string | Namespace containing the RoleBinding |

**Rollback**: Re-creates the RoleBinding from the captured spec.
