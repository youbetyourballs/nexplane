# Sub-project 6b: New Cloud & Discovery Connectors — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Four new production connectors: GCP (Google Cloud Platform), RunZero (network discovery), Wiz (CSPM/CNAPP), and Microsoft Entra ID (standalone M365 identity).

---

## Design Decisions

- **All new connector types have clean names** (no `_mock` suffix): `gcp`, `runzero`, `wiz`, `entra_id`
- **Real-if-credentials, mock-if-not pattern** for all executors
- **Single Alembic migration (010)** adds all 4 new enum values
- **Python SDKs:** `google-cloud-*` (GCP), `requests` (RunZero REST, Wiz GraphQL, Entra via MS Graph)

---

## Connector 1: GCP

**Credential fields:** `project_id` (string, required), `service_account_key_json` (password, required — full JSON of service account key)

**Auth pattern:** `google.oauth2.service_account.Credentials.from_service_account_info(json.loads(key_json))`

| action_id | type | description |
|-----------|------|-------------|
| `discover_compute_instances` | ingest | All GCE instances: name, zone, machine type, status, internal/external IPs, labels, service account |
| `discover_iam_bindings` | ingest | Project-level IAM policy bindings: member, role, condition |
| `discover_storage_buckets` | ingest | GCS buckets: public access, versioning, encryption, IAM |
| `discover_firewall_rules` | ingest | VPC firewall rules: direction, source ranges, target tags, ports |
| `discover_service_accounts` | ingest | Service accounts: key age, roles, disabled status |
| `ingest_scc_findings` | ingest | Security Command Center findings — enriches assets with threat/misconfiguration tags |
| `stop_instance` | change | Stop a GCE instance. Rollback: start. |
| `start_instance` | change | Start a stopped GCE instance. |
| `delete_instance` | change | Terminate a GCE instance. |
| `create_firewall_rule` | change | Add VPC firewall rule (ALLOW or DENY). Rollback: delete. |
| `delete_firewall_rule` | change | Remove VPC firewall rule. |
| `block_public_bucket_access` | change | Remove `allUsers`/`allAuthenticatedUsers` IAM bindings from GCS bucket. Rollback restores binding. |
| `disable_service_account` | change | Disable a service account. Rollback: enable. |
| `rotate_service_account_key` | change | Create new service account key, return in result, delete old key. |

**SDK:** `google-cloud-compute>=1.14`, `google-cloud-storage>=2.10`, `google-cloud-iam>=2.13`, `google-cloud-securitycenter>=1.23`

---

## Connector 2: RunZero

**Credential fields:** `api_token` (password, required), `org_id` (string, required — organization UUID from RunZero console)

**Auth pattern:** `Authorization: Bearer {api_token}` header to `https://console.runzero.com/api/v1.0`

| action_id | type | description |
|-----------|------|-------------|
| `discover_assets` | ingest | All network assets: IP, MAC, OS fingerprint, open ports, services, hostname, first/last seen, tags — creates server/network asset records |
| `discover_services` | ingest | Detected network services per asset: port, protocol, service name, banner |
| `discover_wireless_networks` | ingest | Detected WiFi networks: SSID, BSSID, encryption, signal |
| `trigger_scan` | change | Start a RunZero scan task against a target CIDR or asset group. Returns task ID. |
| `get_scan_status` | change | Check status of a running scan task. |

**SDK:** `httpx` (REST API)

**Key value:** RunZero finds everything — including assets with no agents, IoT devices, printers, OT equipment. It's the "unknown unknowns" connector.

---

## Connector 3: Wiz

**Credential fields:** `client_id` (string, required), `client_secret` (password, required), `tenant_id` (string, required — Wiz tenant URL fragment)

**Auth pattern:** OAuth2 client credentials to `https://auth.app.wiz.io/oauth/token`, then GraphQL API at `https://api.us1.app.wiz.io/graphql` (or `api.eu1.app.wiz.io` depending on region)

| action_id | type | description |
|-----------|------|-------------|
| `discover_cloud_resources` | ingest | All cloud resources indexed by Wiz across connected cloud accounts: resource type, account, region, tags |
| `ingest_issues` | ingest | Wiz security issues (misconfigurations, vulnerabilities, secrets): severity, resource, rule, status — enriches assets with wiz-issue tags |
| `ingest_vulnerabilities` | ingest | Wiz vulnerability findings from OS packages and container images |
| `ingest_attack_paths` | ingest | Critical attack paths identified by Wiz — flags assets that are on a path to sensitive data or admin access |
| `resolve_issue` | change | Mark a Wiz issue as resolved with a reason note. |
| `accept_risk` | change | Accept risk on a Wiz issue for a specified duration. |

**SDK:** `requests` or `gql` (GraphQL client). Wiz API is GraphQL.

---

## Connector 4: Microsoft Entra ID

**Credential fields:** `tenant_id` (string, required), `client_id` (string, required), `client_secret` (password, required)

**Auth pattern:** MSAL client credentials flow → Microsoft Graph API `https://graph.microsoft.com/v1.0/`

**Note:** This is a standalone connector for orgs using M365/Entra without full Azure subscription management. The Azure connector also has Entra actions but this provides identity-only coverage.

| action_id | type | description |
|-----------|------|-------------|
| `discover_users` | ingest | All Entra users: UPN, MFA status, last sign-in, assigned roles, license, account enabled state |
| `discover_groups` | ingest | All Entra groups: members, type (security/M365/distribution), owners |
| `discover_applications` | ingest | Registered applications and service principals |
| `discover_conditional_access` | ingest | Conditional Access policies: state, conditions, grant controls |
| `discover_privileged_roles` | ingest | Users with privileged roles (Global Admin, Security Admin, etc.) |
| `disable_user` | change | Set accountEnabled=false for a user. Rollback: enable. |
| `enable_user` | change | Re-enable a user. |
| `reset_mfa` | change | Delete all MFA auth methods, forcing re-enrollment. |
| `revoke_sessions` | change | Revoke all active sign-in sessions via `revokeSignInSessions`. |
| `remove_from_role` | change | Remove a user from a directory role. |
| `block_sign_in` | change | Disable sign-in + revoke sessions in one operation. Rollback: re-enable + note that sessions cannot be restored. |

**SDK:** `msal>=1.28`, `requests`

---

## Section 5: New Files Summary

**Migration:** `backend/alembic/versions/010_add_new_connector_types_6b.py`
- Adds enum values: `gcp`, `runzero`, `wiz`, `entra_id`

**Model:** `backend/app/models/connector.py` — add 4 new ConnectorType values

**Catalogs:**
- `backend/app/connectors/catalog/gcp.json`
- `backend/app/connectors/catalog/runzero.json`
- `backend/app/connectors/catalog/wiz.json`
- `backend/app/connectors/catalog/entra_id.json`

**Executors:**
- `backend/app/connectors/executors/gcp/` (_client.py + 14 executor files)
- `backend/app/connectors/executors/runzero/` (_client.py + 5 executor files)
- `backend/app/connectors/executors/wiz/` (_client.py + 6 executor files)
- `backend/app/connectors/executors/entra_id/` (_client.py + 11 executor files)

**Frontend:**
- `frontend/src/types/api.ts` — add 4 ConnectorType values
- `frontend/src/pages/Connectors.tsx` — add labels + icons for 4 connectors
- `frontend/src/components/AddConnectorModal.tsx` — add to dropdowns

**Requirements:**
```
google-cloud-compute>=1.14.0
google-cloud-storage>=2.10.0
google-cloud-iam>=2.13.0
google-cloud-securitycenter>=1.23.0
msal>=1.28.0
```
