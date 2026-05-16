# Vault Connector

The Vault connector uses the HashiCorp Vault HTTP API to perform secrets management operations with full rollback support.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `url` | Yes | Vault server URL (e.g. `https://vault.internal:8200`) |
| `token` | Yes | Vault token with the required policies applied |
| `namespace` | No | Vault namespace (HCP Vault or Vault Enterprise only) |

### Required Vault policy

The token must have at minimum:

```hcl
path "secret/data/*" {
  capabilities = ["read", "create", "update"]
}

path "secret/metadata/*" {
  capabilities = ["read"]
}
```

Adjust the path prefix to match your secrets engine mount point.

## Supported change types

### `rotate_secret`

Writes a new version of a KV v2 secret at the specified path. The new secret value is either provided explicitly or auto-generated.

| Parameter | Type | Description |
|---|---|---|
| `path` | string | KV v2 secret path (e.g. `secret/myapp/db-password`) |
| `new_value` | string | New secret value; leave blank to auto-generate a secure random string |
| `key` | string | Key within the secret data map to rotate (e.g. `password`) |

**Rollback**: Reads the previous version number from the before-state snapshot and writes that version's data back as a new version, effectively restoring the prior value. Rollback requires that version history has not been deleted or truncated in Vault (`max_versions` must be greater than 1).

!!! warning
    If the secret engine is configured with `max_versions = 1`, the previous version is not retained and rollback will fail. Set `max_versions` to at least `2` on any path you manage through Nexplane.
