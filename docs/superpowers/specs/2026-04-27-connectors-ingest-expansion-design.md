# Connectors & Ingest Expansion — Design Spec (Spec 3)

**Date:** 2026-04-27
**Status:** Approved
**Scope:** Four new mock connectors (Active Directory, CrowdStrike, Tenable, Azure), ingest action type with asset upsert pipeline, new `identity` asset type, expanded PaloAlto and SSH connectors, and 10 example projects seeded into the database.

---

## Problem Statement

Nexplane's existing connectors (AWS, PaloAlto, Okta, SSH, Cloudflare) cover a narrow slice of the security tooling landscape. Security teams routinely work across EDR platforms, vulnerability scanners, cloud providers, and identity directories. Without connectors for these systems, Nexplane cannot represent the full scope of a security initiative, and the AI planning assistant lacks the asset context needed to propose realistic change plans.

Additionally, all existing connectors are action-only — they push changes but never pull data back. Real security workflows begin with discovery: "which hosts are unprotected?", "which accounts are non-compliant?", "what traffic is traversing this firewall?" Ingest actions close this gap by populating Nexplane's asset inventory from authoritative sources.

---

## Core Decisions

- **New `action_type: "ingest"`** added to the catalog schema alongside the existing `"change"`. Ingest executors return a list of asset payloads; a new `IngestService` upserts them into the `assets` table.
- **Deduplication key:** `(organization_id, name)`. If an asset with the same name exists in the org, its metadata, tags, and environment are updated. If not, a new asset is created.
- **New `identity` asset type** added to the `AssetType` enum. Used for users, service accounts, and groups discovered from AD, CrowdStrike, and Tenable.
- **Ingest trigger:** `POST /connectors/{id}/ingest/{action_id}` — runs a single ingest action and returns a summary of assets created/updated. A "Run Discovery" button added to the connector card in the UI.
- **Open ports and running processes** are stored as structured `asset_metadata` on `server` assets (not separate assets), keeping the model manageable.
- **Vulnerability findings** are represented as tags on existing assets (`vuln:CVE-XXXX-YYYY`, `vuln-severity:critical`) rather than a separate findings model — keeps scope tight while making assets filterable by vulnerability.
- **PaloAlto** gets ingest actions added (traffic logs, security events) in addition to two new change actions.
- **SSH** gets two new hardening change actions (SELinux policy, containerize workload).
- **Example projects** are seeded via a new migration or seed script covering 10 security use cases.

---

## Section 1: Schema Changes

### 1.1 New `identity` asset type

Add `identity` to the `AssetType` enum in `backend/app/models/asset.py`:

```python
class AssetType(str, enum.Enum):
    server = "server"
    cloud_account = "cloud_account"
    dns_zone = "dns_zone"
    firewall = "firewall"
    identity_provider = "identity_provider"
    application = "application"
    identity = "identity"          # NEW
```

Update `frontend/src/types/api.ts`:
```typescript
export type AssetType =
  | "server"
  | "cloud_account"
  | "dns_zone"
  | "firewall"
  | "identity_provider"
  | "application"
  | "identity";         // NEW
```

No migration needed — `AssetType` is a Python enum used for validation; the DB column stores strings.

### 1.2 Alembic migration 005: example projects seed

A single migration `005_seed_example_projects.py` inserts the 10 example projects (Section 5) into the database for the default organization. Uses `INSERT ... ON CONFLICT DO NOTHING` so re-running is safe.

---

## Section 2: Catalog Format — Ingest Actions

### 2.1 New fields for ingest actions

```json
{
  "action_id": "discover_endpoints",
  "generic_action": "discover_endpoints",
  "action_type": "ingest",
  "display_name": "Discover Endpoints",
  "description": "Discovers all managed endpoints and their users, applications, and open ports",
  "produces_asset_types": ["server", "identity", "application"],
  "applicable_asset_types": [],
  "executor": "crowdstrike_mock.discover_endpoints",
  "estimated_duration_seconds": 30
}
```

