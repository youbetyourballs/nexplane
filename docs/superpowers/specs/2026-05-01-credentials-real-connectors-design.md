# Sub-project A: Credential Management & Real Connector Implementations — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Per-connector encrypted credential storage, multi-provider AI key management, inline credential UI on connector cards, and real API implementations for all 10 connectors. SecretsService interface documented as swappable (Fernet-only implementation retained).

---

## Design Decisions

- **Fernet-only now:** SecretsService is already abstract (`encrypt`/`decrypt`). No interface changes. The docstring will note it as the injection point for future Vault/AWS SM backends.
- **Per-connector credentials table:** `ConnectorCredential` (one row per connector) stores an encrypted JSON blob — any connector type holds whatever key-value pairs it needs.
- **Real-if-credentials, mock-if-not:** Every executor checks `connector.credentials`. If populated, makes real API calls. If empty (no credential record), falls back to current mock response. Demo mode is preserved.
- **Connector type names unchanged:** Keep `aws_mock`, `azure_mock`, etc. — the names are identifiers, not behavioral contracts. Executors become real via the credentials pattern.
- **AI providers JSON column:** Replace single `anthropic_api_key_encrypted` with `ai_providers_encrypted` — Fernet-encrypted JSON storing all providers plus default. Old column kept for migration read-back.
- **Catalog `credential_fields`:** Each connector catalog JSON gains a `credential_fields` array describing form inputs, driving the UI dynamically.

---

## Section 1: Data Model

### 1.1 New table: `ConnectorCredential`

```python
class ConnectorCredential(Base):
    __tablename__ = "connector_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("connectors.id"), unique=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
```

### 1.2 Modified: `OrgSettings`

Add column: `ai_providers_encrypted: Mapped[str | None]` — Fernet-encrypted JSON:

```json
{
  "default": "anthropic",
  "providers": {
    "anthropic": {"api_key": "sk-ant-..."},
    "openai": {"api_key": "sk-..."}
  }
}
```

Keep `anthropic_api_key_encrypted` (nullable). Migration logic: on first write to `ai_providers_encrypted`, mark old column as migrated.

### 1.3 Catalog `credential_fields` per connector

Each `backend/app/connectors/catalog/*.json` gains:

```json
"credential_fields": [
  {"name": "field_name", "label": "Display Label", "type": "string|password|select", "required": true|false, "default": "optional"}
]
```

**Per connector:**

