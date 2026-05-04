# Connector Robustness & Asset Type Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five classes of platform bugs found during AWS EC2 live testing and apply them systematically across all connectors and asset types.

**Architecture:** Five sequenced tasks: (1) fix discovery executors to use correct asset types, (2) harden ingest dedup with per-connector external IDs, (3) add five new asset types with migration + frontend, (4) extend post-completion discovery to cover all stateful change types, (5) add waiters to Okta/AD/CrowdStrike/Cloudflare executors.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, PostgreSQL, React 18 + TanStack Query v5, boto3, httpx, ldap3, falconpy.

---

## Task 1: Fix Discovery Executors — Correct Asset Types

RDS instances currently return `asset_type: "server"`, S3 buckets return `asset_type: "cloud_account"`, and ELBs return `asset_type: "server"`. These will become `database`, `storage_bucket`, and `load_balancer` once the new enum values exist (Task 3), but the executor strings need updating now so they're ready.

**Files:**
- Modify: `backend/app/connectors/executors/aws/discover_rds_instances.py`
- Modify: `backend/app/connectors/executors/aws/discover_s3_buckets.py`
- Modify: `backend/app/connectors/executors/aws/discover_elbs.py`

- [ ] **Step 1: Fix discover_rds_instances asset_type and metadata**

Replace the full file:

```python
import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_rds_instances",
        "assets": [
            {
                "id": "arn:aws:rds:us-east-1:123:db:prod-db",
                "name": "prod-db",
                "asset_type": "database",
                "asset_metadata": {
                    "db_identifier": "prod-db",
                    "engine": "postgres",
                    "engine_version": "15.3",
                    "endpoint": "prod-db.abc123.us-east-1.rds.amazonaws.com",
                    "port": 5432,
                    "publicly_accessible": False,
                    "status": "available",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        paginator = rds.get_paginator('describe_db_instances')
        assets = []
        for page in paginator.paginate():
            for db in page['DBInstances']:
                endpoint = db.get('Endpoint', {})
                assets.append({
                    "id": db['DBInstanceArn'],
                    "name": db['DBInstanceIdentifier'],
                    "asset_type": "database",
                    "asset_metadata": {
                        "db_identifier": db['DBInstanceIdentifier'],
                        "engine": db.get('Engine'),
                        "engine_version": db.get('EngineVersion'),
                        "endpoint": endpoint.get('Address'),
                        "port": endpoint.get('Port'),
                        "publicly_accessible": db.get('PubliclyAccessible', False),
                        "status": db.get('DBInstanceStatus'),
                        "multi_az": db.get('MultiAZ', False),
                    },
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_rds_instances", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
```

- [ ] **Step 2: Fix discover_s3_buckets asset_type and metadata**

```python
import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_s3_buckets",
        "assets": [
            {
                "id": "arn:aws:s3:::my-public-bucket",
                "name": "my-public-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-public-bucket",
                    "public_access_blocked": False,
                    "region": "us-east-1",
                    "provider": "aws",
                },
            },
            {
                "id": "arn:aws:s3:::my-private-bucket",
                "name": "my-private-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-private-bucket",
                    "public_access_blocked": True,
                    "region": "us-west-2",
                    "provider": "aws",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        response = s3.list_buckets()
        assets = []
        for bucket in response.get('Buckets', []):
            name = bucket['Name']
            try:
                pab = s3.get_public_access_block(Bucket=name)['PublicAccessBlockConfiguration']
                blocked = all([
                    pab.get('BlockPublicAcls'), pab.get('IgnorePublicAcls'),
                    pab.get('BlockPublicPolicy'), pab.get('RestrictPublicBuckets'),
                ])
            except Exception:
                blocked = False
            try:
                loc = s3.get_bucket_location(Bucket=name)['LocationConstraint'] or 'us-east-1'
            except Exception:
                loc = 'unknown'
            assets.append({
                "id": f"arn:aws:s3:::{name}",
                "name": name,
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": name,
                    "public_access_blocked": blocked,
                    "region": loc,
                    "provider": "aws",
                },
            })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_s3_buckets", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
```

- [ ] **Step 3: Fix discover_elbs asset_type and metadata**

```python
import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_elbs",
        "assets": [
            {
                "id": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/prod-alb/abc",
                "name": "prod-alb",
                "asset_type": "load_balancer",
                "asset_metadata": {
                    "lb_arn": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/prod-alb/abc",
                    "lb_type": "application",
                    "scheme": "internet-facing",
                    "state": "active",
                    "dns_name": "prod-alb-123.us-east-1.elb.amazonaws.com",
                    "provider": "aws",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, 'elbv2')
    loop = asyncio.get_event_loop()

    def _call():
        paginator = elbv2.get_paginator('describe_load_balancers')
        assets = []
        for page in paginator.paginate():
            for lb in page['LoadBalancers']:
                assets.append({
                    "id": lb['LoadBalancerArn'],
                    "name": lb['LoadBalancerName'],
                    "asset_type": "load_balancer",
                    "asset_metadata": {
                        "lb_arn": lb['LoadBalancerArn'],
                        "lb_type": lb.get('Type'),
                        "scheme": lb.get('Scheme'),
                        "state": lb.get('State', {}).get('Code'),
                        "dns_name": lb.get('DNSName'),
                        "provider": "aws",
                    },
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_elbs", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/aws/discover_rds_instances.py \
        backend/app/connectors/executors/aws/discover_s3_buckets.py \
        backend/app/connectors/executors/aws/discover_elbs.py
git commit -m "fix: correct asset_type returns in AWS discovery executors (database, storage_bucket, load_balancer)"
```

---

