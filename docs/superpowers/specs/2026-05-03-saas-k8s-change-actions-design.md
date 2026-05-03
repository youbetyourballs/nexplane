# SaaS & Kubernetes Change Actions — Design Spec

**Date:** 2026-05-03
**Status:** Draft
**Scope:** Add write/change actions to five currently read-only connectors — Google Workspace, GitHub, Slack, Microsoft 365 / Entra ID, and Kubernetes. Each action follows the existing `get_executor` / `.execute(parameters, asset_ids, connector)` contract and integrates with the draft → planned → approved → executing → completed change-request workflow.

---

## Background

All Nexplane connectors today are discovery-only: they read data into the platform but cannot mutate the connected service. Security incidents and compliance remediations require the ability to act — suspend an account, revoke tokens, restart a workload, apply a policy. This spec adds change actions to five connectors without altering the existing discovery pipeline or the connector credentials model.

Each new action:
1. Implements a `BaseExecutor` subclass in the connector's `executors/` subdirectory.
2. Declares itself in the connector's catalog module so `get_executor` can resolve it.
3. Records pre-change state so the UI can surface a rollback option.
4. Returns a structured result (`ActionResult`) with `success`, `rollback_data`, and `message` fields.

---

## Design Decisions

- **Same executor contract, no new framework:** Actions use the identical `get_executor(connector_type, action_id)` → `.execute(parameters, asset_ids, connector)` pattern already in place. No new base classes or dispatch layers.
- **Credentials via SecretsService:** OAuth tokens (Google, GitHub, Slack, M365) and kubeconfigs (Kubernetes) are already stored encrypted in `SecretsService`. Executors call `SecretsService.get_secret(connector.credential_id)` and construct the appropriate client.
- **Pre-change snapshots for rollback:** Before mutating, each executor reads current state and stores it in `rollback_data` on the returned `ActionResult`. The change-request workflow persists this alongside the execution record so an operator can trigger a compensating action.
- **Rollback is a first-class action:** Where possible, rollback is implemented as a separate executor (e.g. `unsuspend_user` is the rollback for `suspend_user`). For actions without a clean inverse (e.g. `wipe_mobile_device`), `rollback_data` is `null` and the UI shows a warning.
- **Kubernetes auth from kubeconfig stored in credentials:** The connector credential payload includes a base64-encoded kubeconfig. The executor writes it to a temp file, creates a `kubernetes.client.ApiClient` from it, and deletes the temp file after. Service-account token auth (in-cluster) is also supported if `KUBERNETES_SERVICE_HOST` is set.
- **Helm actions use subprocess:** The `helm` CLI is already available in the backend container. `helm upgrade` and `helm rollback` are invoked via `subprocess.run` with `--kubeconfig` pointing to the temp file. Structured JSON output (`--output json`) is parsed for status.
- **No partial application:** Each action is atomic from the connector's perspective. If an action covers multiple resources (e.g. `remove_from_groups` iterates all groups), failures are collected and the action returns `success=False` with a partial result so the operator sees exactly what succeeded.
- **Dry-run support:** Every executor accepts an optional `dry_run: bool` parameter. When `True`, the executor performs read-only preflight checks and returns what it *would* do without mutating state. This is used by the change-request planner before approval.

---

## Authentication Per Connector

### Google Workspace

Credential payload (JSON stored in SecretsService):
```json
{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "...",
  "client_email": "...",
  "client_id": "...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "delegated_admin_email": "admin@example.com"
}
```

Executors use **domain-wide delegation**: the service account credential is loaded via `google.oauth2.service_account.Credentials`, then `delegated_to(delegated_admin_email)` is called to impersonate the workspace admin. Required OAuth scopes per action are listed in each action definition below.

### GitHub

Credential payload:
```json
{
  "token": "ghp_...",
  "org": "my-org"
}
```

Executors construct a `github.Github(token)` instance via **PyGithub**. Org-level actions (remove member, revoke PATs) require a token with `admin:org` scope. Repo-level actions (branch protection, archive, Actions toggle) require `repo` scope.