| Connector | Credential fields |
|-----------|-------------------|
| aws_mock | access_key_id (string, required), secret_access_key (password, required), region (string, default: us-east-1), session_token (password, optional) |
| azure_mock | tenant_id (string, required), client_id (string, required), client_secret (password, required), subscription_id (string, required) |
| cloudflare_mock | api_token (password, required), zone_id (string, required) |
| active_directory_mock | server (string, required), port (string, default: 389), base_dn (string, required), bind_dn (string, required), bind_password (password, required), use_ssl (string, default: false) |
| crowdstrike_mock | client_id (string, required), client_secret (password, required), base_url (string, default: https://api.crowdstrike.com) |
| tenable_mock | access_key (string, required), secret_key (password, required) |
| paloalto_mock | hostname (string, required), username (string, required), password (password, required) |
| okta_mock | org_url (string, required), api_token (password, required) |
| ssh_runner_mock | hostname (string, required), port (string, default: 22), username (string, required), private_key (password, optional), password (password, optional) |
| nexplane_agent | (empty — uses agent_secret from OrgSettings, no per-connector creds) |

---

## Section 2: Backend API

### 2.1 Connector credential endpoints (added to `connectors.py`)

```
GET  /connectors/{id}/credentials       → {configured: bool, fields: [...]}
PUT  /connectors/{id}/credentials       → body: {credentials: {key: value}}  → 200
DELETE /connectors/{id}/credentials     → 204
```

- `GET`: never returns decrypted values. Returns `credential_fields` from catalog (for form rendering) plus `configured: bool`.
- `PUT`: validates all `required` fields are present, encrypts JSON blob, upserts `ConnectorCredential` row.
- `DELETE`: removes the credential record.
- All require admin role.

### 2.2 AI provider endpoints (added to `settings.py`)

```
GET    /settings/ai-providers                       → {default: str, providers: {name: {configured: bool}}}
PUT    /settings/ai-providers/{provider}            → body: {api_key: str}  → 200
DELETE /settings/ai-providers/{provider}            → 204
PUT    /settings/ai-providers/default               → body: {provider: str}  → 200
```

- Supported providers: `anthropic`, `openai`
- `PUT /{provider}`: validates key format (`sk-ant-` for Anthropic, `sk-` for OpenAI), stores in `ai_providers_encrypted` JSON.
- `PUT /default`: provider must be currently configured.
- `GET`: never returns key values, only `configured: bool` per provider.
- Keep `PUT /settings/ai-key` endpoint working (deprecated but not removed) — writes through to `ai_providers_encrypted`.

### 2.3 Executor credential injection

In `connector_service.py` (or wherever executors are dispatched), before calling `executor.execute(parameters, asset_ids, connector)`:

```python
async def _attach_credentials(connector: Connector, db: AsyncSession) -> None:
    """Decrypt and attach credentials to connector object for executor use."""
    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        connector.credentials = secrets_service.decrypt_json(cred_row.credentials_encrypted)
    else:
        connector.credentials = {}
```

The `connector` object receives a `credentials: dict` attribute. Executors read from it:

```python
creds = connector.credentials  # {} if unconfigured → falls back to mock
if not creds:
    return mock_response(...)
```

Add `decrypt_json` / `encrypt_json` helpers to `SecretsService` that handle `json.dumps`/`json.loads` around the existing `encrypt`/`decrypt`.

---

## Section 3: Real Connector Implementations

All executors live in `backend/app/connectors/executors/{connector_type}/`. Existing mock files are replaced in-place with real+fallback implementations.

### Pattern for every executor

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)  # preserve demo mode
    return await _real_execute(parameters, asset_ids, creds)

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "note": "mock rollback"}
    return await _real_rollback(parameters, execution_result, creds)
```

### 3.1 AWS (`aws_mock/`)

**SDK:** `boto3`
**Auth:** `boto3.client('ec2', aws_access_key_id=..., aws_secret_access_key=..., region_name=..., aws_session_token=...)`

| Action file | Real implementation |
|-------------|---------------------|
| `describe_security_groups.py` | `ec2.describe_security_groups()` — returns groups filtered by VPC/name from parameters |
| `update_security_group.py` | `ec2.authorize_security_group_ingress()` or `revoke_security_group_ingress()` based on `action` param |
| `create_snapshot.py` | `ec2.create_snapshot(VolumeId=..., Description=...)` |
| `delete_snapshot.py` | `ec2.delete_snapshot(SnapshotId=...)` |
| `rotate_access_key.py` | `iam.create_access_key(UserName=...)` then `iam.delete_access_key(...)` — returns new key in result |
| `list_instances.py` | `ec2.describe_instances()` — returns instance inventory for ingest |

**Rollback:** `update_security_group` rollback reverses the rule (authorize↔revoke). `delete_snapshot` rollback is no-op (snapshots can't be undeleted — return warning). `rotate_access_key` rollback: can't undo key rotation safely — return warning with old key ID.

### 3.2 Azure (`azure_mock/`)

**SDK:** `azure-identity` (ClientSecretCredential), `azure-mgmt-network`, `azure-mgmt-compute`, `azure-storage-blob`
**Auth:** `ClientSecretCredential(tenant_id, client_id, client_secret)`

| Action file | Real implementation |
|-------------|---------------------|
| `list_vms.py` | `ComputeManagementClient.virtual_machines.list_all()` |
| `list_nsgs.py` | `NetworkManagementClient.network_security_groups.list_all()` |
| `update_nsg_rule.py` | `network_client.security_rules.begin_create_or_update(...)` |
| `delete_nsg_rule.py` | `network_client.security_rules.begin_delete(...)` |
| `set_blob_public_access.py` | `StorageManagementClient.blob_services.set_service_properties(...)` with `PublicAccess=None` |
| `regenerate_storage_key.py` | `StorageManagementClient.storage_accounts.regenerate_key(...)` |

### 3.3 Cloudflare (`cloudflare_mock/`)

**SDK:** `httpx` (direct REST — Cloudflare API v4 via `https://api.cloudflare.com/client/v4/`)
**Auth:** `Authorization: Bearer {api_token}` header