## Task 2: Harden Ingest Dedup with Per-Connector External IDs

The current `IngestService` deduplicates assets by `instance_id` metadata key only. Every connector has a different external ID field. This task generalises the lookup.

**Files:**
- Modify: `backend/app/services/ingest_service.py`

- [ ] **Step 1: Replace the external_id extraction and dedup query**

Replace the entire `ingest_service.py` file:

```python
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetType, Environment, Criticality
from app.connectors.catalog_service import ActionCatalogService

# Per-connector metadata key used as the stable external ID for deduplication.
# Prevents same-named assets (two EC2s, two Okta users) from colliding.
_EXTERNAL_ID_KEYS: dict[str, str] = {
    "aws":              "instance_id",       # EC2 — also covers RDS via db_identifier below
    "aws_rds":          "db_identifier",
    "aws_s3":           "bucket_name",
    "aws_elb":          "lb_arn",
    "okta":             "okta_user_id",
    "active_directory": "object_guid",
    "crowdstrike":      "device_id",
    "cloudflare":       "record_id",
}

# Ordered list of metadata keys to try when looking up the external ID,
# regardless of connector type. First non-null value wins.
_METADATA_ID_CANDIDATES = [
    "instance_id", "db_identifier", "bucket_name", "lb_arn",
    "okta_user_id", "object_guid", "device_id", "record_id",
]


def _extract_external_id(payload: dict) -> str | None:
    """Return the stable external ID from the payload, checking top-level 'id'
    then walking known metadata keys."""
    if payload.get("id"):
        return str(payload["id"])
    meta = payload.get("asset_metadata") or {}
    for key in _METADATA_ID_CANDIDATES:
        if meta.get(key):
            return str(meta[key])
    return None


class IngestService:
    def __init__(self, catalog: ActionCatalogService):
        self._catalog = catalog

    async def run(
        self,
        action_id: str,
        connector,
        organization_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        action_def = self._catalog.get_action_def(connector.connector_type, action_id)
        if action_def.get("action_type") != "ingest":
            raise ValueError(f"'{action_id}' is not an ingest action")

        from app.services.connector_service import _attach_credentials
        try:
            await _attach_credentials(connector, db)
        except Exception:
            connector.credentials = {}

        executor = self._catalog.get_executor(connector.connector_type, action_id)
        raw = await executor.execute({}, [], connector)

        if isinstance(raw, dict):
            payloads: list[dict] = raw.get("assets", [])
            auto_asset_payload = raw.get("_auto_asset")
        else:
            payloads = raw
            auto_asset_payload = None

        connector_id = getattr(connector, 'id', None)
        created = 0
        updated = 0
        upserted_assets = []

        if auto_asset_payload:
            from app.services.connector_service import _upsert_auto_asset
            await _upsert_auto_asset(auto_asset_payload, organization_id, db, connector_id=connector_id)

        for payload in payloads:
            name = payload["name"]
            external_id = _extract_external_id(payload)

            base_where = [Asset.organization_id == organization_id]
            if connector_id:
                base_where.append(Asset.connector_id == connector_id)
            else:
                base_where.append(Asset.connector_id.is_(None))

            existing = None

            # Primary dedup: match on any known external ID metadata key
            if external_id:
                for meta_key in _METADATA_ID_CANDIDATES:
                    result = await db.execute(
                        select(Asset).where(
                            *base_where,
                            Asset.asset_metadata[meta_key].as_string() == external_id,
                        )
                    )
                    existing = result.scalar_one_or_none()
                    if existing:
                        break

            # Fallback dedup: name match only if the candidate has no external ID yet
            if existing is None:
                result = await db.execute(
                    select(Asset).where(*base_where, Asset.name == name)
                )
                candidate = result.scalar_one_or_none()
                if candidate:
                    has_ext_id = any(
                        (candidate.asset_metadata or {}).get(k)
                        for k in _METADATA_ID_CANDIDATES
                    )
                    if not has_ext_id:
                        existing = candidate

            if existing:
                if "asset_metadata" in payload:
                    existing.asset_metadata = {**existing.asset_metadata, **payload["asset_metadata"]}
                if "tags" in payload:
                    existing.tags = list(set(existing.tags or []) | set(payload["tags"]))
                if "criticality" in payload:
                    existing.criticality = Criticality(payload["criticality"])
                if "environment" in payload:
                    existing.environment = Environment(payload["environment"])
                db.add(existing)
                upserted_assets.append(existing)
                updated += 1
            else:
                asset_type_raw = payload.get("asset_type")
                if not asset_type_raw:
                    raise ValueError(f"Payload for asset '{name}' is missing required field 'asset_type'")
                asset = Asset(
                    organization_id=organization_id,
                    connector_id=connector_id,
                    name=name,
                    asset_type=AssetType(asset_type_raw),
                    environment=Environment(payload.get("environment", "prod")),
                    criticality=Criticality(payload.get("criticality", "medium")),
                    asset_metadata=payload.get("asset_metadata", {}),
                    tags=payload.get("tags", []),
                )
                db.add(asset)
                await db.flush()
                upserted_assets.append(asset)
                created += 1

        return {"created": created, "updated": updated, "assets": upserted_assets}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/ingest_service.py
git commit -m "feat: generalise ingest dedup to use per-connector external IDs (okta_user_id, device_id, object_guid, etc.)"
```

---

## Task 3: Add New Asset Types — Backend Model, Migration, Frontend

Add `database`, `storage_bucket`, `load_balancer`, `endpoint`, `container_cluster` to the `AssetType` enum everywhere it appears.

