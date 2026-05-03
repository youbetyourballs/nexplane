# Secret & Credential Rotation — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Automated rotation of database credentials, SSH keys, API keys, and service account passwords — with multi-step change execution, step-output propagation, and rollback strategies. Covers new agent commands, new connector actions, and the change plan model changes needed to thread secrets between steps.

---

## Background

Nexplane already stores encrypted credentials in `SecretsService` and executes multi-step change requests on managed hosts via the Go agent. What is missing is the orchestration layer that generates a new credential, pushes it to the authoritative source (DB, IdP, cloud API), propagates it to every consumer (config files, Kubernetes secrets, Parameter Store, Vault), and verifies the result — all as a single auditable change. This spec adds four change types that cover the most common rotation scenarios: database credentials, SSH keys, API keys, and service account passwords.

---

## Design Decisions

- **Agent-side generation:** New passwords and key pairs are generated on the agent, never in the control plane. The control plane never sees a plaintext credential in transit; it only stores an encrypted copy after the fact in `SecretsService`.
- **Step-output propagation:** A change plan step can declare `produces: ["new_password"]`. The execution engine resolves that output after the step succeeds and injects it into subsequent steps that declare `consumes: ["new_password"]`. This avoids storing the live secret in the change plan JSON and keeps propagation deterministic.
- **Backup before mutate:** Every step that modifies a file or an auth store first writes a timestamped backup. Rollback steps restore from the backup path, which is recorded in step outputs.
- **Connector actions are non-agent:** AWS RDS password rotation, Okta password reset, and AD password update are connector actions executed by the control plane, not the agent. The resulting credential is sealed into `SecretsService` and injected into the next step as a consumed output.
- **Health verification is mandatory:** Every rotation change type ends with a `verify_health` step. If health fails, the change engine executes the rollback plan automatically before marking the change as failed.
- **No partial rollback:** Rollback is all-or-nothing per change. If any rollback step fails, the change is marked `rollback_failed` and an alert is raised. Partial states are surfaced in the change audit log with the exact step that failed.

---

## Section 1: Step-Output Propagation Model

The change plan JSON gains two optional fields on each step:

```json
{
  "id": "step_generate_password",
  "type": "agent_command",
  "command": "rotate_db_credentials",
  "params": { "action": "generate" },
  "produces": ["new_password", "old_password_backup_path"],
  "consumes": []
}
```

```json
{
  "id": "step_update_config",
  "type": "agent_command",
  "command": "rotate_db_credentials",
  "params": { "action": "update_config", "config_paths": ["/etc/app/db.env"] },
  "produces": [],
  "consumes": ["new_password"]
}
```

### Propagation Rules

- `produces` values are strings that name the output slot. The step implementation writes them into a `StepOutputs` map keyed by slot name.
- `consumes` values are resolved by the execution engine from the accumulated `StepOutputs` of all prior completed steps before the step is dispatched.
- Consumed values are injected into the step params at dispatch time under the key matching the slot name (e.g., `params["new_password"] = <resolved value>`).
- If a consumed slot has no resolved value (prior step failed), the step is blocked and the change fails before dispatch.
- Credentials in `StepOutputs` are held in memory only during the change execution goroutine. They are never written to the `change_steps` DB table. The audit log records slot names only, not values.

### Backend Data Model

```python
# backend/app/models/change_plan.py (additions)

class StepOutput(BaseModel):
    slot: str           # e.g. "new_password"
    sealed: bool        # True if value should be passed through SecretsService
    value: str | None   # populated at runtime, never persisted

class ChangePlanStep(BaseModel):
    id: str
    type: Literal["agent_command", "connector_action", "verify_health"]
    command: str
    params: dict[str, Any]
    produces: list[str] = []
    consumes: list[str] = []
    rollback_command: str | None = None
    rollback_params: dict[str, Any] = {}
```

---

## Section 2: Database Credential Rotation

**Change type:** `rotate_db_credentials`

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `db_host` | string | Hostname or IP of the database server |
| `db_port` | int | Port (default 5432 for Postgres, 3306 for MySQL) |
| `db_engine` | enum | `postgres` \| `mysql` \| `rds_postgres` \| `rds_mysql` |
| `db_username` | string | Database user whose password is rotated |
| `config_paths` | list[string] | Absolute paths to env/conf files containing the credential |
| `service_name` | string | Systemd unit or process name to restart after update |
| `health_check_url` | string | URL or command used to verify service health post-restart |
| `rds_instance_id` | string | (RDS only) RDS instance identifier |
| `ssm_parameter_path` | string | (RDS only) SSM parameter to update with new password |