| Action file | Real implementation |
|-------------|---------------------|
| `list_dns_records.py` | `GET /zones/{zone_id}/dns_records` |
| `create_dns_record.py` | `POST /zones/{zone_id}/dns_records` body: `{type, name, content, ttl, proxied}` |
| `update_dns_record.py` | `PATCH /zones/{zone_id}/dns_records/{record_id}` |
| `delete_dns_record.py` | `DELETE /zones/{zone_id}/dns_records/{record_id}` |

**Rollback:** `create_dns_record` rollback deletes the created record (ID in result). `delete_dns_record` rollback re-creates from snapshot in result. `update_dns_record` rollback re-applies previous values from snapshot.

### 3.4 Active Directory (`active_directory_mock/`)

**SDK:** `ldap3`
**Auth:** `Connection(Server(server, port, use_ssl), bind_dn, bind_password, auto_bind=True)`

| Action file | Real implementation |
|-------------|---------------------|
| `disable_account.py` | `conn.modify(user_dn, {'userAccountControl': [(MODIFY_REPLACE, [514])]})` (514 = disabled) |
| `enable_account.py` | `conn.modify(user_dn, {'userAccountControl': [(MODIFY_REPLACE, [512])]})` (512 = normal) |
| `reset_password.py` | `conn.modify(user_dn, {'unicodePwd': [(MODIFY_REPLACE, [encoded_pw])]})` |
| `force_password_reset.py` | Set `pwdLastSet` to `0` |
| `list_group_members.py` | `conn.search(base_dn, f'(memberOf={group_dn})', attributes=['sAMAccountName', 'mail'])` |
| `add_to_group.py` | `conn.modify(group_dn, {'member': [(MODIFY_ADD, [user_dn])]})` |
| `remove_from_group.py` | `conn.modify(group_dn, {'member': [(MODIFY_DELETE, [user_dn])]})` |
| `get_computers.py` | `conn.search(base_dn, '(objectClass=computer)', attributes=[...])` — ingest |

**Rollback:** `disable_account` rollback calls `enable_account`. `add_to_group` rollback calls `remove_from_group`.

### 3.5 CrowdStrike (`crowdstrike_mock/`)

**SDK:** `falconpy` (FalconPy SDK — `pip install crowdstrike-falconpy`)
**Auth:** `OAuth2(client_id=..., client_secret=..., base_url=...)`

| Action file | Real implementation |
|-------------|---------------------|
| `list_devices.py` | `Hosts.query_devices_by_filter()` + `Hosts.get_device_details()` — ingest |
| `get_device_details.py` | `Hosts.get_device_details(ids=[device_id])` |
| `isolate_host.py` | `Hosts.perform_action(action_name='contain', ids=[device_id])` |
| `lift_containment.py` | `Hosts.perform_action(action_name='lift_containment', ids=[device_id])` |
| `deploy_sensor.py` | `SensorDownload.get_sensor_installer_ids()` → return download token/URL; actual install via agent |
| `list_unprotected_hosts.py` | Cross-reference asset inventory with CrowdStrike device list — ingest |

**Rollback:** `isolate_host` rollback calls `lift_containment`. `lift_containment` rollback calls `isolate_host`.

### 3.6 Tenable (`tenable_mock/`)

**SDK:** `pytenable` (`TenableIO(access_key, secret_key)`)

| Action file | Real implementation |
|-------------|---------------------|
| `list_assets.py` | `tio.assets.list()` — ingest |
| `list_vulnerabilities.py` | `tio.exports.vulns()` filtered by severity |
| `launch_scan.py` | `tio.scans.launch(scan_id)` — returns scan UUID |
| `get_scan_status.py` | `tio.scans.status(scan_id)` |
| `get_scan_results.py` | `tio.scans.results(scan_id)` |
| `list_critical_vulns.py` | Export filtered to `severity >= high` for specific asset |
| `verify_remediation.py` | Launch targeted scan + poll status + return updated vuln count |

