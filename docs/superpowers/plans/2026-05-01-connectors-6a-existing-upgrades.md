# Connectors 6a: Existing Connector Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename all `_mock` connector type enum values to production names, replace Okta actions with proper ones, and fill critical action gaps in all 10 existing connectors.

**Architecture:** Single Alembic migration renames the PostgreSQL enum values. Catalog JSON files are renamed and updated. Executor directories are renamed and new executor `.py` files are added. Frontend ConnectorType type and labels/icons are updated.

**Tech Stack:** Python/FastAPI, SQLAlchemy, Alembic (PostgreSQL enum rename), boto3, pan-os-python, ldap3, falconpy, httpx, paramiko, React/TypeScript

---

## File Map

**Backend:**
- Create: `backend/alembic/versions/009_rename_connector_types.py`
- Modify: `backend/app/models/connector.py` — update ConnectorType enum
- Rename+modify: `backend/app/connectors/catalog/aws_mock.json` → `aws.json`
- Rename+modify: `backend/app/connectors/catalog/azure_mock.json` → `azure.json`
- Rename+modify: `backend/app/connectors/catalog/okta_mock.json` → `okta.json`
- Rename+modify: `backend/app/connectors/catalog/paloalto_mock.json` → `paloalto.json`
- Rename+modify: `backend/app/connectors/catalog/active_directory_mock.json` → `active_directory.json`
- Rename+modify: `backend/app/connectors/catalog/crowdstrike_mock.json` → `crowdstrike.json`
- Rename+modify: `backend/app/connectors/catalog/cloudflare_mock.json` → `cloudflare.json`
- Rename+modify: `backend/app/connectors/catalog/tenable_mock.json` → `tenable.json`
- Rename+modify: `backend/app/connectors/catalog/ssh_mock.json` → `ssh.json`
- Modify: `backend/app/connectors/catalog/nexplane_agent_mock.json` — update connector_type field only
- Rename: `backend/app/connectors/executors/aws_mock/` → `aws/` (add new executor files)
- Rename: `backend/app/connectors/executors/azure_mock/` → `azure/`
- Rename: `backend/app/connectors/executors/okta_mock/` → `okta/` (replace all executor files)
- Rename: `backend/app/connectors/executors/paloalto_mock/` → `paloalto/`
- Rename: `backend/app/connectors/executors/active_directory_mock/` → `active_directory/`
- Rename: `backend/app/connectors/executors/crowdstrike_mock/` → `crowdstrike/`
- Rename: `backend/app/connectors/executors/cloudflare_mock/` → `cloudflare/`
- Rename: `backend/app/connectors/executors/tenable_mock/` → `tenable/`
- Rename: `backend/app/connectors/executors/ssh_mock/` → `ssh/`
- Rename: `backend/app/connectors/executors/nexplane_agent_mock/` → `nexplane_agent/`
- Modify: `backend/app/connectors/dispatcher.py` — update executor path resolution

**Frontend:**
- Modify: `frontend/src/types/api.ts` — update ConnectorType
- Modify: `frontend/src/pages/Connectors.tsx` — update CONNECTOR_LABELS, CONNECTOR_ICONS
- Modify: `frontend/src/components/AddConnectorModal.tsx` — update labels

**Tests:**
- Create: `backend/app/tests/test_connector_rename_migration.py`

---

### Task 1: Database Migration — Rename Enum Values

**Files:**
- Create: `backend/alembic/versions/009_rename_connector_types.py`

- [ ] **Step 1: Write the migration**

```python
# backend/alembic/versions/009_rename_connector_types.py
"""rename connector type enum values from mock to production

Revision ID: 009
Revises: 008
Create Date: 2026-05-01
"""
from alembic import op

revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type RENAME VALUE 'aws_mock' TO 'aws'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'azure_mock' TO 'azure'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'cloudflare_mock' TO 'cloudflare'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'okta_mock' TO 'okta'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'paloalto_mock' TO 'paloalto'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'ssh_runner_mock' TO 'ssh'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'active_directory_mock' TO 'active_directory'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'crowdstrike_mock' TO 'crowdstrike'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'tenable_mock' TO 'tenable'")


def downgrade():
    op.execute("ALTER TYPE connector_type RENAME VALUE 'aws' TO 'aws_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'azure' TO 'azure_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'cloudflare' TO 'cloudflare_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'okta' TO 'okta_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'paloalto' TO 'paloalto_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'ssh' TO 'ssh_runner_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'active_directory' TO 'active_directory_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'crowdstrike' TO 'crowdstrike_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'tenable' TO 'tenable_mock'")
```

- [ ] **Step 2: Run migration in Docker**

```bash
docker compose exec backend alembic upgrade 009
```
Expected: `Running upgrade 008 -> 009`

- [ ] **Step 3: Update ConnectorType enum in models**

Edit `backend/app/models/connector.py` — replace ConnectorType class:

```python
class ConnectorType(str, enum.Enum):
    aws = "aws"
    azure = "azure"
    cloudflare = "cloudflare"
    okta = "okta"
    paloalto = "paloalto"
    ssh = "ssh"
    active_directory = "active_directory"
    crowdstrike = "crowdstrike"
    tenable = "tenable"
    nexplane_agent = "nexplane_agent"
```

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/009_rename_connector_types.py backend/app/models/connector.py
git commit -m "feat: migrate connector_type enum values from _mock to production names"
```

---

### Task 2: Rename Catalog Files and Executor Directories

**Files:** All 10 catalog JSONs, all 10 executor dirs

- [ ] **Step 1: Rename catalog files**

```bash
cd backend/app/connectors/catalog
cp aws_mock.json aws.json && rm aws_mock.json
cp azure_mock.json azure.json && rm azure_mock.json
cp cloudflare_mock.json cloudflare.json && rm cloudflare_mock.json
cp okta_mock.json okta.json && rm okta_mock.json
cp paloalto_mock.json paloalto.json && rm paloalto_mock.json
cp ssh_mock.json ssh.json && rm ssh_mock.json
cp active_directory_mock.json active_directory.json && rm active_directory_mock.json
cp crowdstrike_mock.json crowdstrike.json && rm crowdstrike_mock.json
cp tenable_mock.json tenable.json && rm tenable_mock.json
```

- [ ] **Step 2: Update connector_type field in each catalog file**

In each renamed JSON, change `"connector_type": "aws_mock"` → `"connector_type": "aws"` (and similarly for each connector). Also update `"executor": "aws_mock.health_check"` → `"executor": "aws.health_check"` in all action entries. Also update `display_name` to drop "(Mock)".

For `nexplane_agent_mock.json`, only update the `connector_type` field (no rename needed, file stays as `nexplane_agent_mock.json` but type becomes `nexplane_agent` — already correct).

- [ ] **Step 3: Rename executor directories**

```bash
cd backend/app/connectors/executors
cp -r aws_mock aws && rm -rf aws_mock
cp -r azure_mock azure && rm -rf azure_mock
cp -r cloudflare_mock cloudflare && rm -rf cloudflare_mock
cp -r okta_mock okta && rm -rf okta_mock
cp -r paloalto_mock paloalto && rm -rf paloalto_mock
cp -r ssh_mock ssh && rm -rf ssh_mock
cp -r active_directory_mock active_directory && rm -rf active_directory_mock
cp -r crowdstrike_mock crowdstrike && rm -rf crowdstrike_mock
cp -r tenable_mock tenable && rm -rf tenable_mock
cp -r nexplane_agent_mock nexplane_agent && rm -rf nexplane_agent_mock
```

- [ ] **Step 4: Update executor module references inside files**

In each executor directory, all files that import from `._client` stay as-is. But the catalog's `executor` field now points to `aws.health_check` etc — check `dispatcher.py` resolves these. The dispatcher should split on `.` and import from `app.connectors.executors.{connector_type}.{action_id}`.

- [ ] **Step 5: Verify dispatcher still works**

```bash
docker compose exec backend python -c "
from app.connectors.catalog_loader import load_catalog
cat = load_catalog('aws')
print('AWS catalog loaded:', len(cat['actions']), 'actions')
"
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/
git commit -m "feat: rename executor directories and catalog files from _mock to production names"
```

---

### Task 3: AWS — Add 15 Missing Actions

**Files:**
- Modify: `backend/app/connectors/catalog/aws.json`
- Create: `backend/app/connectors/executors/aws/discover_instances.py`
- Create: `backend/app/connectors/executors/aws/discover_iam_users.py`
- Create: `backend/app/connectors/executors/aws/discover_s3_buckets.py`
- Create: `backend/app/connectors/executors/aws/discover_security_groups.py`
- Create: `backend/app/connectors/executors/aws/ingest_guardduty_findings.py`
- Create: `backend/app/connectors/executors/aws/ingest_security_hub_findings.py`
- Create: `backend/app/connectors/executors/aws/launch_instance.py`
- Create: `backend/app/connectors/executors/aws/stop_instance.py`
- Create: `backend/app/connectors/executors/aws/start_instance.py`
- Create: `backend/app/connectors/executors/aws/terminate_instance.py`
- Create: `backend/app/connectors/executors/aws/block_s3_public_access.py`
- Create: `backend/app/connectors/executors/aws/enable_cloudtrail.py`
- Create: `backend/app/connectors/executors/aws/enable_guardduty.py`
- Create: `backend/app/connectors/executors/aws/enforce_imdsv2.py`
- Create: `backend/app/connectors/executors/aws/rotate_iam_access_key.py`

- [ ] **Step 1: Add actions to aws.json catalog**

Append to the `actions` array in `backend/app/connectors/catalog/aws.json`:

```json
{
  "action_id": "discover_instances",
  "generic_action": "discover",
  "action_type": "ingest",
  "execution_tier": 1,
  "display_name": "Discover EC2 Instances",
  "description": "EC2 instances: ID, type, state, AZ, VPC, subnet, public IP, IAM role, tags",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [],
  "executor": "aws.discover_instances",
  "estimated_duration_seconds": 30
},
{
  "action_id": "discover_iam_users",
  "generic_action": "discover",
  "action_type": "ingest",
  "execution_tier": 1,
  "display_name": "Discover IAM Users",
  "description": "IAM users: MFA enabled, access key age, last login, attached policies",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.discover_iam_users",
  "estimated_duration_seconds": 30
},
{
  "action_id": "discover_s3_buckets",
  "generic_action": "discover",
  "action_type": "ingest",
  "execution_tier": 1,
  "display_name": "Discover S3 Buckets",
  "description": "S3 buckets: public access block settings, versioning, encryption, ACL",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.discover_s3_buckets",
  "estimated_duration_seconds": 30
},
{
  "action_id": "discover_security_groups",
  "generic_action": "discover",
  "action_type": "ingest",
  "execution_tier": 1,
  "display_name": "Discover Security Groups",
  "description": "All security groups: rules, attached resources",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.discover_security_groups",
  "estimated_duration_seconds": 30
},
{
  "action_id": "ingest_guardduty_findings",
  "generic_action": "ingest_findings",
  "action_type": "ingest",
  "execution_tier": 1,
  "display_name": "Ingest GuardDuty Findings",
  "description": "Import active GuardDuty findings as asset tags and security signals",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.ingest_guardduty_findings",
  "estimated_duration_seconds": 30
},
{
  "action_id": "ingest_security_hub_findings",
  "generic_action": "ingest_findings",
  "action_type": "ingest",
  "execution_tier": 1,
  "display_name": "Ingest Security Hub Findings",
  "description": "Import Security Hub findings (cross-service aggregation)",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.ingest_security_hub_findings",
  "estimated_duration_seconds": 30
},
{
  "action_id": "launch_instance",
  "generic_action": "launch_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Launch EC2 Instance",
  "description": "Launch EC2 from specified AMI with instance type, subnet, SG, IAM role. Returns instance ID. Rollback: terminate instance.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "ami_id", "type": "string", "required": true, "description": "AMI ID to launch"},
    {"name": "instance_type", "type": "string", "required": true, "description": "e.g. t3.micro"},
    {"name": "subnet_id", "type": "string", "required": true, "description": "Subnet ID"},
    {"name": "security_group_ids", "type": "array", "required": true, "description": "List of SG IDs"},
    {"name": "iam_instance_profile", "type": "string", "required": false, "description": "IAM instance profile name"}
  ],
  "executor": "aws.launch_instance",
  "estimated_duration_seconds": 60,
  "blast_radius_hint": "new_resource"
},
{
  "action_id": "stop_instance",
  "generic_action": "stop_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Stop EC2 Instance",
  "description": "Stop a running EC2 instance. Rollback: start_instance.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.stop_instance",
  "estimated_duration_seconds": 30
},
{
  "action_id": "start_instance",
  "generic_action": "start_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Start EC2 Instance",
  "description": "Start a stopped EC2 instance.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.start_instance",
  "estimated_duration_seconds": 30
},
{
  "action_id": "terminate_instance",
  "generic_action": "terminate_instance",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Terminate EC2 Instance",
  "description": "Terminate an EC2 instance.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.terminate_instance",
  "estimated_duration_seconds": 30,
  "blast_radius_hint": "destructive"
},
{
  "action_id": "block_s3_public_access",
  "generic_action": "block_public_access",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Block S3 Public Access",
  "description": "Enable Block Public Access on a bucket. Rollback restores previous setting.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true, "description": "S3 bucket name"}
  ],
  "executor": "aws.block_s3_public_access",
  "estimated_duration_seconds": 10
},
{
  "action_id": "enable_cloudtrail",
  "generic_action": "enable_logging",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Enable CloudTrail",
  "description": "Enable CloudTrail logging for a region. Rollback: disable trail.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "trail_name", "type": "string", "required": true, "description": "Trail name"},
    {"name": "s3_bucket_name", "type": "string", "required": true, "description": "S3 bucket for logs"}
  ],
  "executor": "aws.enable_cloudtrail",
  "estimated_duration_seconds": 15
},
{
  "action_id": "enable_guardduty",
  "generic_action": "enable_service",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Enable GuardDuty",
  "description": "Enable GuardDuty detector in current region.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.enable_guardduty",
  "estimated_duration_seconds": 15
},
{
  "action_id": "enforce_imdsv2",
  "generic_action": "harden_config",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Enforce IMDSv2",
  "description": "Set EC2 instance metadata service to require v2 tokens. Rollback restores optional.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.enforce_imdsv2",
  "estimated_duration_seconds": 10
},
{
  "action_id": "rotate_iam_access_key",
  "generic_action": "rotate_credentials",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Rotate IAM Access Key",
  "description": "Create new access key, return in result, delete old key.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "username", "type": "string", "required": true, "description": "IAM username"},
    {"name": "old_access_key_id", "type": "string", "required": true, "description": "Access key to delete"}
  ],
  "executor": "aws.rotate_iam_access_key",
  "estimated_duration_seconds": 15,
  "blast_radius_hint": "credential_change"
}
```

- [ ] **Step 2: Create discover_instances.py**

```python
# backend/app/connectors/executors/aws/discover_instances.py
import asyncio


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: ec2.describe_instances())
    instances = []
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            instances.append({
                "instance_id": inst.get("InstanceId"),
                "instance_type": inst.get("InstanceType"),
                "state": inst.get("State", {}).get("Name"),
                "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
                "vpc_id": inst.get("VpcId"),
                "subnet_id": inst.get("SubnetId"),
                "public_ip": inst.get("PublicIpAddress"),
                "iam_role": inst.get("IamInstanceProfile", {}).get("Arn"),
                "tags": {t["Key"]: t["Value"] for t in inst.get("Tags", [])},
            })
    return {"action": "discover_instances", "instances": instances, "count": len(instances)}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_instances", "instances": [
            {"instance_id": "i-mock001", "instance_type": "t3.micro", "state": "running",
             "availability_zone": "us-east-1a", "vpc_id": "vpc-mock", "subnet_id": "subnet-mock",
             "public_ip": "1.2.3.4", "iam_role": None, "tags": {"Name": "mock-server"}}
        ], "count": 1}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 3: Create discover_iam_users.py**