### Change Plan Steps

```
1. generate_password          (agent)   → produces: new_password, old_password_backup_path
2. update_db_user_password    (agent)   → consumes: new_password
3. backup_config_files        (agent)   → produces: config_backup_paths
4. update_config_files        (agent)   → consumes: new_password
5. restart_service            (agent)
6. verify_health              (agent/connector)
--- if RDS ---
2a. rotate_rds_password       (connector: AWS)  → consumes: new_password; updates RDS master password
2b. update_ssm_parameter      (connector: AWS)  → consumes: new_password
```

### Agent Command Package: `agent/commands/credrotation/db.go`

```go
package credrotation

import (
    "context"
    "crypto/rand"
    "encoding/base64"
    "fmt"
    "os"
    "os/exec"
    "strings"
    "time"
)

// DBRotateParams holds all inputs resolved from the change step at dispatch time.
type DBRotateParams struct {
    Action          string   // "generate" | "update_db_user" | "backup_config" | "update_config" | "restart" | "restore_config"
    DBHost          string
    DBPort          int
    DBEngine        string   // "postgres" | "mysql"
    DBUsername      string
    ConfigPaths     []string
    ServiceName     string
    HealthCheckURL  string
    NewPassword     string   // injected via consumes
    ConfigBackupPaths []string // injected via consumes for rollback
}

// Execute dispatches to the correct sub-action.
func (p DBRotateParams) Execute(ctx context.Context) (map[string]string, error) {
    switch p.Action {
    case "generate":
        return generatePassword(ctx)
    case "update_db_user":
        return nil, updateDBUserPassword(ctx, p)
    case "backup_config":
        return backupConfigFiles(ctx, p.ConfigPaths)
    case "update_config":
        return nil, updateConfigFiles(ctx, p.ConfigPaths, p.NewPassword, p.DBUsername)
    case "restart":
        return nil, restartService(ctx, p.ServiceName)
    case "restore_config":
        return nil, restoreConfigFiles(ctx, p.ConfigBackupPaths)
    default:
        return nil, fmt.Errorf("unknown db rotation action: %q", p.Action)
    }
}

func generatePassword(ctx context.Context) (map[string]string, error) {
    b := make([]byte, 32)
    if _, err := rand.Read(b); err != nil {
        return nil, fmt.Errorf("generate password: %w", err)
    }
    pw := base64.URLEncoding.EncodeToString(b)[:40]
    return map[string]string{"new_password": pw}, nil
}

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
    var stmt string
    switch p.DBEngine {
    case "postgres":
        stmt = fmt.Sprintf("ALTER USER %s WITH PASSWORD '%s';", p.DBUsername, p.NewPassword)
    case "mysql":
        stmt = fmt.Sprintf("ALTER USER '%s'@'%%' IDENTIFIED BY '%s';", p.DBUsername, p.NewPassword)
    default:
        return fmt.Errorf("unsupported db engine: %s", p.DBEngine)
    }
    cmd := buildDBCmd(ctx, p, stmt)
    out, err := cmd.CombinedOutput()
    if err != nil {
        return fmt.Errorf("update db user password: %w — %s", err, out)
    }
    return nil
}

func backupConfigFiles(ctx context.Context, paths []string) (map[string]string, error) {
    backups := make([]string, 0, len(paths))
    for _, p := range paths {
        dest := fmt.Sprintf("%s.nexplane-bak-%s", p, time.Now().Format("20060102T150405"))
        data, err := os.ReadFile(p)
        if err != nil {
            return nil, fmt.Errorf("backup %s: %w", p, err)
        }
        if err := os.WriteFile(dest, data, 0600); err != nil {
            return nil, fmt.Errorf("write backup %s: %w", dest, err)
        }
        backups = append(backups, dest)
    }
    return map[string]string{"config_backup_paths": strings.Join(backups, ",")}, nil
}

func updateConfigFiles(ctx context.Context, paths []string, newPassword, username string) error {
    for _, p := range paths {
        data, err := os.ReadFile(p)
        if err != nil {
            return fmt.Errorf("read %s: %w", p, err)
        }
        updated := replaceCredentialInContent(string(data), username, newPassword)
        if err := os.WriteFile(p, []byte(updated), 0600); err != nil {
            return fmt.Errorf("write %s: %w", p, err)
        }
    }
    return nil
}

func restartService(ctx context.Context, serviceName string) error {
    cmd := exec.CommandContext(ctx, "systemctl", "restart", serviceName)
    out, err := cmd.CombinedOutput()
    if err != nil {
        return fmt.Errorf("restart %s: %w — %s", serviceName, err, out)
    }
    return nil
}

func restoreConfigFiles(ctx context.Context, backupPaths []string) error {
    for _, bak := range backupPaths {
        original := strings.Replace(bak, ".nexplane-bak-", "", 1)
        // strip the timestamp suffix
        idx := strings.LastIndex(original, ".nexplane-bak")
        if idx != -1 {
            original = bak[:idx]
        }
        data, err := os.ReadFile(bak)
        if err != nil {
            return fmt.Errorf("read backup %s: %w", bak, err)
        }
        if err := os.WriteFile(original, data, 0600); err != nil {
            return fmt.Errorf("restore %s: %w", original, err)
        }
    }
    return nil
}
```