### Slack

Credential payload:
```json
{
  "bot_token": "xoxb-...",
  "admin_token": "xoxp-..."
}
```

User management actions (deactivate, reactivate) require a **user token** (`xoxp-`) with `admin.users:write` scope — available only on Enterprise Grid. The `bot_token` is used for non-admin API calls. Executors use **slack-sdk** (`slack_sdk.WebClient`).

### Microsoft 365 / Entra ID

Credential payload:
```json
{
  "tenant_id": "...",
  "client_id": "...",
  "client_secret": "..."
}
```

Executors acquire a token via **MSAL** (`msal.ConfidentialClientApplication`) with the `https://graph.microsoft.com/.default` scope and call the **Microsoft Graph API** using `httpx`. Application permissions (not delegated) are required: `User.ReadWrite.All`, `Directory.ReadWrite.All`, `TeamMember.ReadWrite.All`.

### Kubernetes

Credential payload (one of):
```json
{ "kubeconfig_b64": "<base64-encoded kubeconfig YAML>" }
```
or
```json
{ "server": "https://k8s.example.com", "token": "<bearer token>", "ca_cert_b64": "<base64 CA cert>" }
```

Executors use the **kubernetes** Python client. If `kubeconfig_b64` is present, it is base64-decoded, written to a `tempfile.NamedTemporaryFile`, and loaded via `kubernetes.config.load_kube_config(config_file=tmpfile.name)`. If `server`/`token` are present, `kubernetes.config.load_incluster_config()` is skipped and a `Configuration` object is constructed manually. The temp file is deleted in a `finally` block.

---

## Section 1: Google Workspace Actions

### 1.1 `suspend_user`

Suspend a Google Workspace user account, preventing sign-in.

**API:** `Admin SDK Directory API` — `PATCH /admin/directory/v1/users/{userKey}` with `{"suspended": true}`
**Required scope:** `https://www.googleapis.com/auth/admin.directory.user`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_email` | string | yes | Primary email of the user to suspend |

**Pre-change snapshot:** Current `suspended` status fetched from `GET /admin/directory/v1/users/{userKey}` and stored in `rollback_data`.

**Rollback:** Call `unsuspend_user` with the same `user_email`. Rollback data: `{"user_email": "<email>", "was_suspended": false}`.

---

### 1.2 `unsuspend_user`

Restore a previously suspended Google Workspace user.

**API:** `PATCH /admin/directory/v1/users/{userKey}` with `{"suspended": false}`
**Required scope:** `https://www.googleapis.com/auth/admin.directory.user`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_email` | string | yes | Primary email of the user to restore |

**Rollback:** Call `suspend_user`. No meaningful rollback data beyond the user email.

---

### 1.3 `remove_from_groups`

Remove a user from all Google Groups they are a member of.

**API:**
1. `GET /admin/directory/v1/groups?userKey={email}` — list all groups
2. `DELETE /admin/directory/v1/groups/{groupKey}/members/{memberKey}` — for each group

**Required scope:** `https://www.googleapis.com/auth/admin.directory.group`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_email` | string | yes | User to remove from all groups |

**Pre-change snapshot:** Full list of group emails stored in `rollback_data` as `{"groups": ["g1@...", "g2@..."]}`.

**Rollback:** Re-add the user to each group via `POST /admin/directory/v1/groups/{groupKey}/members` with `{"email": user_email, "role": "MEMBER"}`. Executor `restore_group_memberships` (can be called programmatically from the rollback handler).

---

### 1.4 `reset_2fa`

Revoke all enrolled 2-Step Verification methods for a user, forcing re-enrollment on next sign-in.

**API:** `DELETE /admin/directory/v1/users/{userKey}/twoStepVerification` (turns off enforcement for user) — followed by toggling enrollment off via `POST /admin/directory/v1/users/{userKey}/makeAdmin` is **not** used; instead the Admin SDK `twoStepVerification.turnOff` method is called.

Correct endpoint: `POST https://admin.googleapis.com/admin/directory/v1/users/{userKey}/twoStepVerification/turnOff`

