# Connector Robustness & Asset Type Expansion Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Apply lessons learned from AWS EC2 live-testing to all connectors and asset types — eliminating fire-and-forget races, catalog gaps, dedup collisions, stale metadata, and missing asset classifications.

**Architecture:** Five sequenced sub-projects. Each is independently shippable. Order: catalog completeness → ingest dedup → asset type expansion → post-completion discovery → waiter hardening.

**Tech Stack:** FastAPI/SQLAlchemy backend, React/TanStack Query frontend, PostgreSQL, Alembic migrations, boto3 for AWS, httpx-based clients for Okta/CrowdStrike/Cloudflare.

---

## Lessons Learned (from AWS EC2 Live Testing)

1. **Fire-and-forget races** — `stop_instances`/`terminate_instances` return before AWS completes. Fixed with boto3 waiters. Same pattern needed for Okta suspend, AD disable, CrowdStrike isolate, Cloudflare DNS.
2. **Catalog gaps** — `create_ebs_snapshot` executor existed but wasn't in `aws.json`, causing `connector_type: "unknown"` in plans and `KeyError` at execution. Every executor needs a catalog entry.
3. **Ingest dedup by name** — two EC2 instances named `my-new-instance` collapsed into one asset. Fixed by deduping on `instance_id` metadata field. Same needed for all connectors.
4. **Post-completion discovery** — after ec2_launch/stop/terminate, inventory stayed stale until manual scan. Automated discovery trigger needed for all stateful change types.
5. **Asset metadata coverage** — `create_ebs_snapshot` needed `vol-*` ID that wasn't in asset metadata; rollback needed `instance_id` from the completed run, not the stub run. Metadata must cover what executors need.
6. **Rollback result nesting** — execution result stored as `{"execution": {"steps": [...]}}` but rollback read `result.get("steps")`. All result extraction must handle the nested structure.
7. **Wrong asset types** — RDS instances discovered as `server`, S3 buckets as `cloud_account`. New asset types needed for proper classification and targeted change actions.

---

## Sub-Project 1: Catalog Completeness Audit

### What
Every executor module must have a corresponding catalog entry. Every catalog action must have a working executor. Change type definitions must only reference registered generic actions.

### Gaps Found
- `discover_rds_instances` returns `asset_type: "server"` — wrong, needs `database`
- `discover_s3_buckets` returns `asset_type: "cloud_account"` — wrong, needs `storage_bucket`
- `discover_elbs` returns no standardized asset_type — needs `load_balancer`
- `wait_dns_propagation` executor is a stub (always returns `propagated: True`)
- No catalog entries for `entra_id`, `github`, `google_workspace`, `kubernetes` connectors (executors exist, JSONs missing)

### Fix
- Add catalog JSON files for `entra_id`, `github`, `google_workspace`, `kubernetes`
- Fix asset_type returns in `discover_rds_instances`, `discover_s3_buckets`, `discover_elbs`
- Implement real `wait_dns_propagation` using DNS resolver polling

---

## Sub-Project 2: Ingest Dedup by External ID

### What
`IngestService.run()` currently deduplicates by `(org_id, connector_id, name)`. Same-named assets (two EC2 instances, two Okta users named "John Smith") collapse into one record.

### Fix Per Connector
- **AWS EC2**: `instance_id` (already fixed)
- **AWS RDS**: `db_instance_identifier` from metadata
- **AWS S3**: `bucket_name` from metadata
- **Okta**: `okta_user_id` from metadata
- **Active Directory**: `object_guid` from metadata
- **CrowdStrike**: `device_id` from metadata
- **Cloudflare DNS**: `record_id` from metadata

### Mechanism
`IngestService._get_external_id(payload)` — checks `payload.get("id")` then walks known metadata keys in priority order. Dedup query uses `asset_metadata->>'<key>' = value` before falling back to name match (only if no existing asset has that external ID yet).

---

## Sub-Project 3: New Asset Types

### New Types (Backend Enum + Migration)
| Type | Description | Discovered By | Key Metadata Fields |
|------|-------------|---------------|---------------------|
| `database` | RDS instances, managed DBs | AWS RDS, discover_rds | `db_identifier`, `engine`, `endpoint`, `port`, `status` |
| `storage_bucket` | S3 buckets, object stores | AWS S3 discover | `bucket_name`, `region`, `public_access_blocked` |
| `load_balancer` | ELBs, ALBs, NLBs | AWS ELB discover | `lb_arn`, `dns_name`, `scheme`, `type` |
| `endpoint` | CrowdStrike-managed hosts | CrowdStrike discover | `device_id`, `hostname`, `platform`, `containment_status` |
| `container_cluster` | Kubernetes clusters | Kubernetes connector | `cluster_name`, `provider`, `version`, `node_count` |