```python
# backend/app/connectors/executors/aws/discover_iam_users.py
import asyncio
from datetime import datetime, timezone


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_boto3_client
    iam = get_boto3_client(creds, "iam")
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: iam.list_users())
    users = []
    for u in response.get("Users", []):
        users.append({
            "username": u.get("UserName"),
            "arn": u.get("Arn"),
            "created": u.get("CreateDate", "").isoformat() if hasattr(u.get("CreateDate", ""), "isoformat") else str(u.get("CreateDate", "")),
            "password_last_used": u.get("PasswordLastUsed", "").isoformat() if hasattr(u.get("PasswordLastUsed", ""), "isoformat") else None,
        })
    return {"action": "discover_iam_users", "users": users, "count": len(users)}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_iam_users", "users": [
            {"username": "mock-admin", "arn": "arn:aws:iam::123456789:user/mock-admin",
             "created": "2024-01-01T00:00:00", "password_last_used": "2025-01-01T00:00:00"}
        ], "count": 1}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 4: Create discover_s3_buckets.py**

```python
# backend/app/connectors/executors/aws/discover_s3_buckets.py
import asyncio


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, "s3")
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: s3.list_buckets())
    buckets = []
    for b in response.get("Buckets", []):
        name = b["Name"]
        try:
            pab = await loop.run_in_executor(None, lambda: s3.get_public_access_block(Bucket=name))
            public_access_blocked = pab.get("PublicAccessBlockConfiguration", {}).get("BlockPublicAcls", False)
        except Exception:
            public_access_blocked = None
        buckets.append({"name": name, "created": str(b.get("CreationDate", "")), "public_access_blocked": public_access_blocked})
    return {"action": "discover_s3_buckets", "buckets": buckets, "count": len(buckets)}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_s3_buckets", "buckets": [
            {"name": "mock-bucket", "created": "2024-01-01", "public_access_blocked": True}
        ], "count": 1}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 5: Create discover_security_groups.py**