**Required scope:** `https://www.googleapis.com/auth/admin.directory.user.security`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_email` | string | yes | User whose 2FA enrollment to revoke |

**Rollback:** None — 2FA state before reset cannot be programmatically restored. `rollback_data = null`. UI displays: "2FA reset cannot be automatically rolled back. User must re-enroll."

---

### 1.5 `revoke_oauth_tokens`

Revoke all third-party OAuth application tokens granted by a user.

**API:** `GET /admin/directory/v1/tokens?userKey={email}` → for each token: `DELETE /admin/directory/v1/tokens/{userKey}/{clientId}`

**Required scope:** `https://www.googleapis.com/auth/admin.directory.user.security`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_email` | string | yes | User whose OAuth tokens to revoke |

**Pre-change snapshot:** List of `{clientId, displayText, scopes}` objects stored in `rollback_data`.

**Rollback:** None — individual OAuth grants cannot be re-issued programmatically. `rollback_data` lists what was revoked for audit purposes only.

---

### 1.6 `wipe_mobile_device`

Remotely wipe a mobile device enrolled in Google Workspace MDM.

**API:**
1. `GET /admin/directory/v1/mobiledevices?customerId=my_customer&query=email:{user_email}` — list devices
2. `POST /admin/directory/v1/customer/{customerId}/devices/mobile/{resourceId}/action` with `{"action": "wipe"}`

**Required scope:** `https://www.googleapis.com/auth/admin.directory.device.mobile.action`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_email` | string | yes | Owner of the device(s) to wipe |
| `resource_id` | string | no | Specific device resource ID; if omitted, all enrolled devices are wiped |

**Rollback:** None. `rollback_data = null`. UI displays a hard warning before approval: "THIS ACTION IS IRREVERSIBLE. All data on the device will be deleted."

---

## Section 2: GitHub Actions

### 2.1 `remove_org_member`

Remove a user from the GitHub organization.

**API:** `DELETE /orgs/{org}/members/{username}` (PyGithub: `org.remove_from_members(user)`)

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `username` | string | yes | GitHub username to remove |

**Pre-change snapshot:** List of teams the user belonged to and their org role (`member`/`owner`) stored in `rollback_data`.

**Rollback:** Re-invite via `POST /orgs/{org}/invitations` with `{invitee_id, role}` and re-add to teams. Note: invitation must be accepted by the user.

---

### 2.2 `revoke_user_pats`

Revoke all fine-grained personal access tokens a user has authorized for the organization.

**API:** `GET /orgs/{org}/personal-access-tokens` filtered by `owner={username}` → for each: `DELETE /orgs/{org}/personal-access-tokens/{pat_id}`

Requires **fine-grained PAT review** to be enabled on the org. Classic PATs cannot be org-revoked via API; this action targets fine-grained PATs only and logs a warning if classic PATs are detected.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `username` | string | yes | GitHub username whose org-authorized PATs to revoke |

**Rollback:** None — tokens cannot be re-issued. `rollback_data` contains token metadata (name, scopes, last used) for audit.

---

### 2.3 `enforce_branch_protection`

Apply a branch protection ruleset to a repository branch.

**API:** `PUT /repos/{owner}/{repo}/branches/{branch}/protection` with the ruleset payload.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `repo` | string | yes | Repository name (without org prefix) |
| `branch` | string | yes | Branch name (e.g. `main`) |
| `require_pr_reviews` | bool | no | Require pull request reviews (default: `true`) |
| `required_approving_review_count` | int | no | Number of approvals required (default: `1`) |
| `require_status_checks` | list[string] | no | Required status check contexts |
| `enforce_admins` | bool | no | Enforce rules for admins (default: `true`) |
| `restrict_push` | list[string] | no | Teams/users allowed to push directly |

**Pre-change snapshot:** Current branch protection settings fetched from `GET /repos/{owner}/{repo}/branches/{branch}/protection` and stored verbatim in `rollback_data`.

**Rollback:** `PUT` the previous protection payload back. If no protection existed, call `DELETE /repos/{owner}/{repo}/branches/{branch}/protection`.

---

### 2.4 `archive_repo`

Archive a GitHub repository, making it read-only.

**API:** `PATCH /repos/{owner}/{repo}` with `{"archived": true}`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `repo` | string | yes | Repository name to archive |

**Pre-change snapshot:** `{"archived": false}` stored in `rollback_data`.

**Rollback:** `PATCH /repos/{owner}/{repo}` with `{"archived": false}` (unarchive).

---

### 2.5 `disable_actions` / `enable_actions`

Enable or disable GitHub Actions on a repository.

**API:** `PUT /repos/{owner}/{repo}/actions/permissions` with `{"enabled": false}` or `{"enabled": true}`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `repo` | string | yes | Repository name |

**Pre-change snapshot:** Current `enabled` value from `GET /repos/{owner}/{repo}/actions/permissions`.

**Rollback:** Restore the previous `enabled` value.

---

## Section 3: Slack Actions

### 3.1 `deactivate_user`

Deactivate a Slack user, preventing them from signing in. Enterprise Grid only.

**API:** `POST /admin.users.setInactive` with `{"user_id": "<user_id>"}`
**Required token:** `xoxp-` admin token with `admin.users:write` scope.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_id` | string | yes | Slack user ID (e.g. `U012AB3CD`) |