`produces_asset_types` — list of asset types this ingest action creates or updates. Used by the UI to describe what a discovery run will populate.

`applicable_asset_types` — empty array for ingest actions (they are not targeted at specific existing assets; they pull from the source system).

### 2.2 IngestService

`backend/app/services/ingest_service.py`

```python
class IngestService:
    async def run(
        self,
        action_id: str,
        connector,
        organization_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        # 1. Look up executor from catalog
        # 2. Call executor.execute({}, [], connector)
        # 3. For each asset payload returned, upsert into assets table
        # 4. Return {"created": int, "updated": int, "assets": [AssetRead]}
```

Upsert logic: `SELECT` by `(organization_id, name)`. If found, update `asset_metadata`, `tags`, `environment`, `criticality` if provided. If not found, `INSERT` with `asset_type`, `name`, `organization_id`, and all provided fields.

### 2.3 New endpoint

`POST /connectors/{id}/ingest/{action_id}`

- Loads connector (must belong to caller's org)
- Looks up action in catalog — returns 400 if `action_type != "ingest"`
- Calls `IngestService.run()`
- Returns `{"created": int, "updated": int, "assets": [AssetRead]}`

### 2.4 Ingest executor contract

Each ingest executor module has a single `execute` function returning a list of asset dicts:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> list[dict]:
    return [
        {
            "name": "payments-api-01",
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["crowdstrike-managed", "payments"],
            "asset_metadata": {
                "os": "Ubuntu 22.04",
                "sensor_version": "7.14.0",
                "open_ports": [22, 80, 443, 8080],
                "last_seen": "2026-04-27T10:00:00Z"
            }
        },
        ...
    ]
```

---

## Section 3: New Connectors

### 3.1 `active_directory_mock`

**Display name:** Active Directory (Mock)

**Ingest actions:**

| action_id | produces | description |
|---|---|---|
| `discover_computers` | `server` | AD-joined computers with OU path, OS, last logon in metadata; tag: `ad-joined` |
| `discover_identities` | `identity` | Users, service accounts, admin accounts with last login, group memberships, enabled status in metadata; tags: `ad-user`, `ad-service-account`, or `ad-admin` |

**Change actions:**

| action_id | description | rollback |
|---|---|---|
| `disable_account` | Disables a user or service account | `enable_account` |
| `enable_account` | Re-enables a disabled account | — |
| `reset_password` | Forces password reset on next login | — |
| `add_to_group` | Adds account to a security group | `remove_from_group` |
| `remove_from_group` | Removes account from a security group | `add_to_group` |
| `enforce_mfa` | Flags account for mandatory MFA enrollment | — |

**Applicable asset types for change actions:** `identity`

---

### 3.2 `crowdstrike_mock`

**Display name:** CrowdStrike Falcon (Mock)

**Ingest actions:**

| action_id | produces | description |
|---|---|---|
| `discover_endpoints` | `server` | All endpoints with OS, sensor version, last seen, open ports, running processes in metadata; tags: `crowdstrike-managed` (protected) or `crowdstrike-unprotected` (gap) |
| `discover_endpoint_users` | `identity` | Last logged-in user per endpoint; linked to host by matching tag; tags: `crowdstrike-detected-user` |
| `discover_applications` | `application` | Software inventory per endpoint — name, version, vendor, install path in metadata; tags: `crowdstrike-detected` |

**Change actions:**

| action_id | description | rollback |
|---|---|---|
| `deploy_sensor` | Installs CrowdStrike Falcon sensor via SSH | `remove_sensor` |
| `remove_sensor` | Uninstalls sensor | — |
| `isolate_host` | Network-isolates an endpoint via Falcon API | `restore_host` |
| `restore_host` | Lifts network isolation | — |
| `contain_process` | Kills a named process on endpoint | — |

**Applicable asset types for change actions:** `server`

---

### 3.3 `tenable_mock`

**Display name:** Tenable Vulnerability Management (Mock)

**Ingest actions:**

| action_id | produces | description |
|---|---|---|
| `discover_assets` | `server` | Scanned hosts with IP, OS, open ports in metadata; tag: `tenable-scanned` |
| `discover_vulnerabilities` | (enriches existing `server` + `application`) | Adds `vuln:CVE-XXXX-YYYY` and `vuln-severity:critical/high/medium/low` tags to matched assets; creates `application` assets for vulnerable software found |
| `discover_local_accounts` | `identity` | Local OS accounts found on scanned hosts; tags: `tenable-discovered-account` |

**Change actions:**

| action_id | description | rollback |
|---|---|---|
| `trigger_scan` | Launches a Tenable scan against target assets | — |
| `verify_remediation` | Re-scans a specific asset to confirm a finding is resolved | — |

**Applicable asset types for change actions:** `server`

---

### 3.4 `azure_mock`

**Display name:** Microsoft Azure (Mock)

**Ingest actions:**

| action_id | produces | description |
|---|---|---|
| `discover_vms` | `server` | VMs with size, region, OS, public IP, subscription in metadata; tag: `azure` |
| `discover_storage_accounts` | `cloud_account` | Storage accounts with public access status, replication type, encryption state in metadata; tags: `azure-storage`, `azure-public-storage` if misconfigured |
| `discover_nsgs` | `firewall` | NSGs with rule count, associated subnets, any-source rules in metadata; tags: `azure-nsg`, `azure-nsg-permissive` if overly permissive |

**Change actions:**

| action_id | description | rollback |
|---|---|---|
| `update_nsg_rule` | Adds, modifies, or removes a rule on a network security group | `restore_nsg_rule` |
| `restore_nsg_rule` | Restores previous NSG rule state | — |
| `disable_public_blob_access` | Sets public access to disabled on a storage account | `enable_public_blob_access` |
| `enable_public_blob_access` | Re-enables public access (rollback only) | — |
| `rotate_storage_key` | Regenerates a storage account access key | — |

**Applicable asset types for change actions:** `cloud_account` (storage), `firewall` (NSG)

---

## Section 4: Expanded Existing Connectors

### 4.1 `paloalto_mock` — new ingest + change actions

**New ingest actions:**

| action_id | produces | description |
|---|---|---|
| `ingest_traffic_logs` | `application`, enriches `server` + `firewall` | Observed traffic flows become `application` assets (service name, port, protocol, avg byte volume in metadata; tags: `palo-observed`, `high-volume` if above threshold). Updates last-seen-traffic metadata on matched server and firewall assets. |
| `ingest_security_events` | (enriches existing assets) | Tags assets with `palo-threat-detected` when the firewall logged a threat against them; stores threat category in metadata. |

**New change actions:**

| action_id | description | rollback |
|---|---|---|
| `enable_traffic_logging` | Enables enhanced traffic logging for a zone or policy rule | `disable_traffic_logging` |
| `disable_traffic_logging` | Disables enhanced logging (rollback) | — |
| `update_chokepoint_rule` | Modifies a policy rule at an identified traffic chokepoint | `restore_chokepoint_rule` |
| `restore_chokepoint_rule` | Restores previous chokepoint rule (rollback) | — |

### 4.2 `ssh_mock` — new hardening change actions

| action_id | description | rollback |
|---|---|---|
| `apply_selinux_policy` | Applies a named SELinux policy module to a target host | `revert_selinux_policy` |
| `revert_selinux_policy` | Reverts SELinux policy to previous state | — |
| `containerize_workload` | Wraps a running process in a container namespace with cgroup/seccomp restrictions using an approved template | `restore_bare_metal_service` |
| `restore_bare_metal_service` | Removes container wrapper and restores bare-metal service | — |

---

## Section 5: Example Projects

Seeded via migration 005. Each project is `status: draft`, belongs to the default org, and has a descriptive `goal` field used by the AI assistant.

| # | Name | Connectors involved | Goal |
|---|---|---|---|
| 1 | Isolate and investigate compromised endpoint | CrowdStrike, AD, AWS | Contain a suspected compromise: isolate the endpoint, disable the associated user account, and snapshot the machine for forensic review |
| 2 | Deploy EDR to unprotected hosts | CrowdStrike, SSH | Discover hosts missing CrowdStrike sensor coverage and deploy the sensor to all unprotected endpoints |
| 3 | Remediate critical CVE across fleet | Tenable, SSH | Identify all assets affected by a critical CVE, apply the patch via remote command, and verify remediation with a follow-up scan |
| 4 | Offboard departed employee | AD, Okta | Disable the user's Active Directory account, remove them from all security groups, and revoke their Okta API credentials |
| 5 | Remediate public Azure storage | Azure | Discover storage accounts with public blob access enabled and disable public access on all affected accounts |
| 6 | Tighten firewall after vulnerability scan | Tenable, AWS, Azure | Use scan findings to identify unnecessary open ports and tighten the corresponding security group and NSG rules |
| 7 | MFA enforcement for non-compliant accounts | AD | Discover identity assets without MFA enabled and enforce MFA enrollment across all non-compliant accounts |
| 8 | Microsegmentation for payments subnet | PaloAlto, AWS | Analyze current traffic flows to the payments subnet, generate a microsegmentation policy diff, stage and apply the new policy, and update AWS security groups to match |
| 9 | Harden payments-api workload isolation | SSH | Snapshot the payments-api host, apply a SELinux policy to restrict process permissions, and containerize the application for workload isolation |
| 10 | Identify and respond to firewall chokepoints | PaloAlto | Enable enhanced traffic logging, ingest flow data to identify high-volume chokepoints, analyze flows, and update policy rules at identified bottlenecks |

---

## Section 6: New Files

| File | Purpose |
|---|---|
| `backend/app/connectors/catalog/active_directory_mock.json` | AD connector catalog |
| `backend/app/connectors/catalog/crowdstrike_mock.json` | CrowdStrike connector catalog |
| `backend/app/connectors/catalog/tenable_mock.json` | Tenable connector catalog |
| `backend/app/connectors/catalog/azure_mock.json` | Azure connector catalog |
| `backend/app/connectors/executors/active_directory_mock/` | AD executor stubs (one file per action) |
| `backend/app/connectors/executors/crowdstrike_mock/` | CrowdStrike executor stubs |
| `backend/app/connectors/executors/tenable_mock/` | Tenable executor stubs |
| `backend/app/connectors/executors/azure_mock/` | Azure executor stubs |
| `backend/app/services/ingest_service.py` | IngestService — executor dispatch + asset upsert |
| `backend/alembic/versions/005_seed_example_projects.py` | Migration: seed 10 example projects |

## Section 7: Modified Files

| File | Change |
|---|---|
| `backend/app/models/asset.py` | Add `identity` to `AssetType` enum |
| `backend/app/connectors/catalog/paloalto_mock.json` | Add ingest + chokepoint/logging change actions |
| `backend/app/connectors/catalog/ssh_mock.json` | Add SELinux and containerize change actions |
| `backend/app/connectors/executors/paloalto_mock/` | Add executor stubs for new actions |
| `backend/app/connectors/executors/ssh_mock/` | Add executor stubs for new actions |
| `backend/app/routers/connectors.py` | Add `POST /connectors/{id}/ingest/{action_id}` endpoint |
| `backend/app/models/connector.py` | Add `active_directory_mock`, `crowdstrike_mock`, `tenable_mock` to `ConnectorType` enum (`azure_mock` already present) |
| `frontend/src/types/api.ts` | Add `identity` to `AssetType`; add `active_directory_mock`, `crowdstrike_mock`, `tenable_mock` to `ConnectorType` |
| `frontend/src/pages/Connectors.tsx` | Add "Run Discovery" button for connectors with ingest actions |