### Rollback Plan

| Step failed | Rollback actions |
|-------------|-----------------|
| `update_db_user_password` | Re-run `update_db_user_password` with original password from `SecretsService` |
| `update_config_files` | `restore_config` using `config_backup_paths` |
| `restart_service` or `verify_health` | `restore_config` + re-run `update_db_user_password` with old password + restart service |

---

## Section 3: SSH Key Fleet Rotation

**Change type:** `rotate_ssh_keys`

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `asset_group` | string | Asset group tag — change targets all assets in the group |
| `username` | string | OS username whose `authorized_keys` is modified |
| `old_key_fingerprint` | string | SHA256 fingerprint of the key to remove |
| `new_public_key` | string | Full public key string to add |

### Change Plan Steps

```
1. backup_authorized_keys     (agent, per host)   → produces: authorized_keys_backup_path
2. remove_old_key             (agent, per host)   → consumes: old_key_fingerprint
3. add_new_key                (agent, per host)   → consumes: new_public_key
4. verify_ssh_access          (connector/agent)   verifies new key still allows login
```

Steps 1–4 fan out across all assets in `asset_group`. The execution engine dispatches them in parallel per host and collects results. A single host failure does not abort other hosts but is recorded; rollback targets only the hosts that succeeded before the failure.

### Agent Command Package: `agent/commands/credrotation/sshkeys.go`

```go
package credrotation

import (
    "bufio"
    "context"
    "fmt"
    "os"
    "path/filepath"
    "strings"
    "time"
)

type SSHKeyParams struct {
    Action               string // "backup" | "remove_old" | "add_new" | "restore"
    Username             string
    OldKeyFingerprint    string
    NewPublicKey         string
    BackupPath           string // injected via consumes for restore
}

func (p SSHKeyParams) Execute(ctx context.Context) (map[string]string, error) {
    authKeysPath := filepath.Join("/home", p.Username, ".ssh", "authorized_keys")
    if p.Username == "root" {
        authKeysPath = "/root/.ssh/authorized_keys"
    }
    switch p.Action {
    case "backup":
        return backupAuthorizedKeys(authKeysPath)
    case "remove_old":
        return nil, removeKeyByFingerprint(ctx, authKeysPath, p.OldKeyFingerprint)
    case "add_new":
        return nil, addPublicKey(authKeysPath, p.NewPublicKey)
    case "restore":
        return nil, restoreAuthorizedKeys(authKeysPath, p.BackupPath)
    default:
        return nil, fmt.Errorf("unknown ssh key action: %q", p.Action)
    }
}

func backupAuthorizedKeys(src string) (map[string]string, error) {
    dest := fmt.Sprintf("%s.nexplane-bak-%s", src, time.Now().Format("20060102T150405"))
    data, err := os.ReadFile(src)
    if err != nil {
        return nil, fmt.Errorf("backup authorized_keys: %w", err)
    }
    if err := os.WriteFile(dest, data, 0600); err != nil {
        return nil, fmt.Errorf("write backup: %w", err)
    }
    return map[string]string{"authorized_keys_backup_path": dest}, nil
}

func removeKeyByFingerprint(ctx context.Context, path, fingerprint string) error {
    f, err := os.Open(path)
    if err != nil {
        return err
    }
    defer f.Close()

    var kept []string
    scanner := bufio.NewScanner(f)
    for scanner.Scan() {
        line := scanner.Text()
        fp, err := sshKeyFingerprint(ctx, line)
        if err != nil || fp != fingerprint {
            kept = append(kept, line)
        }
    }
    if err := scanner.Err(); err != nil {
        return err
    }
    return os.WriteFile(path, []byte(strings.Join(kept, "\n")+"\n"), 0600)
}

func addPublicKey(path, pubKey string) error {
    f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
    if err != nil {
        return err
    }
    defer f.Close()
    _, err = fmt.Fprintf(f, "%s\n", strings.TrimSpace(pubKey))
    return err
}

func restoreAuthorizedKeys(original, backupPath string) error {
    data, err := os.ReadFile(backupPath)
    if err != nil {
        return fmt.Errorf("read backup %s: %w", backupPath, err)
    }
    return os.WriteFile(original, data, 0600)
}
```