**Files:**
- Modify: `backend/app/models/asset.py`
- Create: `backend/alembic/versions/017_add_new_asset_types.py`
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/Assets.tsx` (ASSET_TYPE_ICONS, groupedByEnv filter)
- Modify: `frontend/src/pages/AssetDetail.tsx` (ASSET_ACTIONS)
- Modify: `frontend/src/pages/CreateChangeRequest.tsx` (CHANGE_TYPE_ASSET_FILTER, ASSET_TYPE_LABELS)

- [ ] **Step 1: Update backend AssetType enum**

In `backend/app/models/asset.py`, replace the `AssetType` class:

```python
class AssetType(str, enum.Enum):
    server = "server"
    cloud_account = "cloud_account"
    dns_zone = "dns_zone"
    firewall = "firewall"
    identity_provider = "identity_provider"
    application = "application"
    identity = "identity"
    database = "database"
    storage_bucket = "storage_bucket"
    load_balancer = "load_balancer"
    endpoint = "endpoint"
    container_cluster = "container_cluster"
```

- [ ] **Step 2: Create Alembic migration**

Create `backend/alembic/versions/017_add_new_asset_types.py`:

```python
"""add new asset types

Revision ID: 017
Revises: 016
Create Date: 2026-05-03
"""
from alembic import op

revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'database'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'storage_bucket'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'load_balancer'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'endpoint'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'container_cluster'")


def downgrade():
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
```

- [ ] **Step 3: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected output ends with: `Running upgrade ... -> 017, add new asset types`

- [ ] **Step 4: Update frontend AssetType**

In `frontend/src/types/api.ts`, replace lines 21-28:

```typescript
export type AssetType =
  | "server"
  | "cloud_account"
  | "dns_zone"
  | "firewall"
  | "identity_provider"
  | "application"
  | "identity"
  | "database"
  | "storage_bucket"
  | "load_balancer"
  | "endpoint"
  | "container_cluster";
```

- [ ] **Step 5: Add icons in Assets.tsx**

In `frontend/src/pages/Assets.tsx`, replace the `ASSET_TYPE_ICONS` constant:

```typescript
const ASSET_TYPE_ICONS: Record<AssetType, string> = {
  server: "🖥",
  cloud_account: "☁️",
  dns_zone: "🌐",
  firewall: "🛡",
  identity_provider: "🔑",
  application: "📦",
  identity: "👤",
  database: "🗄",
  storage_bucket: "🪣",
  load_balancer: "⚖️",
  endpoint: "💻",
  container_cluster: "🐳",
};
```

- [ ] **Step 6: Add new types to Assets.tsx filter dropdown**

In `frontend/src/pages/Assets.tsx`, find the `<option>` list in the "All Types" select and replace:

```tsx
{["server", "cloud_account", "dns_zone", "firewall", "identity_provider", "application", "identity", "database", "storage_bucket", "load_balancer", "endpoint", "container_cluster"].map((t) => (
  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
))}
```

Also update the Add Asset form type select to include new types:

```tsx
{["server","cloud_account","dns_zone","firewall","identity_provider","application","identity","database","storage_bucket","load_balancer","endpoint","container_cluster"].map((t) => (
  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
))}
```

- [ ] **Step 7: Add quick actions for new asset types in AssetDetail.tsx**

In `frontend/src/pages/AssetDetail.tsx`, add to the `ASSET_ACTIONS` object after the `application` entry:

```typescript
  database: [
    {
      changeType: "rotate_db_credentials",
      label: "Rotate Credentials",
      title: (a) => `Rotate credentials on ${a.name}`,
      description: (a) => `Rotate database credentials for ${a.name} (${a.asset_metadata?.engine ?? "database"}).`,
    },
    {
      changeType: "create_backup",
      label: "Create Backup",
      title: (a) => `Backup ${a.name}`,
      description: (a) => `Create a backup snapshot of database ${a.name}.`,
    },
    {
      changeType: "provision_db_user",
      label: "Provision DB User",
      title: (a) => `Provision user on ${a.name}`,
      description: (a) => `Create a new database user on ${a.name}.`,
    },
    {
      changeType: "configure_db_audit",
      label: "Configure Audit Logging",
      title: (a) => `Configure audit on ${a.name}`,
      description: (a) => `Enable audit logging on database ${a.name}.`,
    },
    {
      changeType: "promote_db_replica",
      label: "Promote Replica",
      title: (a) => `Promote ${a.name} to primary`,
      description: (a) => `Promote ${a.name} read replica to standalone primary.`,
    },
  ],
  storage_bucket: [
    {
      changeType: "s3_block_public_access",
      label: "Block Public Access",
      title: (a) => `Block public access on ${a.name}`,
      description: (a) => `Enable S3 Block Public Access on bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    {
      changeType: "create_backup",
      label: "Create Backup",
      title: (a) => `Backup ${a.name}`,
      description: (a) => `Create a backup of bucket ${a.name}.`,
    },
  ],
  load_balancer: [
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group on ${a.name}`,
      description: (a) => `Modify security group rules for load balancer ${a.name}.`,
    },
    {
      changeType: "snapshot_asset",
      label: "Snapshot Config",
      title: (a) => `Snapshot ${a.name} config`,
      description: (a) => `Capture current configuration of load balancer ${a.name}.`,
    },
  ],
  endpoint: [
    {
      changeType: "isolate_host",
      label: "Isolate Host",
      title: (a) => `Isolate ${a.name}`,
      description: (a) => `Network-isolate endpoint ${a.name} (device_id: ${a.asset_metadata?.device_id ?? "unknown"}).`,
    },
    {
      changeType: "patch_packages",
      label: "Patch Packages",
      title: (a) => `Patch ${a.name}`,
      description: (a) => `Apply security patches to endpoint ${a.name}.`,
    },
    {
      changeType: "telemetry_agent_deploy",
      label: "Deploy Agent",
      title: (a) => `Deploy agent to ${a.name}`,
      description: (a) => `Deploy telemetry or security agent to endpoint ${a.name}.`,
    },
    {
      changeType: "remote_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute an approved command on endpoint ${a.name}.`,
    },
    {
      changeType: "enforce_cis_benchmark",
      label: "Enforce CIS Benchmark",
      title: (a) => `CIS benchmark on ${a.name}`,
      description: (a) => `Audit and remediate CIS controls on endpoint ${a.name}.`,
    },
  ],
  container_cluster: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Helm upgrade on ${a.name}`,
      description: (a) => `Upgrade a Helm release on cluster ${a.name}.`,
    },
    {
      changeType: "rolling_restart",
      label: "Rolling Restart",
      title: (a) => `Rolling restart on ${a.name}`,
      description: (a) => `Rolling restart of services on cluster ${a.name}.`,
    },
  ],
```