**Lookup helper:** If only email is known, use `GET /users.lookupByEmail?email={email}` to resolve `user_id` first.

**Pre-change snapshot:** `{"user_id": "...", "was_active": true}` in `rollback_data`.

**Rollback:** Call `reactivate_user` with the same `user_id`.

---

### 3.2 `reactivate_user`

Reactivate a previously deactivated Slack user.

**API:** `POST /admin.users.setActive` with `{"user_id": "<user_id>"}`
**Required token:** `xoxp-` admin token with `admin.users:write` scope.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_id` | string | yes | Slack user ID to reactivate |

**Rollback:** Call `deactivate_user`.

---

## Section 4: Microsoft 365 / Entra ID Actions

### 4.1 `disable_mailbox`

Disable a user's Exchange Online mailbox by blocking sign-in to the account.

**API:** `PATCH https://graph.microsoft.com/v1.0/users/{userId}` with `{"accountEnabled": false}`

Note: Disabling the account also blocks all M365 services. A mailbox-only block requires Exchange PowerShell (`Set-Mailbox -Identity ... -HiddenFromAddressListsEnabled $true`) which is not available via Graph. This action disables the entire account; see `revoke_sessions` for a softer session-only revocation.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_id` | string | yes | Entra ID object ID or UPN |

**Pre-change snapshot:** `{"accountEnabled": true}` from `GET /users/{userId}?$select=accountEnabled`.

**Rollback:** `PATCH /users/{userId}` with `{"accountEnabled": true}`.

---

### 4.2 `remove_from_teams`

Remove a user from all Microsoft Teams they are a member of.

**API:**
1. `GET /users/{userId}/joinedTeams` — list all teams
2. `DELETE /groups/{teamId}/members/{userId}/$ref` — for each team

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_id` | string | yes | Entra ID object ID or UPN |

**Pre-change snapshot:** List of `{teamId, teamName, membershipId}` objects in `rollback_data`.

**Rollback:** Re-add user to each team via `POST /groups/{teamId}/members/$ref` with `{"@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/{userId}"}`.

---

### 4.3 `revoke_sessions`

Revoke all active sign-in sessions for a Microsoft 365 user (invalidates all refresh tokens).