### Frontend Updates
- Add new types to `AssetType` TypeScript type
- Add icons to `ASSET_TYPE_ICONS` in Assets.tsx
- Add quick actions in AssetDetail.tsx ASSET_ACTIONS:
  - `database`: `rotate_db_credentials`, `create_backup`, `promote_db_replica`, `configure_db_audit`, `provision_db_user`
  - `storage_bucket`: `s3_block_public_access`, `create_backup`, `verify_backup`
  - `load_balancer`: `security_group_update`, `snapshot_asset`
  - `endpoint`: `isolate_host`, `patch_packages`, `telemetry_agent_deploy`, `remote_command`, `enforce_cis_benchmark`
  - `container_cluster`: `helm_upgrade`, `rolling_restart`
- Add asset type filter in `CHANGE_TYPE_ASSET_FILTER` for new types:
  - `rotate_db_credentials` → `database`
  - `promote_db_replica` → `database`
  - `s3_block_public_access` → `storage_bucket`
  - `isolate_host` → `endpoint` (currently `server`, keep both)

### Alembic Migration
Alter `asset_type` enum to add 5 new values. Use `ADD VALUE IF NOT EXISTS` for PostgreSQL enum extension.

---

## Sub-Project 4: Post-Completion Discovery

### What
After any change type that creates, modifies, or destroys an asset, trigger a discovery re-sync on the relevant connector so inventory reflects reality.

### Mapping
```python
_DISCOVERY_CHANGE_TYPES = {
    # AWS EC2 (existing)
    "ec2_launch", "ec2_terminate", "ec2_stop", "ec2_start", "ec2_stop_start",
    # AWS other
    "s3_block_public_access",          # → discover_s3_buckets
    "security_group_update",           # → export_security_group (no full resync needed)
    # Identity
    "offboard_user", "onboard_user",   # → discover_users (Okta + AD)
    "lockdown_account",                # → discover_users
    # DNS
    "dns_update", "dr_failover",       # → discover_dns_records (Cloudflare)
    # Endpoint
    "isolate_host",                    # → discover_endpoints (CrowdStrike)
    # DB
    "promote_db_replica",              # → discover_rds_instances
}

_CONNECTOR_TYPE_TO_DISCOVERY = {
    "aws":         {"server": "discover_ec2_instances", "database": "discover_rds_instances", "storage_bucket": "discover_s3_buckets"},
    "okta":        {"identity": "discover_users"},
    "active_directory": {"identity": "discover_identities"},
    "cloudflare":  {"dns_zone": "discover_dns_records"},
    "crowdstrike": {"endpoint": "discover_endpoints"},
}
```

The `activity_post_completion_discovery` function looks up the CR's target asset types and connector types, then dispatches the appropriate discovery action for each unique (connector, asset_type) pair.

---

## Sub-Project 5: Waiter Hardening

### What
All stateful operations must block until the terminal state is confirmed, not just until the API call returns. Prevents CR from flipping to `completed`/`rolled_back` while the actual operation is still in progress.

### Per Connector

**Okta:**
- `suspend_user` → poll `GET /api/v1/users/{id}` until `status == "SUSPENDED"` (max 30s, 3s interval)
- `deactivate_user` → poll until `status == "DEPROVISIONED"` (max 60s, 5s interval)
- `reactivate_user` → poll until `status == "ACTIVE"` (max 60s, 5s interval)
- `unsuspend_user` → poll until `status == "ACTIVE"` (max 30s, 3s interval)

**Active Directory:**
- `disable_account` → re-query LDAP and check `userAccountControl & 2 == 2` (disabled bit set)
- `enable_account` → re-query LDAP and check `userAccountControl & 2 == 0`
- Max 3 retries, 2s delay each

**CrowdStrike:**
- `isolate_host` → poll `GET /devices/entities/devices/v2` until `network_containment_status == "contained"` (max 120s, 10s interval)
- `restore_host` → poll until `network_containment_status == "normal"` (max 120s, 10s interval)

**Cloudflare:**
- `wait_dns_propagation` → replace stub with real DNS resolver polling using `dnspython` or standard socket. Query Cloudflare's resolver (1.1.1.1) and Google's (8.8.8.8) for the record. Poll every 10s up to TTL seconds (capped at 300s).
- `update_dns_record` → after API call, verify record exists via Cloudflare's own API GET before returning

**AWS (already done for EC2):**
- `block_s3_public_access` → verify via `get_public_access_block` after PUT call
- `attach_iam_policy` → verify via `list_attached_user_policies` after attach
- `disable_iam_user` → verify via `get_login_profile` returns 404 (no console) or `get_user` shows no active keys

---

## Execution Order

1. **Sub-project 2: Catalog completeness** — fixes the silent failure mode where executors exist but aren't routable
2. **Sub-project 3: Ingest dedup** — data integrity before we add more asset types
3. **Sub-project 4: New asset types** — model, migration, frontend, updated discovery return types
4. **Sub-project 5: Post-completion discovery** — extended to cover new asset types and connectors
5. **Sub-project 1: Waiter hardening** — polish that prevents premature status flips

Each sub-project is independently committable and testable.