- [ ] **Step 8: Update CHANGE_TYPE_ASSET_FILTER and ASSET_TYPE_LABELS in CreateChangeRequest.tsx**

In `frontend/src/pages/CreateChangeRequest.tsx`, add to `CHANGE_TYPE_ASSET_FILTER`:

```typescript
  rotate_db_credentials: "database",
  promote_db_replica: "database",
  provision_db_user: "database",
  configure_db_audit: "database",
  deprovision_db_user: "database",
  db_permission_change: "database",
  db_connection_config: "database",
  s3_block_public_access: "storage_bucket",
  isolate_host: "endpoint",
```

And add to `ASSET_TYPE_LABELS`:

```typescript
  database: "database",
  storage_bucket: "storage bucket",
  load_balancer: "load balancer",
  endpoint: "endpoint",
  container_cluster: "container cluster",
```

- [ ] **Step 9: Restart services and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
docker compose restart backend
```

Open the Assets page — new asset type filter options should appear. Open CreateChangeRequest — "Rotate DB Credentials" should now pre-filter to `database` assets.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/asset.py \
        backend/alembic/versions/017_add_new_asset_types.py \
        frontend/src/types/api.ts \
        frontend/src/pages/Assets.tsx \
        frontend/src/pages/AssetDetail.tsx \
        frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat: add database, storage_bucket, load_balancer, endpoint, container_cluster asset types"
```

---

## Task 4: Extend Post-Completion Discovery to All Connectors

The current `activity_post_completion_discovery` only handles AWS EC2 and always uses `discover_ec2_instances`. Extend it to cover identity, DNS, endpoint, and database change types.

**Files:**
- Modify: `backend/app/workflows/activities.py`

- [ ] **Step 1: Replace `_DISCOVERY_CHANGE_TYPES` and `_CONNECTOR_DISCOVERY_ACTION` and rewrite `activity_post_completion_discovery`**

Find and replace the section from `_DISCOVERY_CHANGE_TYPES = {` to the end of `activity_post_completion_discovery`. Replace with:

```python
# Change types that trigger asset re-sync after completion.
# Maps change_type → (connector_type, discovery_action_id)
# When multiple connectors are possible (e.g. identity), we pick by the
# target asset's connector_type at runtime.
_CHANGE_TYPE_DISCOVERY: dict[str, list[tuple[str, str]]] = {
    # AWS EC2
    "ec2_launch":      [("aws", "discover_ec2_instances")],
    "ec2_terminate":   [("aws", "discover_ec2_instances")],
    "ec2_stop":        [("aws", "discover_ec2_instances")],
    "ec2_start":       [("aws", "discover_ec2_instances")],
    "ec2_stop_start":  [("aws", "discover_ec2_instances")],
    # AWS other
    "s3_block_public_access": [("aws", "discover_s3_buckets")],
    "promote_db_replica":     [("aws", "discover_rds_instances")],
    # Identity (try okta then active_directory — whichever connector is attached)
    "offboard_user":   [("okta", "discover_users"), ("active_directory", "discover_identities")],
    "onboard_user":    [("okta", "discover_users"), ("active_directory", "discover_identities")],
    "lockdown_account":[("okta", "discover_users"), ("active_directory", "discover_identities")],
    # DNS
    "dns_update":      [("cloudflare", "discover_dns_records")],
    "dr_failover":     [("cloudflare", "discover_dns_records")],
    # Endpoint
    "isolate_host":    [("crowdstrike", "discover_endpoints")],
}


async def activity_post_completion_discovery(change_request_id: str) -> None:
    """Re-run discovery after stateful changes so inventory reflects reality."""
    from app.services.ingest_service import IngestService
    from app.connectors.catalog_service import get_catalog_service

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == uuid.UUID(change_request_id))
            .options(selectinload(ChangeRequest.change_plan))
        )
        cr = result.scalar_one_or_none()
        if not cr:
            return

        candidates = _CHANGE_TYPE_DISCOVERY.get(cr.change_type.value)
        if not candidates:
            return

        # Collect connector IDs referenced in plan steps
        plan = cr.change_plan
        step_connector_ids: list[str] = []
        if plan:
            for step in plan.generated_steps:
                if step.get("connector_id") and step["connector_id"] not in step_connector_ids:
                    step_connector_ids.append(step["connector_id"])

        service = IngestService(get_catalog_service())

        for connector_type, action_id in candidates:
            # Find a matching connector: prefer one used in the plan steps
            connector = None
            for cid in step_connector_ids:
                conn_result = await db.execute(
                    select(Connector).where(
                        Connector.id == uuid.UUID(cid),
                        Connector.connector_type == connector_type,
                    )
                )
                connector = conn_result.scalar_one_or_none()
                if connector:
                    break

            # Fall back: any connector of that type in the org
            if not connector:
                conn_result = await db.execute(
                    select(Connector).where(
                        Connector.organization_id == cr.organization_id,
                        Connector.connector_type == connector_type,
                    )
                )
                connector = conn_result.scalars().first()

            if not connector:
                logger.info(
                    "No %s connector found for post-completion discovery on %s",
                    connector_type, change_request_id,
                )
                continue

            # Verify the action is an ingest action before running
            try:
                from app.connectors.catalog_service import get_catalog_service as _cat
                action_def = _cat().get_action_def(connector_type, action_id)
                if action_def.get("action_type") != "ingest":
                    continue
            except KeyError:
                logger.warning("Discovery action %s not found in %s catalog", action_id, connector_type)
                continue

            try:
                await service.run(action_id, connector, cr.organization_id, db)
                await db.commit()
                logger.info(
                    "Post-completion discovery %s/%s ran for CR %s",
                    connector_type, action_id, change_request_id,
                )
            except Exception as exc:
                logger.warning(
                    "Post-completion discovery %s/%s failed for CR %s: %s",
                    connector_type, action_id, change_request_id, exc,
                )
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/workflows/activities.py
git commit -m "feat: extend post-completion discovery to cover identity, DNS, endpoint, and database change types"
```