```python
# backend/app/connectors/executors/aws/discover_security_groups.py
import asyncio


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: ec2.describe_security_groups())
    sgs = [{"group_id": sg["GroupId"], "group_name": sg["GroupName"], "vpc_id": sg.get("VpcId"),
             "ingress_rules": len(sg.get("IpPermissions", [])), "egress_rules": len(sg.get("IpPermissionsEgress", []))}
            for sg in response.get("SecurityGroups", [])]
    return {"action": "discover_security_groups", "security_groups": sgs, "count": len(sgs)}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_security_groups", "security_groups": [
            {"group_id": "sg-mock001", "group_name": "mock-sg", "vpc_id": "vpc-mock", "ingress_rules": 2, "egress_rules": 1}
        ], "count": 1}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 6: Create ingest_guardduty_findings.py**

```python
# backend/app/connectors/executors/aws/ingest_guardduty_findings.py
import asyncio


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_boto3_client
    gd = get_boto3_client(creds, "guardduty")
    loop = asyncio.get_event_loop()
    detectors = await loop.run_in_executor(None, lambda: gd.list_detectors())
    detector_ids = detectors.get("DetectorIds", [])
    findings = []
    for detector_id in detector_ids:
        finding_ids_resp = await loop.run_in_executor(None, lambda: gd.list_findings(DetectorId=detector_id, FindingCriteria={"Criterion": {"severity": {"Gte": 4}}}))
        if finding_ids_resp.get("FindingIds"):
            details = await loop.run_in_executor(None, lambda: gd.get_findings(DetectorId=detector_id, FindingIds=finding_ids_resp["FindingIds"][:50]))
            for f in details.get("Findings", []):
                findings.append({"id": f.get("Id"), "type": f.get("Type"), "severity": f.get("Severity"), "region": f.get("Region"), "title": f.get("Title")})
    return {"action": "ingest_guardduty_findings", "findings": findings, "count": len(findings)}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_guardduty_findings", "findings": [], "count": 0}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
```

- [ ] **Step 7: Create ingest_security_hub_findings.py**

```python
# backend/app/connectors/executors/aws/ingest_security_hub_findings.py
import asyncio


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_boto3_client
    sh = get_boto3_client(creds, "securityhub")
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: sh.get_findings(Filters={"RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}]}, MaxResults=100))
    findings = [{"id": f.get("Id"), "title": f.get("Title"), "severity": f.get("Severity", {}).get("Label"), "product": f.get("ProductArn")}
                for f in response.get("Findings", [])]
    return {"action": "ingest_security_hub_findings", "findings": findings, "count": len(findings)}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_security_hub_findings", "findings": [], "count": 0}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
```

- [ ] **Step 8: Create stop_instance.py, start_instance.py, terminate_instance.py**

```python
# backend/app/connectors/executors/aws/stop_instance.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[parameters["instance_id"]]))
    return {"action": "stop_instance", "instance_id": parameters["instance_id"], "status": "stopping"}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "stop_instance", "instance_id": parameters.get("instance_id", "i-mock"), "status": "stopping"}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.start_instance import execute as start
    return await start(parameters, [], connector)
```

```python
# backend/app/connectors/executors/aws/start_instance.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[parameters["instance_id"]]))
    return {"action": "start_instance", "instance_id": parameters["instance_id"], "status": "starting"}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "start_instance", "instance_id": parameters.get("instance_id", "i-mock"), "status": "starting"}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "start_instance rollback would stop instance — use stop_instance action explicitly"}
```

```python
# backend/app/connectors/executors/aws/terminate_instance.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.terminate_instances(InstanceIds=[parameters["instance_id"]]))
    return {"action": "terminate_instance", "instance_id": parameters["instance_id"], "status": "shutting-down"}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "terminate_instance", "instance_id": parameters.get("instance_id", "i-mock"), "status": "shutting-down"}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terminate is irreversible"}
```

- [ ] **Step 9: Create launch_instance.py**

```python
# backend/app/connectors/executors/aws/launch_instance.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    kwargs = {
        "ImageId": parameters["ami_id"],
        "InstanceType": parameters["instance_type"],
        "SubnetId": parameters["subnet_id"],
        "SecurityGroupIds": parameters["security_group_ids"],
        "MinCount": 1,
        "MaxCount": 1,
    }
    if parameters.get("iam_instance_profile"):
        kwargs["IamInstanceProfile"] = {"Name": parameters["iam_instance_profile"]}
    response = await loop.run_in_executor(None, lambda: ec2.run_instances(**kwargs))
    instance_id = response["Instances"][0]["InstanceId"]
    return {"action": "launch_instance", "instance_id": instance_id, "status": "pending"}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "launch_instance", "instance_id": "i-mock-new", "status": "pending"}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_id = execution_result.get("instance_id")
    if instance_id:
        from app.connectors.executors.aws.terminate_instance import execute as terminate
        return await terminate({"instance_id": instance_id}, [], connector)
    return {"rolled_back": False, "reason": "no instance_id in result"}
```

- [ ] **Step 10: Create block_s3_public_access.py**

```python
# backend/app/connectors/executors/aws/block_s3_public_access.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, "s3")
    bucket = parameters["bucket_name"]
    loop = asyncio.get_event_loop()
    try:
        prev = await loop.run_in_executor(None, lambda: s3.get_public_access_block(Bucket=bucket))
        previous_config = prev.get("PublicAccessBlockConfiguration", {})
    except Exception:
        previous_config = {}
    config = {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}
    await loop.run_in_executor(None, lambda: s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration=config))
    return {"action": "block_s3_public_access", "bucket": bucket, "previous_config": previous_config}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "block_s3_public_access", "bucket": parameters.get("bucket_name", "mock-bucket"), "previous_config": {}}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    prev = execution_result.get("previous_config", {})
    if not prev:
        return {"rolled_back": False, "reason": "no previous config to restore"}
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "note": "mock rollback"}
    from ._client import get_boto3_client
    import asyncio
    s3 = get_boto3_client(creds, "s3")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: s3.put_public_access_block(Bucket=parameters["bucket_name"], PublicAccessBlockConfiguration=prev))
    return {"rolled_back": True}