**API:** `POST https://graph.microsoft.com/v1.0/users/{userId}/revokeSignInSessions`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_id` | string | yes | Entra ID object ID or UPN |

**Rollback:** None — sessions cannot be restored. `rollback_data = null`. User will be required to re-authenticate on all devices.

---

### 4.4 `assign_license` / `remove_license`

Add or remove a Microsoft 365 license from a user.

**API:** `POST https://graph.microsoft.com/v1.0/users/{userId}/assignLicense` with `{"addLicenses": [{"skuId": "..."}], "removeLicenses": []}` or inverse.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `user_id` | string | yes | Entra ID object ID or UPN |
| `sku_id` | string | yes | License SKU GUID (e.g. `6fd2c87f-b296-42f0-b197-1e91e994b900` for E3) |

**Pre-change snapshot:** Current assigned licenses from `GET /users/{userId}?$select=assignedLicenses`.

**Rollback:** Inverse operation (assign if removed, remove if assigned).

---

## Section 5: Kubernetes Actions

### 5.1 `restart_deployment`

Perform a rolling restart of a Kubernetes Deployment by patching the pod template annotation.

**API:** `kubernetes.client.AppsV1Api().patch_namespaced_deployment` — patches `spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]` to current timestamp.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `namespace` | string | yes | Kubernetes namespace |
| `deployment_name` | string | yes | Deployment name |

**Pre-change snapshot:** Current `restartedAt` annotation value (or absence thereof).

**Rollback:** No meaningful rollback (pods were restarted from the same image). `rollback_data` records the deployment's `resourceVersion` before restart for audit.

---

### 5.2 `scale_deployment`

Change the replica count of a Kubernetes Deployment.

**API:** `AppsV1Api().patch_namespaced_deployment_scale` with `{"spec": {"replicas": N}}`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `namespace` | string | yes | Kubernetes namespace |
| `deployment_name` | string | yes | Deployment name |
| `replicas` | int | yes | Target replica count (0 to scale to zero) |

**Pre-change snapshot:** Current replica count from `read_namespaced_deployment_scale`.

**Rollback:** Patch replicas back to the previous count from `rollback_data`.

---

### 5.3 `apply_network_policy`

Apply a NetworkPolicy manifest to a namespace.

**API:** `kubernetes.client.NetworkingV1Api().create_namespaced_network_policy` or `patch_namespaced_network_policy` (server-side apply).

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `namespace` | string | yes | Target namespace |
| `manifest` | string | yes | NetworkPolicy manifest as a YAML or JSON string |

**Pre-change snapshot:** Existing NetworkPolicy with the same name (if any), serialized to JSON. If no prior policy existed, `rollback_data = {"existed": false, "name": "...", "namespace": "..."}`.

**Rollback:** If a previous policy existed, restore it via `replace_namespaced_network_policy`. If it did not exist, `delete_namespaced_network_policy`.

---

### 5.4 `update_rbac`

Apply a ClusterRoleBinding or RoleBinding manifest.

**API:** `kubernetes.client.RbacAuthorizationV1Api().create_namespaced_role_binding` / `create_cluster_role_binding` or patch variants.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `manifest` | string | yes | RoleBinding or ClusterRoleBinding manifest as YAML or JSON |
| `namespace` | string | no | Required for RoleBinding; omit for ClusterRoleBinding |

**Pre-change snapshot:** Existing binding with the same name serialized to JSON.

**Rollback:** Restore previous binding or delete if it did not exist before.

---

### 5.5 `rotate_secret`

Update a Kubernetes Secret's data values and trigger a rolling restart of all Deployments that mount the secret.

**API:**
1. `CoreV1Api().patch_namespaced_secret` — update secret data fields
2. For each dependent Deployment found via label selector or annotation: `AppsV1Api().patch_namespaced_deployment` — bump `restartedAt` annotation

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `namespace` | string | yes | Kubernetes namespace |
| `secret_name` | string | yes | Secret name |
| `data` | object | yes | Key-value pairs to update (values are plain strings; executor base64-encodes them) |
| `restart_deployments` | list[string] | no | Explicit list of Deployment names to restart; if omitted, executor searches for deployments that reference the secret |