### 3.7 Palo Alto (`paloalto_mock/`)

**SDK:** `pan-os-python` (`panos` library)
**Auth:** `Firewall(hostname, api_username=username, api_password=password)` or `api_key`

| Action file | Real implementation |
|-------------|---------------------|
| `get_security_rules.py` | `Rulebase.refreshall()` — list all security policies |
| `create_security_rule.py` | `SecurityRule(name, ...).create()` + `fw.commit()` |
| `delete_security_rule.py` | `rule.delete()` + `fw.commit()` |
| `create_address_object.py` | `AddressObject(name, value, type).create()` |
| `commit_policy.py` | `fw.commit()` — explicit commit endpoint |
| `enable_traffic_logging.py` | Modify rule `log_start`/`log_end` + commit |
| `get_traffic_logs.py` | `fw.op('show log traffic dir=both')` — parse XML response |
| `microsegmentation_policy.py` | Create deny rule between source/destination zones + commit |

**Rollback:** Rule creation rollback deletes the rule + commits. Rule deletion rollback re-creates from result snapshot + commits.

### 3.8 Okta (`okta_mock/`)

**SDK:** `okta` (official Okta Python SDK) or `httpx` to Okta REST API v2
**Auth:** `OktaClient({"orgUrl": org_url, "token": api_token})`

| Action file | Real implementation |
|-------------|---------------------|
| `list_users.py` | `client.list_users(query_params={...})` — ingest |
| `suspend_user.py` | `client.suspend_user(user_id)` |
| `unsuspend_user.py` | `client.unsuspend_user(user_id)` |
| `deactivate_user.py` | `client.deactivate_user(user_id)` |
| `reset_password.py` | `client.reset_password(user_id)` — sends reset email |
| `reset_mfa_factors.py` | `client.reset_factors(user_id)` |
| `list_group_members.py` | `client.list_group_users(group_id)` |
| `force_password_expiry.py` | `client.expire_password(user_id)` |

**Rollback:** `suspend_user` rollback calls `unsuspend_user`. `deactivate_user` rollback calls `client.activate_user(user_id)`.

### 3.9 SSH (`ssh_runner_mock/`)

**SDK:** `paramiko`
**Auth:** `SSHClient` with private key or password; `known_hosts` policy: `AutoAddPolicy` (acceptable for managed infrastructure)

| Action file | Real implementation |
|-------------|---------------------|
| `execute_command.py` | `ssh.exec_command(command)` — command allowlist enforced: only pre-approved commands execute |
| `check_service_status.py` | `ssh.exec_command(f'systemctl status {service}')` |
| `get_systemd_status.py` | `ssh.exec_command('systemctl list-units --failed')` |
| `tail_log.py` | `ssh.exec_command(f'tail -n {lines} {log_path}')` — path allowlist enforced |
| `install_agent.py` | SCP agent binary then `ssh.exec_command('./nexplane-agent --mode ephemeral ...')` |
| `apply_selinux_policy.py` | SCP policy file then `ssh.exec_command('semodule -i ...')` |

**Command allowlist** (hard-coded, cannot be overridden by parameters):
```python
ALLOWED_COMMANDS = {
    "systemctl status": True,
    "systemctl list-units": True,
    "tail -n": True,
    "journalctl": True,
    "df -h": True,
    "free -h": True,
    "uptime": True,
}
```
Any command not matching an allowlist prefix returns an error.

**Rollback:** `execute_command` — no rollback (idempotent commands only). `install_agent` rollback: stop agent process + remove binary.

### 3.10 Nexplane Agent (`nexplane_agent_mock/`)

The agent connector dispatches jobs to real registered agents via the internal `/agent/jobs` API. The mock executor stubs currently return fake data immediately. Real implementation polls for job completion.

**Pattern for all agent executor files:**

```python
import httpx
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_secret = creds.get("agent_secret") or getattr(connector, "agent_secret", None)
    if not agent_secret or not asset_ids:
        return _mock_response(parameters)

    # Find registered agent for the target asset
    # Dispatch job via internal API
    # Poll /agent/jobs/{id} until completed or timeout (30s)
    ...
```