---

## Task 5: Waiter Hardening — Okta Lifecycle Actions

Add polling loops to `suspend_user`, `unsuspend_user`, `deactivate_user`, and `reactivate_user` so the executor blocks until Okta confirms the terminal state.

**Files:**
- Modify: `backend/app/connectors/executors/okta/suspend_user.py`
- Modify: `backend/app/connectors/executors/okta/unsuspend_user.py`
- Modify: `backend/app/connectors/executors/okta/deactivate_user.py`
- Modify: `backend/app/connectors/executors/okta/reactivate_user.py`

- [ ] **Step 1: Add `_wait_for_status` helper and update suspend_user.py**

```python
import asyncio
from datetime import datetime, timezone


async def _wait_for_status(user_id: str, target_status: str, creds: dict, max_wait: int = 30, interval: int = 3) -> None:
    """Poll Okta GET /users/{id} until status matches target_status or timeout."""
    import httpx
    from ._client import okta_headers, okta_base
    deadline = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < deadline:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{okta_base(creds)}/users/{user_id}", headers=okta_headers(creds))
            if resp.status_code == 200 and resp.json().get("status") == target_status:
                return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Okta user {user_id} did not reach status '{target_status}' within {max_wait}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "suspend_user", "user_id": user_id, "status": "SUSPENDED", "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/suspend", headers=okta_headers(creds))
        resp.raise_for_status()
    await _wait_for_status(user_id, "SUSPENDED", creds, max_wait=30, interval=3)
    return {"action": "suspend_user", "user_id": user_id, "status": "SUSPENDED", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use unsuspend_user to roll back"}
```

- [ ] **Step 2: Update unsuspend_user.py**

```python
import asyncio
from datetime import datetime, timezone


async def _wait_for_status(user_id: str, target_status: str, creds: dict, max_wait: int = 30, interval: int = 3) -> None:
    import httpx
    from ._client import okta_headers, okta_base
    deadline = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < deadline:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{okta_base(creds)}/users/{user_id}", headers=okta_headers(creds))
            if resp.status_code == 200 and resp.json().get("status") == target_status:
                return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Okta user {user_id} did not reach status '{target_status}' within {max_wait}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "unsuspend_user", "user_id": user_id, "status": "ACTIVE", "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/unsuspend", headers=okta_headers(creds))
        resp.raise_for_status()
    await _wait_for_status(user_id, "ACTIVE", creds, max_wait=30, interval=3)
    return {"action": "unsuspend_user", "user_id": user_id, "status": "ACTIVE", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use suspend_user to roll back"}
```

- [ ] **Step 3: Update deactivate_user.py**

```python
import asyncio
from datetime import datetime, timezone


async def _wait_for_status(user_id: str, target_status: str, creds: dict, max_wait: int = 60, interval: int = 5) -> None:
    import httpx
    from ._client import okta_headers, okta_base
    deadline = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < deadline:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{okta_base(creds)}/users/{user_id}", headers=okta_headers(creds))
            if resp.status_code == 200 and resp.json().get("status") == target_status:
                return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Okta user {user_id} did not reach status '{target_status}' within {max_wait}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "deactivate_user", "user_id": user_id, "status": "DEPROVISIONED", "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/deactivate", headers=okta_headers(creds))
        resp.raise_for_status()
    await _wait_for_status(user_id, "DEPROVISIONED", creds, max_wait=60, interval=5)
    return {"action": "deactivate_user", "user_id": user_id, "status": "DEPROVISIONED", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use reactivate_user to roll back"}
```

- [ ] **Step 4: Update reactivate_user.py**

```python
import asyncio
from datetime import datetime, timezone


async def _wait_for_status(user_id: str, target_status: str, creds: dict, max_wait: int = 60, interval: int = 5) -> None:
    import httpx
    from ._client import okta_headers, okta_base
    deadline = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < deadline:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{okta_base(creds)}/users/{user_id}", headers=okta_headers(creds))
            if resp.status_code == 200 and resp.json().get("status") == target_status:
                return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Okta user {user_id} did not reach status '{target_status}' within {max_wait}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "reactivate_user", "user_id": user_id, "status": "ACTIVE", "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/reactivate", headers=okta_headers(creds))
        resp.raise_for_status()
    await _wait_for_status(user_id, "ACTIVE", creds, max_wait=60, interval=5)
    return {"action": "reactivate_user", "user_id": user_id, "status": "ACTIVE", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use deactivate_user to roll back"}
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/okta/suspend_user.py \
        backend/app/connectors/executors/okta/unsuspend_user.py \
        backend/app/connectors/executors/okta/deactivate_user.py \
        backend/app/connectors/executors/okta/reactivate_user.py
git commit -m "feat: add Okta lifecycle waiters — block until SUSPENDED/ACTIVE/DEPROVISIONED confirmed"
```