**Pre-change snapshot:** Previous secret data keys (not values — secrets are not stored in rollback_data for security) and list of deployment names that were restarted.

**Rollback:** Rollback data contains previous secret data values (encrypted at rest in the change record using the same SecretsService). Rollback patches the secret back and restarts deployments again.

**Security note:** `rollback_data` for this action is encrypted using `SecretsService` rather than stored as plain JSON in the change record.

---

### 5.6 `helm_upgrade`

Upgrade a Helm release to a new chart version, optionally overriding values.

**Implementation:** `subprocess.run(["helm", "upgrade", release_name, chart, "--version", version, "--namespace", namespace, "--kubeconfig", tmpfile.name, "--output", "json", "--atomic", "--timeout", "5m0s"] + values_args)`

The `--atomic` flag rolls back automatically on failure. `--output json` is parsed for the new revision number.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `namespace` | string | yes | Kubernetes namespace |
| `release_name` | string | yes | Helm release name |
| `chart` | string | yes | Chart reference (e.g. `bitnami/nginx` or path to chart directory) |
| `version` | string | no | Chart version to upgrade to; omit for latest |
| `values` | object | no | Key-value pairs passed as `--set key=value` arguments |
| `values_yaml` | string | no | YAML string passed via `--values` (written to a temp file) |

**Pre-change snapshot:** Current release revision from `helm history {release} --output json | tail -1` stored in `rollback_data` as `{"previous_revision": N}`.

**Rollback:** Call `helm_rollback` with `revision = rollback_data["previous_revision"]`.

---

### 5.7 `helm_rollback`

Roll back a Helm release to a previous revision.

**Implementation:** `subprocess.run(["helm", "rollback", release_name, str(revision), "--namespace", namespace, "--kubeconfig", tmpfile.name, "--wait"])`

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `namespace` | string | yes | Kubernetes namespace |
| `release_name` | string | yes | Helm release name |
| `revision` | int | no | Target revision number; omit to roll back one revision |

**Rollback:** Re-run `helm_upgrade` to the version that was active before this rollback. `rollback_data` records the revision rolled back from.

---

## New Python Dependencies

Add to `backend/requirements.txt` (or `pyproject.toml`):

| Package | Version | Used By |
|---------|---------|---------|
| `google-api-python-client` | `>=2.120.0` | Google Workspace actions |
| `google-auth` | `>=2.29.0` | Google Workspace auth (service account + delegation) |
| `google-auth-httplib2` | `>=0.2.0` | Google Workspace HTTP transport |
| `PyGithub` | `>=2.3.0` | GitHub actions |
| `slack-sdk` | `>=3.27.0` | Slack actions |
| `msal` | `>=1.28.0` | Microsoft 365 / Entra ID auth |
| `httpx` | `>=0.27.0` | Microsoft Graph API calls (already likely present) |
| `kubernetes` | `>=29.0.0` | Kubernetes actions |

Note: `helm` CLI must be present in the backend Docker image. Add to `backend/Dockerfile`:
```dockerfile
RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

---

## Executor File Layout

New files follow the pattern `backend/app/connectors/{connector}/executors/{action_id}.py`:

```
backend/app/connectors/
  google_workspace/
    executors/
      suspend_user.py
      unsuspend_user.py
      remove_from_groups.py
      reset_2fa.py
      revoke_oauth_tokens.py
      wipe_mobile_device.py
  github/
    executors/
      remove_org_member.py
      revoke_user_pats.py
      enforce_branch_protection.py
      archive_repo.py
      disable_actions.py
      enable_actions.py
  slack/
    executors/
      deactivate_user.py
      reactivate_user.py
  microsoft365/
    executors/
      disable_mailbox.py
      remove_from_teams.py
      revoke_sessions.py
      assign_license.py
      remove_license.py
  kubernetes/
    executors/
      restart_deployment.py
      scale_deployment.py
      apply_network_policy.py
      update_rbac.py
      rotate_secret.py
      helm_upgrade.py
      helm_rollback.py