### Rollback Plan

| Step failed | Rollback actions |
|-------------|-----------------|
| `remove_old_key` or `add_new_key` | `restore` from `authorized_keys_backup_path` (per host) |
| `verify_ssh_access` | `restore` on all hosts that ran steps 2–3 |

---

## Section 4: API Key Rotation

**Change type:** `rotate_api_key`

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `service` | enum | `okta` \| `github` \| `aws_iam` |
| `key_identifier` | string | Key ID, IAM access key ID, or GitHub token name |
| `propagation_targets` | list[PropagationTarget] | Where to push the new key value |

**PropagationTarget:**

```python
class PropagationTarget(BaseModel):
    type: Literal["k8s_secret", "agent_env_file", "ssm_parameter", "vault_secret"]
    # k8s_secret
    namespace: str | None = None
    secret_name: str | None = None
    secret_key: str | None = None
    # agent_env_file
    asset_id: str | None = None
    file_path: str | None = None
    env_var_name: str | None = None
    # ssm_parameter
    ssm_path: str | None = None
    # vault_secret
    vault_path: str | None = None
    vault_key: str | None = None
```

### Change Plan Steps

```
1. create_new_api_key         (connector: okta|github|aws_iam)   → produces: new_api_key, old_api_key
2. propagate_to_targets       (parallel fan-out, one step per target):
   2a. update_k8s_secret      (connector: kubernetes)             → consumes: new_api_key
   2b. update_agent_env_file  (agent)                             → consumes: new_api_key
   2c. update_ssm_parameter   (connector: AWS)                    → consumes: new_api_key
   2d. update_vault_secret    (connector: vault)                  → consumes: new_api_key
3. verify_propagation         (connector, per target)
4. revoke_old_api_key         (connector: okta|github|aws_iam)   → consumes: old_api_key
```

Step 4 (revoke old key) runs only after all propagation targets pass verification. This ensures the old key remains valid as a fallback throughout propagation.

### Connector Actions

**`backend/app/connectors/aws/actions/rotate_api_key.py`**

```python
async def create_iam_access_key(connector_config: AWSConnectorConfig, iam_username: str) -> dict:
    """Creates a new IAM access key, returns {"new_api_key": "AKIA...:secret", "old_key_id": "..."}."""
    client = boto3.client("iam", **connector_config.boto_kwargs())
    response = client.create_access_key(UserName=iam_username)
    key = response["AccessKey"]
    return {
        "new_api_key": f"{key['AccessKeyId']}:{key['SecretAccessKey']}",
    }

async def delete_iam_access_key(connector_config: AWSConnectorConfig, iam_username: str, key_id: str) -> None:
    client = boto3.client("iam", **connector_config.boto_kwargs())
    client.delete_access_key(UserName=iam_username, AccessKeyId=key_id)
```

**`backend/app/connectors/okta/actions/rotate_api_key.py`**