```

- [ ] **Step 11: Create enable_cloudtrail.py, enable_guardduty.py, enforce_imdsv2.py, rotate_iam_access_key.py**

```python
# backend/app/connectors/executors/aws/enable_cloudtrail.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    ct = get_boto3_client(creds, "cloudtrail")
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: ct.create_trail(Name=parameters["trail_name"], S3BucketName=parameters["s3_bucket_name"], IsMultiRegionTrail=True))
    await loop.run_in_executor(None, lambda: ct.start_logging(Name=parameters["trail_name"]))
    return {"action": "enable_cloudtrail", "trail_arn": response.get("TrailARN"), "trail_name": parameters["trail_name"]}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "enable_cloudtrail", "trail_arn": "arn:aws:cloudtrail:us-east-1:123456789:trail/mock", "trail_name": parameters.get("trail_name", "mock")}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "note": "mock rollback"}
    from ._client import get_boto3_client
    import asyncio
    ct = get_boto3_client(creds, "cloudtrail")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ct.stop_logging(Name=parameters["trail_name"]))
    return {"rolled_back": True, "trail_name": parameters["trail_name"]}
```

```python
# backend/app/connectors/executors/aws/enable_guardduty.py
import asyncio


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    gd = get_boto3_client(creds, "guardduty")
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: gd.create_detector(Enable=True, FindingPublishingFrequency="SIX_HOURS"))
    return {"action": "enable_guardduty", "detector_id": response.get("DetectorId")}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "enable_guardduty", "detector_id": "mock-detector-id"}
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disabling GuardDuty requires explicit action — not auto-rolled back"}
```

```python
# backend/app/connectors/executors/aws/enforce_imdsv2.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    prev = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[parameters["instance_id"]]))
    prev_tokens = prev["Reservations"][0]["Instances"][0].get("MetadataOptions", {}).get("HttpTokens", "optional")
    await loop.run_in_executor(None, lambda: ec2.modify_instance_metadata_options(InstanceId=parameters["instance_id"], HttpTokens="required", HttpEndpoint="enabled"))
    return {"action": "enforce_imdsv2", "instance_id": parameters["instance_id"], "previous_http_tokens": prev_tokens}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "enforce_imdsv2", "instance_id": parameters.get("instance_id", "i-mock"), "previous_http_tokens": "optional"}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    prev = execution_result.get("previous_http_tokens", "optional")
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "note": "mock rollback"}
    from ._client import get_ec2_client
    import asyncio
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.modify_instance_metadata_options(InstanceId=parameters["instance_id"], HttpTokens=prev, HttpEndpoint="enabled"))
    return {"rolled_back": True, "http_tokens": prev}
```

```python
# backend/app/connectors/executors/aws/rotate_iam_access_key.py
import asyncio


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    iam = get_boto3_client(creds, "iam")
    loop = asyncio.get_event_loop()
    new_key = await loop.run_in_executor(None, lambda: iam.create_access_key(UserName=parameters["username"]))
    await loop.run_in_executor(None, lambda: iam.delete_access_key(UserName=parameters["username"], AccessKeyId=parameters["old_access_key_id"]))
    k = new_key["AccessKey"]
    return {"action": "rotate_iam_access_key", "username": parameters["username"], "new_access_key_id": k["AccessKeyId"], "new_secret_access_key": k["SecretAccessKey"]}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "rotate_iam_access_key", "username": parameters.get("username", "mock"), "new_access_key_id": "AKIAMOCK", "new_secret_access_key": "mock-secret"}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key deletion is irreversible — store new key securely"}
```

- [ ] **Step 12: Update _client.py in aws/ to support multiple service clients**

```python
# backend/app/connectors/executors/aws/_client.py
import boto3


def get_ec2_client(creds: dict):
    return _make_client(creds, "ec2")


def get_boto3_client(creds: dict, service: str):
    return _make_client(creds, service)


def _make_client(creds: dict, service: str):
    kwargs = {
        "aws_access_key_id": creds.get("access_key_id"),
        "aws_secret_access_key": creds.get("secret_access_key"),
        "region_name": creds.get("region", "us-east-1"),
    }
    if creds.get("session_token"):
        kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client(service, **kwargs)