```

Each executor registers itself in the connector's `catalog.py`:

```python
# Example: google_workspace/catalog.py (additions)
from .executors.suspend_user import SuspendUserExecutor
from .executors.unsuspend_user import UnsuspendUserExecutor
# ...

ACTION_CATALOG = {
    # existing discovery actions ...
    "suspend_user":       SuspendUserExecutor,
    "unsuspend_user":     UnsuspendUserExecutor,
    "remove_from_groups": RemoveFromGroupsExecutor,
    "reset_2fa":          Reset2FAExecutor,
    "revoke_oauth_tokens": RevokeOAuthTokensExecutor,
    "wipe_mobile_device": WipeMobileDeviceExecutor,
}
```

---

## Shared ActionResult Schema

All executors return an `ActionResult` dataclass (new shared model):

```python
# backend/app/connectors/base.py (addition)
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ActionResult:
    success: bool
    message: str
    rollback_data: Any = None          # JSON-serializable; None if rollback not supported
    partial_failures: list[str] = field(default_factory=list)  # for multi-resource actions
    dry_run: bool = False
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/requirements.txt` | Add `google-api-python-client`, `google-auth`, `google-auth-httplib2`, `PyGithub`, `slack-sdk`, `msal`, `kubernetes` |
| `backend/Dockerfile` | Install `helm` CLI via get-helm-3 script |
| `backend/app/connectors/base.py` | Add `ActionResult` dataclass |
| `backend/app/connectors/google_workspace/catalog.py` | Register 6 new change action executors |
| `backend/app/connectors/google_workspace/executors/suspend_user.py` | New |
| `backend/app/connectors/google_workspace/executors/unsuspend_user.py` | New |
| `backend/app/connectors/google_workspace/executors/remove_from_groups.py` | New |
| `backend/app/connectors/google_workspace/executors/reset_2fa.py` | New |
| `backend/app/connectors/google_workspace/executors/revoke_oauth_tokens.py` | New |
| `backend/app/connectors/google_workspace/executors/wipe_mobile_device.py` | New |
| `backend/app/connectors/github/catalog.py` | Register 6 new change action executors |
| `backend/app/connectors/github/executors/remove_org_member.py` | New |
| `backend/app/connectors/github/executors/revoke_user_pats.py` | New |
| `backend/app/connectors/github/executors/enforce_branch_protection.py` | New |
| `backend/app/connectors/github/executors/archive_repo.py` | New |
| `backend/app/connectors/github/executors/disable_actions.py` | New |
| `backend/app/connectors/github/executors/enable_actions.py` | New |
| `backend/app/connectors/slack/catalog.py` | Register 2 new change action executors |
| `backend/app/connectors/slack/executors/deactivate_user.py` | New |
| `backend/app/connectors/slack/executors/reactivate_user.py` | New |
| `backend/app/connectors/microsoft365/catalog.py` | Register 5 new change action executors |
| `backend/app/connectors/microsoft365/executors/disable_mailbox.py` | New |
| `backend/app/connectors/microsoft365/executors/remove_from_teams.py` | New |
| `backend/app/connectors/microsoft365/executors/revoke_sessions.py` | New |
| `backend/app/connectors/microsoft365/executors/assign_license.py` | New |
| `backend/app/connectors/microsoft365/executors/remove_license.py` | New |
| `backend/app/connectors/kubernetes/catalog.py` | Register 7 new change action executors |
| `backend/app/connectors/kubernetes/executors/restart_deployment.py` | New |
| `backend/app/connectors/kubernetes/executors/scale_deployment.py` | New |
| `backend/app/connectors/kubernetes/executors/apply_network_policy.py` | New |
| `backend/app/connectors/kubernetes/executors/update_rbac.py` | New |
| `backend/app/connectors/kubernetes/executors/rotate_secret.py` | New |
| `backend/app/connectors/kubernetes/executors/helm_upgrade.py` | New |
| `backend/app/connectors/kubernetes/executors/helm_rollback.py` | New |