```python
async def rotate_okta_api_key(connector_config: OktaConnectorConfig, key_name: str) -> dict:
    """Revokes the named API key and creates a replacement. Returns {"new_api_key": "...", "old_api_key": "..."}."""
    headers = {"Authorization": f"SSWS {connector_config.api_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        keys_resp = await client.get(f"{connector_config.domain}/api/v1/api-tokens", headers=headers)
        keys_resp.raise_for_status()
        old_token = next((k for k in keys_resp.json() if k["name"] == key_name), None)
        old_id = old_token["id"] if old_token else None

        create_resp = await client.post(
            f"{connector_config.domain}/api/v1/api-tokens",
            headers=headers,
            json={"name": f"{key_name}-rotated-{datetime.utcnow():%Y%m%d}"},
        )
        create_resp.raise_for_status()
        new_key = create_resp.json()["token"]

        return {"new_api_key": new_key, "old_key_id": old_id or ""}
```

### Agent Command for `update_agent_env_file`

```go
// agent/commands/credrotation/apikey.go

type APIKeyEnvParams struct {
    FilePath   string
    EnvVarName string
    NewAPIKey  string // injected via consumes
    BackupPath string // injected via consumes for rollback
}

func (p APIKeyEnvParams) Execute(ctx context.Context) (map[string]string, error) {
    // Backup
    dest := fmt.Sprintf("%s.nexplane-bak-%s", p.FilePath, time.Now().Format("20060102T150405"))
    data, err := os.ReadFile(p.FilePath)
    if err != nil {
        return nil, fmt.Errorf("read %s: %w", p.FilePath, err)
    }
    if err := os.WriteFile(dest, data, 0600); err != nil {
        return nil, fmt.Errorf("write backup: %w", err)
    }
    // Replace env var line
    updated := replaceEnvVarInContent(string(data), p.EnvVarName, p.NewAPIKey)
    if err := os.WriteFile(p.FilePath, []byte(updated), 0600); err != nil {
        return nil, fmt.Errorf("write %s: %w", p.FilePath, err)
    }
    return map[string]string{"env_file_backup_path": dest}, nil
}
```

### Rollback Plan

| Step failed | Rollback actions |
|-------------|-----------------|
| `propagate_to_targets` (any target) | Restore old key value to all targets that were updated; do not revoke old key |
| `verify_propagation` | Same as above |
| `revoke_old_api_key` | Log and alert only — old key may already be revoked; human review required |

---

## Section 5: Service Account Credential Rotation

**Change type:** `rotate_service_account`

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `idp` | enum | `active_directory` \| `okta` |
| `account_username` | string | Service account UPN or login name |
| `domain` | string | AD domain (AD only) |
| `service_asset_ids` | list[string] | Asset IDs of hosts that run services using this account (resolved from asset metadata `service_accounts` field if empty) |
| `config_paths_per_asset` | dict[str, list[str]] | Map of asset_id → list of config file paths containing the credential |
| `service_names_per_asset` | dict[str, list[str]] | Map of asset_id → list of service names to restart |
| `health_check_urls` | list[string] | URLs to verify post-restart |

When `service_asset_ids` is empty, the control plane resolves it at change-plan-build time by querying assets whose `service_accounts` metadata field contains `account_username`.

### Change Plan Steps

```
1. generate_password             (control plane — not agent)      → produces: new_password
2. update_idp_password           (connector: AD|Okta)             → consumes: new_password
3. per asset (fan-out):
   3a. backup_config_files        (agent)                         → produces: config_backup_paths
   3b. update_config_files        (agent)                         → consumes: new_password
   3c. restart_services           (agent)
   3d. verify_health              (agent)
```

### Connector Actions

**`backend/app/connectors/active_directory/actions/rotate_service_account.py`**

```python
async def update_ad_password(
    connector_config: ADConnectorConfig,
    username: str,
    domain: str,
    new_password: str,
) -> None:
    """Uses ldap3 to set the unicodePwd attribute on the AD user object."""
    import ldap3
    server = ldap3.Server(connector_config.ldap_host, use_ssl=True)
    conn = ldap3.Connection(server, user=connector_config.bind_dn, password=connector_config.bind_password, auto_bind=True)
    dn = f"CN={username},{connector_config.users_base_dn}"
    encoded = ('"%s"' % new_password).encode("utf-16-le")
    conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [encoded])]})
    if conn.result["result"] != 0:
        raise RuntimeError(f"AD password update failed: {conn.result['description']}")
```

**`backend/app/connectors/okta/actions/rotate_service_account.py`**