---

## Task 6: Waiter Hardening — Active Directory Account Actions

Add LDAP re-verification after `disable_account` and `enable_account` to confirm the change took effect.

**Files:**
- Modify: `backend/app/connectors/executors/active_directory/disable_account.py`
- Modify: `backend/app/connectors/executors/active_directory/enable_account.py`

- [ ] **Step 1: Update disable_account.py**

```python
import asyncio
from datetime import datetime, timezone


async def _verify_disabled(username: str, user_dn: str, creds: dict, retries: int = 3, delay: float = 2.0) -> bool:
    """Re-query LDAP to confirm userAccountControl has the disabled bit (0x2) set."""
    from ._client import get_connection
    from ldap3 import SUBTREE
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _check():
        conn = get_connection(creds)
        conn.search(base_dn, f"(distinguishedName={user_dn})", SUBTREE, attributes=["userAccountControl"])
        entries = conn.entries
        conn.unbind()
        if not entries:
            return False
        uac = int(entries[0].userAccountControl.value)
        return bool(uac & 0x2)

    for _ in range(retries):
        confirmed = await asyncio.get_event_loop().run_in_executor(None, _check)
        if confirmed:
            return True
        await asyncio.sleep(delay)
    return False


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import MODIFY_REPLACE
    username = parameters.get("username")
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    user_dn = parameters.get("user_dn") or f"CN={username},{base_dn}"

    def _sync():
        conn = get_connection(creds)
        conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        conn.unbind()
        return conn.result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    confirmed = await _verify_disabled(username, user_dn, creds)
    return {
        "action": "disable_account",
        "username": username,
        "user_dn": user_dn,
        "disabled": confirmed,
        "ldap_result": str(result),
        "disabled_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "disable_account",
            "username": parameters.get("username"),
            "disabled": True,
            "disabled_at": datetime.now(timezone.utc).isoformat(),
        }
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "enable_account", "username": parameters.get("username")}
```

- [ ] **Step 2: Read enable_account.py to check its structure**

Run: `docker compose exec backend cat /app/app/connectors/executors/active_directory/enable_account.py`

Then apply the same verification pattern — after the LDAP modify (sets `userAccountControl` to `512`), call `_verify_enabled` that checks `uac & 0x2 == 0`.

Write `enable_account.py`:

```python
import asyncio
from datetime import datetime, timezone


async def _verify_enabled(username: str, user_dn: str, creds: dict, retries: int = 3, delay: float = 2.0) -> bool:
    from ._client import get_connection
    from ldap3 import SUBTREE
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _check():
        conn = get_connection(creds)
        conn.search(base_dn, f"(distinguishedName={user_dn})", SUBTREE, attributes=["userAccountControl"])
        entries = conn.entries
        conn.unbind()
        if not entries:
            return False
        uac = int(entries[0].userAccountControl.value)
        return not bool(uac & 0x2)

    for _ in range(retries):
        confirmed = await asyncio.get_event_loop().run_in_executor(None, _check)
        if confirmed:
            return True
        await asyncio.sleep(delay)
    return False


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import MODIFY_REPLACE
    username = parameters.get("username")
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    user_dn = parameters.get("user_dn") or f"CN={username},{base_dn}"

    def _sync():
        conn = get_connection(creds)
        conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [512])]})
        conn.unbind()
        return conn.result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    confirmed = await _verify_enabled(username, user_dn, creds)
    return {
        "action": "enable_account",
        "username": username,
        "user_dn": user_dn,
        "enabled": confirmed,
        "ldap_result": str(result),
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "enable_account",
            "username": parameters.get("username"),
            "enabled": True,
            "enabled_at": datetime.now(timezone.utc).isoformat(),
        }
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "disable_account", "username": parameters.get("username")}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/active_directory/disable_account.py \
        backend/app/connectors/executors/active_directory/enable_account.py
git commit -m "feat: add LDAP re-verification waiters to AD disable_account and enable_account"
```

---

## Task 7: Waiter Hardening — CrowdStrike Isolation

Poll the CrowdStrike Hosts API after containment to confirm `network_containment_status` is `contained` before returning.

**Files:**
- Modify: `backend/app/connectors/executors/crowdstrike/isolate_host.py`
- Modify: `backend/app/connectors/executors/crowdstrike/restore_host.py`

- [ ] **Step 1: Update isolate_host.py**

```python
import asyncio
import random
import string
from datetime import datetime, timezone


def _mock_response(asset_ids):
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {
        "action": "isolate_host",
        "isolation_id": isolation_id,
        "assets": asset_ids,
        "device_ids": asset_ids,
        "isolated": True,
        "containment_status": "contained",
        "isolated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _wait_for_containment(device_ids: list, target_status: str, creds: dict, max_wait: int = 120, interval: int = 10) -> bool:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait

    def _check():
        resp = falcon.get_device_details(ids=device_ids)
        resources = (resp or {}).get("body", {}).get("resources", [])
        return all(r.get("network_containment_status") == target_status for r in resources)

    while loop.time() < deadline:
        confirmed = await loop.run_in_executor(None, _check)
        if confirmed:
            return True
        await asyncio.sleep(interval)
    return False


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    device_ids = parameters.get("device_ids", asset_ids)

    await loop.run_in_executor(
        None,
        lambda: falcon.perform_action(action_name="contain", body={"ids": device_ids}),
    )
    confirmed = await _wait_for_containment(device_ids, "contained", creds)
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {
        "action": "isolate_host",
        "isolation_id": isolation_id,
        "assets": asset_ids,
        "device_ids": device_ids,
        "isolated": confirmed,
        "containment_status": "contained" if confirmed else "pending",
        "isolated_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_host", "isolation_id": execution_result.get("isolation_id")}
```

