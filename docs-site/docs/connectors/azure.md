# Azure Connector

The Azure connector uses the Microsoft Graph API and Azure Resource Manager to manage Entra ID (formerly Azure AD) user state and role assignments.

## Credential fields

| Field | Required | Description |
|---|---|---|
| `tenant_id` | Yes | Azure AD tenant ID (GUID) |
| `client_id` | Yes | Application (client) ID of the registered app |
| `client_secret` | Yes | Client secret for the registered app |
| `subscription_id` | No | Azure subscription ID — required for subscription-scoped RBAC operations |

### Required API permissions (Microsoft Graph)

The registered Entra app must have the following **application** permissions (not delegated):

- `User.ReadWrite.All` — to disable/enable users
- `RoleManagement.ReadWrite.Directory` — to manage role assignments

Grant admin consent after assigning these permissions.

## Supported change types

### `disable_entra_user`

Sets the `accountEnabled` property to `false` on an Entra ID user, blocking sign-in.

| Parameter | Type | Description |
|---|---|---|
| `user_principal_name` | string | UPN of the user (e.g. `alice@example.com`) |

**Rollback**: Sets `accountEnabled` back to `true`.

---

### `enable_entra_user`

Re-enables an Entra ID user whose account has been disabled.

| Parameter | Type | Description |
|---|---|---|
| `user_principal_name` | string | UPN of the user |

**Rollback**: Disables the user account again.

---

### `remove_role_assignment`

Removes an Azure RBAC role assignment at subscription or resource group scope.

| Parameter | Type | Description |
|---|---|---|
| `principal_id` | string | Object ID of the user, group, or service principal |
| `role_definition_name` | string | Role name (e.g. `Contributor`, `Reader`) |
| `scope` | string | ARM scope path (e.g. `/subscriptions/{id}` or `/subscriptions/{id}/resourceGroups/{rg}`) |

**Rollback**: Re-creates the role assignment with the same principal, role, and scope.