```python
async def update_okta_password(
    connector_config: OktaConnectorConfig,
    username: str,
    new_password: str,
) -> None:
    headers = {"Authorization": f"SSWS {connector_config.api_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        # Fetch user ID
        resp = await client.get(
            f"{connector_config.domain}/api/v1/users/{username}",
            headers=headers,
        )
        resp.raise_for_status()
        user_id = resp.json()["id"]
        # Set password
        pw_resp = await client.post(
            f"{connector_config.domain}/api/v1/users/{user_id}/lifecycle/reset_password",
            headers=headers,
            json={"newPassword": {"value": new_password}},
        )
        pw_resp.raise_for_status()
```

### Rollback Plan

| Step failed | Rollback actions |
|-------------|-----------------|
| `update_idp_password` | Re-run connector action with old password from `SecretsService` |
| `update_config_files` (any asset) | `restore_config` on all assets that ran 3b |
| `restart_services` or `verify_health` | `restore_config` + `restart_services` on failed asset; re-run `update_idp_password` with old password if all assets failed |

---

## Section 6: SecretsService Integration

After a successful rotation, the control plane seals the new credential into `SecretsService` under the same secret ID as the old one, incrementing a version counter. The old version is retained for the rollback window (configurable, default 7 days) then purged.

```python
# backend/app/services/secrets_service.py (additions)

async def rotate_secret(self, secret_id: UUID, new_value: str) -> SecretVersion:
    """Encrypts new_value, stores it as the current version, demotes the prior version to 'superseded'."""
    encrypted = self.encrypt(new_value)
    old_version = await self.db.get_current_secret_version(secret_id)
    if old_version:
        await self.db.set_secret_version_status(old_version.id, "superseded")
    new_version = SecretVersion(
        secret_id=secret_id,
        ciphertext=encrypted,
        status="current",
        created_at=datetime.utcnow(),
    )
    await self.db.insert_secret_version(new_version)
    return new_version

async def get_superseded_secret(self, secret_id: UUID) -> str | None:
    """Returns the most recent superseded plaintext, used for rollback re-encryption."""
    version = await self.db.get_latest_superseded_secret_version(secret_id)
    if not version:
        return None
    return self.decrypt(version.ciphertext)
```

---

## Files Changed

| File | Change |
|------|--------|
| `agent/commands/credrotation/db.go` | New — DB credential rotation agent commands |
| `agent/commands/credrotation/sshkeys.go` | New — SSH key rotation agent commands |
| `agent/commands/credrotation/apikey.go` | New — API key env-file propagation agent command |
| `agent/commands/credrotation/util.go` | New — shared helpers: `replaceCredentialInContent`, `replaceEnvVarInContent`, `sshKeyFingerprint` |
| `agent/commands/registry.go` | Register `rotate_db_credentials`, `rotate_ssh_keys`, `update_agent_env_file` command handlers |
| `backend/app/models/change_plan.py` | Add `produces`, `consumes`, `rollback_command`, `rollback_params` to `ChangePlanStep`; add `StepOutput` model |
| `backend/app/services/change_execution.py` | Implement step-output propagation: accumulate `StepOutputs`, inject into step params at dispatch, trigger rollback plan on health-check failure |
| `backend/app/services/secrets_service.py` | Add `rotate_secret` and `get_superseded_secret`; add `SecretVersion` model with `status` field |
| `backend/app/connectors/aws/actions/rotate_api_key.py` | New — `create_iam_access_key`, `delete_iam_access_key` |
| `backend/app/connectors/aws/actions/rds_rotate.py` | New — `rotate_rds_master_password`, `update_ssm_parameter` |
| `backend/app/connectors/okta/actions/rotate_api_key.py` | New — `rotate_okta_api_key` |
| `backend/app/connectors/okta/actions/rotate_service_account.py` | New — `update_okta_password` |
| `backend/app/connectors/active_directory/actions/rotate_service_account.py` | New — `update_ad_password` |
| `backend/app/api/change_types.py` | Register `rotate_db_credentials`, `rotate_ssh_keys`, `rotate_api_key`, `rotate_service_account` change types with parameter schemas |
| `backend/alembic/versions/XXXX_secret_versions.py` | Migration — add `secret_versions` table with `status`, `created_at`, `secret_id` FK |
| `frontend/src/pages/Changes.tsx` | Add UI for the four new change types: parameter forms, propagation target builder |