- [ ] **Step 2: Read restore_host.py**

Run: `docker compose exec backend cat /app/app/connectors/executors/crowdstrike/restore_host.py`

Apply the same `_wait_for_containment` pattern with `target_status="normal"`.

Write `restore_host.py`:

```python
import asyncio
import random
import string
from datetime import datetime, timezone


async def _wait_for_containment(device_ids: list, target_status: str, creds: dict, max_wait: int = 120, interval: int = 10) -> bool:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait

    def _check():
        resp = falcon.get_device_details(ids=device_ids)
        resources = (resp or {}).get("body", {}).get("resources", [])
        return all(r.get("network_containment_status") == target_status for r in resources)

    while loop.time() < deadline:
        confirmed = await loop.run_in_executor(None, _check)
        if confirmed:
            return True
        await asyncio.sleep(interval)
    return False


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_ids = parameters.get("device_ids", asset_ids)
    if not creds:
        return {
            "action": "restore_host",
            "device_ids": device_ids,
            "restored": True,
            "containment_status": "normal",
            "restored_at": datetime.now(timezone.utc).isoformat(),
        }
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: falcon.perform_action(action_name="lift_containment", body={"ids": device_ids}),
    )
    confirmed = await _wait_for_containment(device_ids, "normal", creds)
    return {
        "action": "restore_host",
        "device_ids": device_ids,
        "restored": confirmed,
        "containment_status": "normal" if confirmed else "pending",
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_host has no further rollback"}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/crowdstrike/isolate_host.py \
        backend/app/connectors/executors/crowdstrike/restore_host.py
git commit -m "feat: add CrowdStrike containment status waiters to isolate_host and restore_host"
```

---

## Task 8: Waiter Hardening — Cloudflare DNS Propagation

Replace the stub `wait_dns_propagation` with real DNS resolver polling, and add post-write verification to `update_dns_record`.

**Files:**
- Modify: `backend/app/connectors/executors/cloudflare/wait_dns_propagation.py`
- Modify: `backend/app/connectors/executors/cloudflare/update_dns_record.py`

- [ ] **Step 1: Replace wait_dns_propagation.py with real polling**

```python
import asyncio
import socket
from datetime import datetime, timezone


_CHECK_RESOLVERS = ["1.1.1.1", "8.8.8.8"]  # Cloudflare + Google


async def _dns_query(record_name: str, expected_value: str, resolver_ip: str) -> bool:
    """Return True if the record resolves to expected_value via the given resolver."""
    loop = asyncio.get_event_loop()

    def _lookup():
        import dns.resolver
        r = dns.resolver.Resolver()
        r.nameservers = [resolver_ip]
        try:
            answers = r.resolve(record_name, "A")
            return any(str(rdata) == expected_value for rdata in answers)
        except Exception:
            return False

    try:
        return await loop.run_in_executor(None, _lookup)
    except Exception:
        return False


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    ttl = int(parameters.get("ttl", 60))
    record_name = parameters.get("record_name", "")
    new_value = parameters.get("new_value", "")
    creds = getattr(connector, "credentials", {})

    # Without real creds or record info, return immediately (mock/test mode)
    if not creds or not record_name or not new_value:
        return {
            "action": "wait_dns_propagation",
            "ttl_seconds": ttl,
            "propagated": True,
            "mock": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    # Poll both resolvers; succeed when both confirm propagation
    max_wait = min(ttl, 300)
    interval = 10
    deadline = asyncio.get_event_loop().time() + max_wait

    while asyncio.get_event_loop().time() < deadline:
        results = await asyncio.gather(*[
            _dns_query(record_name, new_value, resolver)
            for resolver in _CHECK_RESOLVERS
        ])
        if all(results):
            return {
                "action": "wait_dns_propagation",
                "ttl_seconds": ttl,
                "propagated": True,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        await asyncio.sleep(interval)

    # Timeout — log but don't fail the workflow (propagation can be slow)
    return {
        "action": "wait_dns_propagation",
        "ttl_seconds": ttl,
        "propagated": False,
        "note": f"Propagation not confirmed within {max_wait}s — may still be in progress",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "propagation wait has no rollback"}
```

Note: This requires `dnspython` to be installed. Add to `backend/requirements.txt`:

```
dnspython>=2.4.0
```

- [ ] **Step 2: Check requirements.txt and add dnspython if missing**

```bash
docker compose exec backend pip show dnspython
```

If not installed: add `dnspython>=2.4.0` to `backend/requirements.txt` and rebuild:

```bash
docker compose build backend
docker compose up backend -d
```

- [ ] **Step 3: Add post-write record verification to update_dns_record.py**

After the PUT/POST call in `_real_execute`, verify the record was created via a GET:

In `update_dns_record.py`, replace `_real_execute` with:

```python
async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import cf_get, cf_put, cf_post
    zone_id = creds.get("zone_id")
    record_name = parameters.get("record_name")
    record_type = parameters.get("record_type", "A")
    new_value = parameters.get("new_value")
    ttl = parameters.get("ttl", 300)
    proxied = parameters.get("proxied", False)

    # Find existing record
    params = f"?name={record_name}&type={record_type}" if record_name else ""
    data = await cf_get(f"/zones/{zone_id}/dns_records{params}", creds)
    records = data.get("result", [])
    previous_value = records[0].get("content") if records else None
    record_id = records[0].get("id") if records else None

    body = {"type": record_type, "name": record_name, "content": new_value, "ttl": ttl, "proxied": proxied}
    if record_id:
        resp = await cf_put(f"/zones/{zone_id}/dns_records/{record_id}", body, creds)
    else:
        resp = await cf_post(f"/zones/{zone_id}/dns_records", body, creds)

    result_rec = resp.get("result", {})
    final_record_id = result_rec.get("id", record_id)

    # Verify the record now returns the expected value
    verify = await cf_get(f"/zones/{zone_id}/dns_records/{final_record_id}", creds)
    verified_value = verify.get("result", {}).get("content")
    if verified_value != new_value:
        raise RuntimeError(f"DNS record write unconfirmed: expected {new_value!r}, got {verified_value!r}")

    return {
        "action": "update_dns_record",
        "record_name": record_name,
        "record_type": record_type,
        "record_id": final_record_id,
        "previous_value": previous_value,
        "new_value": new_value,
        "ttl": ttl,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/cloudflare/wait_dns_propagation.py \
        backend/app/connectors/executors/cloudflare/update_dns_record.py \
        backend/requirements.txt
git commit -m "feat: replace Cloudflare DNS propagation stub with real resolver polling; add post-write verification"
```

---

## Task 9: Add Okta Discovery to Ingest for Identity Assets

Okta's `discover_users` executor exists but may return identity assets without `okta_user_id` in metadata, breaking the dedup. Verify and fix.

**Files:**
- Modify: `backend/app/connectors/executors/okta/discover_users.py`

- [ ] **Step 1: Read discover_users.py**

```bash
docker compose exec backend cat /app/app/connectors/executors/okta/discover_users.py
```

- [ ] **Step 2: Ensure each user asset includes `okta_user_id` in asset_metadata**

The executor should return payloads in this shape:

```python
{
    "id": user["id"],                 # top-level id used for dedup
    "name": user.get("profile", {}).get("displayName") or user.get("profile", {}).get("login"),
    "asset_type": "identity",
    "asset_metadata": {
        "okta_user_id": user["id"],
        "login": user.get("profile", {}).get("login"),
        "email": user.get("profile", {}).get("email"),
        "first_name": user.get("profile", {}).get("firstName"),
        "last_name": user.get("profile", {}).get("lastName"),
        "status": user.get("status"),
        "provider": "okta",
    },
}
```

Update `discover_users.py` mock and real paths to match this shape exactly.

- [ ] **Step 3: Ensure discover_identities (AD) returns object_guid**

```bash
docker compose exec backend cat /app/app/connectors/executors/active_directory/discover_identities.py
```

Each payload should include:

```python
{
    "id": str(entry.get("objectGUID")),
    "name": str(entry.get("cn")),
    "asset_type": "identity",
    "asset_metadata": {
        "object_guid": str(entry.get("objectGUID")),
        "sam_account_name": str(entry.get("sAMAccountName")),
        "email": str(entry.get("mail", "")),
        "dn": str(entry.entry_dn),
        "enabled": not bool(int(entry.get("userAccountControl", 512)) & 0x2),
        "provider": "active_directory",
    },
}
```

- [ ] **Step 4: Ensure CrowdStrike discover_endpoints returns device_id**

```bash
docker compose exec backend cat /app/app/connectors/executors/crowdstrike/discover_endpoints.py
```

Each payload should include:

```python
{
    "id": device["device_id"],
    "name": device.get("hostname"),
    "asset_type": "endpoint",
    "asset_metadata": {
        "device_id": device["device_id"],
        "hostname": device.get("hostname"),
        "platform_name": device.get("platform_name"),
        "os_version": device.get("os_version"),
        "containment_status": device.get("network_containment_status", "normal"),
        "agent_version": device.get("agent_version"),
        "provider": "crowdstrike",
    },
}
```

- [ ] **Step 5: Commit all discovery executor fixes**

```bash
git add backend/app/connectors/executors/okta/discover_users.py \
        backend/app/connectors/executors/active_directory/discover_identities.py \
        backend/app/connectors/executors/crowdstrike/discover_endpoints.py
git commit -m "fix: ensure Okta/AD/CrowdStrike discovery payloads include external IDs in asset_metadata"
```

---

## Task 10: Final Integration — Restart and Smoke Test

- [ ] **Step 1: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade ... -> 017, add new asset types`

- [ ] **Step 2: Restart all services**

```bash
docker compose stop frontend && docker compose up frontend -d
docker compose restart backend
```

- [ ] **Step 3: Smoke test asset type filter**

Open `http://localhost:3000/assets` — the "All Types" dropdown should include `database`, `storage bucket`, `load balancer`, `endpoint`, `container cluster`.

- [ ] **Step 4: Smoke test quick actions on server asset**

Click any server asset → "Quick Actions" panel shows 10 actions. Click "Stop Instance" → redirected to Create CR with change type `ec2_stop`, asset pre-selected, `instance_id` pre-filled in outcome JSON.

- [ ] **Step 5: Smoke test quick actions on database asset (if any exist)**

If no database assets exist, run discovery manually on the AWS connector — RDS instances should now appear as `database` type assets with `db_identifier`, `endpoint`, `port` in metadata.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: connector robustness pass complete — new asset types, waiters, dedup, discovery"
```