The internal API call uses the org's `agent_secret` (from `OrgSettings`) not a per-connector credential. The `nexplane_agent` connector type has empty `credential_fields`.

---

## Section 4: Frontend

### 4.1 Connector cards (`frontend/src/components/ConnectorCard.tsx` — new component or in `Connectors.tsx`)

Each connector card shows:
- **Credential status indicator:** lock icon, green (`text-green-500`) if configured, amber (`text-amber-500`) if not
- **"Configure Credentials" button:** opens `CredentialModal` — only shown if `credential_fields` is non-empty
- On save: `PUT /connectors/{id}/credentials` → toast "Credentials saved" → refresh status

**`CredentialModal` component** (`frontend/src/components/CredentialModal.tsx`):
- Dynamic form driven by `credential_fields` from `GET /connectors/{id}/credentials`
- `type: "password"` fields render as `<input type="password">` (masked, never echoed)
- "Clear credentials" danger button → `DELETE /connectors/{id}/credentials`
- Closes on save/cancel

### 4.2 Settings page AI Providers section (`Settings.tsx`)

Replace "AI Key" card with "AI Providers" section:
- Two rows: Anthropic, OpenAI
- Each row: provider name + badge (Configured / Not configured) + "Update Key" button + "Remove" link + "Set as Default" radio button
- "Set as Default" radio is disabled for unconfigured providers
- "Update Key" opens inline text input (password type), save calls `PUT /settings/ai-providers/{provider}`

---

## Section 5: New Python Dependencies

Add to `backend/requirements.txt`:

```
boto3>=1.35.0
azure-identity>=1.19.0
azure-mgmt-network>=27.0.0
azure-mgmt-compute>=33.0.0
azure-mgmt-storage>=22.0.0
azure-storage-blob>=12.23.0
ldap3>=2.9.1
crowdstrike-falconpy>=1.4.0
pytenable>=1.5.0
pan-os-python>=1.12.0
okta>=2.9.0
paramiko>=3.5.0
```

---

## Section 6: New / Modified Files

| File | Change |
|------|--------|
| `backend/app/models/connector_credential.py` | New — `ConnectorCredential` SQLAlchemy model |
| `backend/app/models/__init__.py` | Import `ConnectorCredential` |
| `backend/app/models/org_settings.py` | Add `ai_providers_encrypted` column |
| `backend/app/schemas/connector.py` | Add `CredentialRead`, `CredentialWrite` schemas |
| `backend/app/schemas/settings.py` | Add `AIProviderRead`, `AIProviderWrite`, `AIProvidersRead` schemas |
| `backend/app/routers/connectors.py` | Add credential CRUD endpoints |
| `backend/app/routers/settings.py` | Add AI provider endpoints, deprecate `ai-key` |
| `backend/app/services/secrets_service.py` | Add `encrypt_json`/`decrypt_json` helpers |
| `backend/app/services/connector_service.py` | Add `_attach_credentials` injection |
| `backend/app/services/ai_service.py` | Read from `ai_providers_encrypted`, use default provider |
| `backend/alembic/versions/007_connector_credentials_ai_providers.py` | New migration |
| `backend/app/connectors/catalog/*.json` | Add `credential_fields` to all 10 catalogs |
| `backend/app/connectors/executors/aws_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/azure_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/cloudflare_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/active_directory_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/crowdstrike_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/tenable_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/paloalto_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/okta_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/ssh_mock/*.py` | Replace with real+fallback implementations |
| `backend/app/connectors/executors/nexplane_agent_mock/*.py` | Replace with polling real implementations |
| `backend/app/tests/test_connector_credentials.py` | New — credential CRUD tests |
| `backend/app/tests/test_ai_providers.py` | New — AI provider management tests |
| `frontend/src/components/CredentialModal.tsx` | New — credential configuration modal |
| `frontend/src/pages/Connectors.tsx` | Add credential status + Configure button |
| `frontend/src/pages/Settings.tsx` | Replace AI Key with AI Providers section |
| `frontend/src/types/api.ts` | Add credential and AI provider types |