```

- [ ] **Step 13: Commit**

```bash
git add backend/app/connectors/
git commit -m "feat: add 15 missing AWS actions (discover instances, IAM users, S3, GuardDuty, security groups, stop/start/terminate/launch, block public access, CloudTrail, GuardDuty, IMDSv2, key rotation)"
```

---

### Task 4: Okta — Replace Action Set

**Files:**
- Modify: `backend/app/connectors/catalog/okta.json`
- Delete: `backend/app/connectors/executors/okta/generate_key.py`, `distribute_key.py`, `verify_consumers.py`, `schedule_revoke.py`, `cancel_revoke.py`
- Create: 12 new executor files in `backend/app/connectors/executors/okta/`

- [ ] **Step 1: Replace okta.json catalog**

Replace the entire `actions` array with the proper Okta actions. Update `connector_type` to `"okta"`, `display_name` to `"Okta"`. Credential fields: `org_url` (string, required), `api_token` (password, required).

```json
{
  "connector_type": "okta",
  "display_name": "Okta",
  "credential_fields": [
    {"name": "org_url", "label": "Okta Org URL", "type": "string", "required": true, "placeholder": "https://acme.okta.com"},
    {"name": "api_token", "label": "API Token", "type": "password", "required": true}
  ],
  "actions": [
    {"action_id": "discover_users", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Users", "description": "All Okta users: status, MFA enrolled, last login, group memberships, app assignments", "applicable_asset_types": ["identity", "cloud_account"], "parameters": [], "executor": "okta.discover_users", "estimated_duration_seconds": 30},
    {"action_id": "discover_groups", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Groups", "description": "Okta groups and members", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "okta.discover_groups", "estimated_duration_seconds": 30},
    {"action_id": "discover_applications", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Applications", "description": "Okta applications and assigned users/groups", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "okta.discover_applications", "estimated_duration_seconds": 30},
    {"action_id": "suspend_user", "generic_action": "suspend_user", "action_type": "change", "execution_tier": 2, "display_name": "Suspend User", "description": "Suspend an Okta user account. Rollback: unsuspend.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.suspend_user", "estimated_duration_seconds": 5},
    {"action_id": "unsuspend_user", "generic_action": "unsuspend_user", "action_type": "change", "execution_tier": 2, "display_name": "Unsuspend User", "description": "Unsuspend a previously suspended user.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.unsuspend_user", "estimated_duration_seconds": 5},
    {"action_id": "deactivate_user", "generic_action": "deactivate_user", "action_type": "change", "execution_tier": 2, "display_name": "Deactivate User", "description": "Deactivate (deprovision) an Okta user. Rollback: reactivate.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.deactivate_user", "estimated_duration_seconds": 5},
    {"action_id": "reactivate_user", "generic_action": "reactivate_user", "action_type": "change", "execution_tier": 2, "display_name": "Reactivate User", "description": "Reactivate a deactivated user.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.reactivate_user", "estimated_duration_seconds": 5},
    {"action_id": "reset_password", "generic_action": "reset_password", "action_type": "change", "execution_tier": 2, "display_name": "Reset Password", "description": "Expire password and send reset email.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.reset_password", "estimated_duration_seconds": 5},
    {"action_id": "reset_mfa_factors", "generic_action": "reset_mfa", "action_type": "change", "execution_tier": 2, "display_name": "Reset MFA Factors", "description": "Reset all enrolled MFA factors — user re-enrolls on next login.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.reset_mfa_factors", "estimated_duration_seconds": 5},
    {"action_id": "revoke_sessions", "generic_action": "revoke_sessions", "action_type": "change", "execution_tier": 2, "display_name": "Revoke Sessions", "description": "Revoke all active Okta sessions for a user.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.revoke_sessions", "estimated_duration_seconds": 5},
    {"action_id": "force_mfa_enrollment", "generic_action": "force_mfa", "action_type": "change", "execution_tier": 2, "display_name": "Force MFA Enrollment", "description": "Set user to require MFA enrollment on next login.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}], "executor": "okta.force_mfa_enrollment", "estimated_duration_seconds": 5},
    {"action_id": "deprovision_from_app", "generic_action": "remove_app_access", "action_type": "change", "execution_tier": 2, "display_name": "Deprovision from App", "description": "Remove user assignment from a specific Okta application.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Okta user ID or login"}, {"name": "app_id", "type": "string", "required": true, "description": "Okta application ID"}], "executor": "okta.deprovision_from_app", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Remove old Okta executor files**

```bash
rm backend/app/connectors/executors/okta/generate_key.py
rm backend/app/connectors/executors/okta/distribute_key.py
rm backend/app/connectors/executors/okta/verify_consumers.py
rm backend/app/connectors/executors/okta/schedule_revoke.py
rm backend/app/connectors/executors/okta/cancel_revoke.py
```

- [ ] **Step 3: Create Okta _client.py**

```python
# backend/app/connectors/executors/okta/_client.py
import httpx


def get_okta_client(creds: dict) -> httpx.AsyncClient:
    org_url = creds["org_url"].rstrip("/")
    token = creds["api_token"]
    return httpx.AsyncClient(
        base_url=f"{org_url}/api/v1",
        headers={"Authorization": f"SSWS {token}", "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )
```

- [ ] **Step 4: Create discover_users.py, discover_groups.py, discover_applications.py**

```python
# backend/app/connectors/executors/okta/discover_users.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_users", "users": [{"id": "mock-user-1", "login": "alice@example.com", "status": "ACTIVE", "mfa_enrolled": True}], "count": 1}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.get("/users", params={"limit": 200})
        resp.raise_for_status()
        users = [{"id": u["id"], "login": u["profile"]["login"], "status": u["status"], "last_login": u.get("lastLogin")} for u in resp.json()]
    return {"action": "discover_users", "users": users, "count": len(users)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/okta/discover_groups.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_groups", "groups": [{"id": "mock-group-1", "name": "Everyone", "member_count": 10}], "count": 1}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.get("/groups", params={"limit": 200})
        resp.raise_for_status()
        groups = [{"id": g["id"], "name": g["profile"]["name"], "description": g["profile"].get("description")} for g in resp.json()]
    return {"action": "discover_groups", "groups": groups, "count": len(groups)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/okta/discover_applications.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_applications", "applications": [{"id": "mock-app-1", "name": "Salesforce", "status": "ACTIVE"}], "count": 1}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.get("/apps", params={"limit": 200})
        resp.raise_for_status()
        apps = [{"id": a["id"], "name": a["label"], "status": a["status"]} for a in resp.json()]
    return {"action": "discover_applications", "applications": apps, "count": len(apps)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 5: Create user lifecycle executors (suspend, unsuspend, deactivate, reactivate, reset_password, reset_mfa_factors, revoke_sessions, force_mfa_enrollment, deprovision_from_app)**

```python
# backend/app/connectors/executors/okta/suspend_user.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "suspend_user", "user_id": user_id, "status": "SUSPENDED"}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.post(f"/users/{user_id}/lifecycle/suspend")
        resp.raise_for_status()
    return {"action": "suspend_user", "user_id": user_id, "status": "SUSPENDED"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.okta.unsuspend_user import execute as unsuspend
    return await unsuspend(parameters, [], connector)
```

```python
# backend/app/connectors/executors/okta/unsuspend_user.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "unsuspend_user", "user_id": user_id, "status": "ACTIVE"}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.post(f"/users/{user_id}/lifecycle/unsuspend")
        resp.raise_for_status()
    return {"action": "unsuspend_user", "user_id": user_id, "status": "ACTIVE"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unsuspend rollback would suspend user — use suspend_user explicitly"}
```

```python
# backend/app/connectors/executors/okta/deactivate_user.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "deactivate_user", "user_id": user_id, "status": "DEPROVISIONED"}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.post(f"/users/{user_id}/lifecycle/deactivate")
        resp.raise_for_status()
    return {"action": "deactivate_user", "user_id": user_id, "status": "DEPROVISIONED"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.okta.reactivate_user import execute as reactivate
    return await reactivate(parameters, [], connector)
```

```python
# backend/app/connectors/executors/okta/reactivate_user.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "reactivate_user", "user_id": user_id, "status": "ACTIVE"}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.post(f"/users/{user_id}/lifecycle/reactivate")
        resp.raise_for_status()
    return {"action": "reactivate_user", "user_id": user_id, "status": "ACTIVE"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reactivate rollback would deactivate — use deactivate_user explicitly"}
```

```python
# backend/app/connectors/executors/okta/reset_password.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "reset_password", "user_id": user_id, "reset_email_sent": True}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.post(f"/users/{user_id}/lifecycle/reset_password", params={"sendEmail": "true"})
        resp.raise_for_status()
    return {"action": "reset_password", "user_id": user_id, "reset_email_sent": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "password reset email is already sent"}
```

```python
# backend/app/connectors/executors/okta/reset_mfa_factors.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "reset_mfa_factors", "user_id": user_id, "factors_reset": True}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.get(f"/users/{user_id}/factors")
        resp.raise_for_status()
        factors = resp.json()
        for factor in factors:
            if factor.get("status") == "ACTIVE":
                await client.delete(f"/users/{user_id}/factors/{factor['id']}")
    return {"action": "reset_mfa_factors", "user_id": user_id, "factors_reset": len(factors)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot restore deleted MFA factors"}
```

```python
# backend/app/connectors/executors/okta/revoke_sessions.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "revoke_sessions", "user_id": user_id, "sessions_revoked": True}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.delete(f"/users/{user_id}/sessions")
        resp.raise_for_status()
    return {"action": "revoke_sessions", "user_id": user_id, "sessions_revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot restore revoked sessions"}
```

```python
# backend/app/connectors/executors/okta/force_mfa_enrollment.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "force_mfa_enrollment", "user_id": user_id, "enrollment_required": True}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.post(f"/users/{user_id}/lifecycle/reset_password", params={"sendEmail": "false"})
        resp.raise_for_status()
    return {"action": "force_mfa_enrollment", "user_id": user_id, "enrollment_required": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enrollment state cannot be reversed automatically"}
```

```python
# backend/app/connectors/executors/okta/deprovision_from_app.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    app_id = parameters["app_id"]
    if not creds:
        return {"action": "deprovision_from_app", "user_id": user_id, "app_id": app_id, "removed": True}
    from ._client import get_okta_client
    async with get_okta_client(creds) as client:
        resp = await client.delete(f"/apps/{app_id}/users/{user_id}")
        resp.raise_for_status()
    return {"action": "deprovision_from_app", "user_id": user_id, "app_id": app_id, "removed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-assigning user to app requires manual configuration"}
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/okta.json backend/app/connectors/executors/okta/
git commit -m "feat: replace Okta stub actions with proper user lifecycle and identity management actions"
```

---

### Task 5: Azure, Palo Alto, Active Directory, CrowdStrike, Cloudflare, Tenable, SSH — Gap-Fill

**Files:** Multiple catalog JSONs and executor files for 7 connectors.

This task adds the actions specified in the spec for each connector. Follow the same pattern as AWS: add to the JSON catalog and create minimal executor files that return mock data if `connector.credentials` is empty, or call the real SDK otherwise.

- [ ] **Step 1: Azure — add 9 new actions to catalog and executors**

Add to `azure.json` actions array:
- `discover_entra_users` (ingest) — executor: `azure.discover_entra_users`
- `discover_entra_groups` (ingest) — executor: `azure.discover_entra_groups`
- `discover_subscriptions` (ingest) — executor: `azure.discover_subscriptions`
- `ingest_defender_alerts` (ingest) — executor: `azure.ingest_defender_alerts`
- `ingest_policy_compliance` (ingest) — executor: `azure.ingest_policy_compliance`
- `disable_entra_user` (change, tier 2) — executor: `azure.disable_entra_user`
- `enable_entra_user` (change, tier 2) — executor: `azure.enable_entra_user`
- `reset_entra_mfa` (change, tier 2) — executor: `azure.reset_entra_mfa`
- `revoke_entra_sessions` (change, tier 2) — executor: `azure.revoke_entra_sessions`

Each executor follows the real-if-credentials/mock-if-not pattern. Real implementations use `msgraph-sdk-python` for Entra ID operations:

```python
# backend/app/connectors/executors/azure/discover_entra_users.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_entra_users", "users": [
            {"id": "mock-user-id", "upn": "alice@example.com", "display_name": "Alice", "account_enabled": True, "mfa_registered": True}
        ], "count": 1}
    from ._client import get_graph_client
    client = await get_graph_client(creds)
    result = await client.users.get()
    users = [{"id": u.id, "upn": u.user_principal_name, "display_name": u.display_name, "account_enabled": u.account_enabled} for u in (result.value or [])]
    return {"action": "discover_entra_users", "users": users, "count": len(users)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

Create similar files for all 9 Azure actions. The `_client.py` for Azure should add a `get_graph_client` function using `msgraph-sdk-python`.

- [ ] **Step 2: Palo Alto — add 13 actions to catalog and executors**

Add to `paloalto.json` and create executors:
- `discover_address_objects`, `discover_security_zones`, `discover_security_rules`, `discover_nat_rules`, `discover_interfaces` (all ingest)
- `create_address_object`, `delete_address_object`, `add_to_address_group`, `remove_from_address_group`, `create_security_rule`, `delete_security_rule`, `block_ip`, `commit_changes` (all change)

Real implementations use `pan-os-python` SDK already installed. Mock returns static data.

Example executor:
```python
# backend/app/connectors/executors/paloalto/discover_address_objects.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_address_objects", "objects": [
            {"name": "mock-addr-1", "value": "10.0.0.1", "type": "ip-netmask"}
        ], "count": 1}
    from ._client import get_panos_device
    import asyncio
    device = get_panos_device(creds)
    loop = asyncio.get_event_loop()
    from panos.objects import AddressObject
    objs = await loop.run_in_executor(None, lambda: AddressObject.refreshall(device))
    result = [{"name": o.name, "value": o.value, "type": o.type} for o in (objs or [])]
    return {"action": "discover_address_objects", "objects": result, "count": len(result)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 3: Active Directory — add 7 actions to catalog and executors**

Add to `active_directory.json` and create executors:
- `discover_domain_admins`, `discover_stale_accounts`, `discover_spn_accounts`, `discover_gpos`, `discover_password_policy` (ingest)
- `disable_stale_accounts`, `move_to_ou` (change)

Real implementations use `ldap3` already installed.

- [ ] **Step 4: CrowdStrike — add 6 actions to catalog and executors**

Add to `crowdstrike.json` and create executors:
- `ingest_spotlight_findings`, `discover_unmanaged_devices` (ingest)
- `get_host_details`, `update_prevention_policy`, `run_rtr_command`, `put_file` (change)

Real implementations use `falconpy` already installed.

- [ ] **Step 5: Cloudflare — add 8 actions to catalog and executors**

Add to `cloudflare.json` and create executors:
- `discover_waf_custom_rules`, `discover_access_policies`, `discover_firewall_rules` (ingest)
- `create_firewall_rule`, `delete_firewall_rule`, `block_ip`, `update_waf_rule_action`, `set_ssl_mode` (change)

Real implementations use `httpx` already installed.

- [ ] **Step 6: Tenable — add 6 actions to catalog and executors**

Add to `tenable.json` and create executors:
- `discover_scan_policies`, `discover_scan_results` (ingest)
- `create_scan`, `pause_scan`, `resume_scan`, `export_vulnerability_report` (change)

Real implementations use `pytenable` already installed.

- [ ] **Step 7: SSH — add 2 actions to catalog and executors**

Add to `ssh.json` and create executors:
- `check_service_status`, `tail_log` (change)

Real implementations use `paramiko` already installed.

- [ ] **Step 8: Commit all gap-fill executors**

```bash
git add backend/app/connectors/
git commit -m "feat: add missing actions for Azure, PaloAlto, ActiveDirectory, CrowdStrike, Cloudflare, Tenable, SSH connectors"
```

---

### Task 6: Frontend — Update ConnectorType + Labels + Icons

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/Connectors.tsx`
- Modify: `frontend/src/components/AddConnectorModal.tsx`

- [ ] **Step 1: Update ConnectorType in api.ts**

Find and replace the ConnectorType union:

```typescript
export type ConnectorType =
  | 'aws'
  | 'azure'
  | 'cloudflare'
  | 'okta'
  | 'paloalto'
  | 'ssh'
  | 'active_directory'
  | 'crowdstrike'
  | 'tenable'
  | 'nexplane_agent';
```

- [ ] **Step 2: Update CONNECTOR_LABELS in Connectors.tsx**

```typescript
const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  aws: 'Amazon Web Services',
  azure: 'Microsoft Azure',
  cloudflare: 'Cloudflare',
  okta: 'Okta',
  paloalto: 'Palo Alto Networks',
  ssh: 'SSH',
  active_directory: 'Active Directory',
  crowdstrike: 'CrowdStrike',
  tenable: 'Tenable',
  nexplane_agent: 'Nexplane Agent',
};
```

- [ ] **Step 3: Update CONNECTOR_ICONS in Connectors.tsx**

Keep the same icon assignments but update keys to drop `_mock` suffix.

- [ ] **Step 4: Update AddConnectorModal.tsx**

Same changes: replace all `_mock` references with production names. The dropdown options should use the new ConnectorType values.

- [ ] **Step 5: Verify frontend builds**

```bash
docker compose exec frontend npm run build 2>&1 | tail -5
```
Expected: no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/
git commit -m "feat: update frontend ConnectorType values from _mock to production names"
```

---

### Task 7: Integration Test

**Files:**
- Create: `backend/app/tests/test_connector_6a.py`

- [ ] **Step 1: Write integration test**

```python
# backend/app/tests/test_connector_6a.py
import pytest


@pytest.mark.asyncio
async def test_aws_discover_instances_mock(auth_client):
    """Mock execution returns instances list without real credentials."""
    from app.connectors.executors.aws.discover_instances import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_instances"
    assert isinstance(result["instances"], list)
    assert result["count"] >= 0


@pytest.mark.asyncio
async def test_okta_suspend_user_mock(auth_client):
    """Mock execution returns suspended status without real credentials."""
    from app.connectors.executors.okta.suspend_user import execute
    result = await execute({"user_id": "test-user"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "suspend_user"
    assert result["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_okta_suspend_rollback(auth_client):
    """Suspend rollback calls unsuspend."""
    from app.connectors.executors.okta.suspend_user import rollback
    result = await rollback({"user_id": "test-user"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "unsuspend_user"


@pytest.mark.asyncio
async def test_aws_stop_instance_rollback(auth_client):
    """Stop instance rollback calls start instance."""
    from app.connectors.executors.aws.stop_instance import rollback
    result = await rollback({"instance_id": "i-test"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "start_instance"
```

- [ ] **Step 2: Run tests**

```bash
docker compose exec backend pytest app/tests/test_connector_6a.py -v
```
Expected: All 4 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/tests/test_connector_6a.py
git commit -m "test: add integration tests for 6a connector upgrades"
```
