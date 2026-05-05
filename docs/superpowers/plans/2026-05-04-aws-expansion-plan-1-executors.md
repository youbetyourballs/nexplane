# AWS Expansion — Plan 1: Executors, Change Types & Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 15 new AWS executors, 12 new change type JSON definitions, one Alembic migration for new ChangeType enum values, and rollback executor coverage for two existing change types that lack it.

**Architecture:** Each executor lives in `backend/app/connectors/executors/aws/` following the existing pattern: mock return when no credentials, real boto3 call otherwise, `_auto_asset` dict on create operations. Change type JSONs in `backend/app/connectors/change_type_definitions/`. Catalog entries added to `backend/app/connectors/catalog/aws.json`. One migration (`023_add_aws_expansion_change_types.py`) adds all new `ChangeType` enum values.

**Tech Stack:** Python 3.12, boto3, asyncio, Alembic/PostgreSQL, FastAPI

---

## Files

**Create:**
- `backend/app/connectors/executors/aws/create_iam_user.py`
- `backend/app/connectors/executors/aws/delete_iam_user.py`
- `backend/app/connectors/executors/aws/create_s3_bucket.py`
- `backend/app/connectors/executors/aws/delete_s3_bucket.py`
- `backend/app/connectors/executors/aws/set_s3_lifecycle.py`
- `backend/app/connectors/executors/aws/create_route53_zone.py`
- `backend/app/connectors/executors/aws/delete_route53_zone.py`
- `backend/app/connectors/executors/aws/upsert_route53_record.py`
- `backend/app/connectors/executors/aws/delete_route53_record.py`
- `backend/app/connectors/executors/aws/create_rds_instance.py`
- `backend/app/connectors/executors/aws/delete_rds_instance.py`
- `backend/app/connectors/executors/aws/delete_rds_snapshot.py`
- `backend/app/connectors/executors/aws/create_cloudwatch_alarm.py`
- `backend/app/connectors/executors/aws/delete_cloudwatch_alarm.py`
- `backend/app/connectors/executors/aws/delete_ebs_snapshot.py`
- `backend/app/connectors/change_type_definitions/iam_user_create.json`
- `backend/app/connectors/change_type_definitions/iam_user_delete.json`
- `backend/app/connectors/change_type_definitions/s3_bucket_create.json`
- `backend/app/connectors/change_type_definitions/s3_bucket_delete.json`
- `backend/app/connectors/change_type_definitions/s3_lifecycle_configure.json`
- `backend/app/connectors/change_type_definitions/route53_zone_create.json`
- `backend/app/connectors/change_type_definitions/route53_record_upsert.json`
- `backend/app/connectors/change_type_definitions/route53_record_delete.json`
- `backend/app/connectors/change_type_definitions/rds_instance_create.json`
- `backend/app/connectors/change_type_definitions/rds_instance_delete.json`
- `backend/app/connectors/change_type_definitions/rds_snapshot_create.json`
- `backend/app/connectors/change_type_definitions/cloudwatch_alarm_create.json`
- `backend/app/connectors/change_type_definitions/cloudwatch_alarm_delete.json`
- `backend/alembic/versions/023_add_aws_expansion_change_types.py`

**Modify:**
- `backend/app/models/change_request.py` — add 12 new ChangeType enum values
- `backend/app/connectors/catalog/aws.json` — add 15 new action entries
- `backend/app/connectors/change_type_definitions/ec2_stop.json` — add rollback_action
- `backend/app/connectors/change_type_definitions/ec2_start.json` — add rollback_action
- `backend/app/connectors/change_type_definitions/snapshot_asset.json` — add rollback_action

---

### Task 1: Alembic migration + ChangeType enum

**Files:**
- Create: `backend/alembic/versions/023_add_aws_expansion_change_types.py`
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Add ChangeType enum values**

Open `backend/app/models/change_request.py`. Find the `ChangeType` class and add after line with `ansible_local_playbook`:

```python
    # AWS expansion — Plan 1
    iam_user_create = "iam_user_create"
    iam_user_delete = "iam_user_delete"
    s3_bucket_create = "s3_bucket_create"
    s3_bucket_delete = "s3_bucket_delete"
    s3_lifecycle_configure = "s3_lifecycle_configure"
    route53_zone_create = "route53_zone_create"
    route53_record_upsert = "route53_record_upsert"
    route53_record_delete = "route53_record_delete"
    rds_instance_create = "rds_instance_create"
    rds_instance_delete = "rds_instance_delete"
    rds_snapshot_create = "rds_snapshot_create"
    cloudwatch_alarm_create = "cloudwatch_alarm_create"
    cloudwatch_alarm_delete = "cloudwatch_alarm_delete"
```

- [ ] **Step 2: Write migration**

Create `backend/alembic/versions/023_add_aws_expansion_change_types.py`:

```python
"""add aws expansion change types

Revision ID: 023
Revises: 022
Create Date: 2026-05-04
"""
from alembic import op

revision = '023'
down_revision = '022'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'iam_user_create', 'iam_user_delete',
        's3_bucket_create', 's3_bucket_delete', 's3_lifecycle_configure',
        'route53_zone_create', 'route53_record_upsert', 'route53_record_delete',
        'rds_instance_create', 'rds_instance_delete', 'rds_snapshot_create',
        'cloudwatch_alarm_create', 'cloudwatch_alarm_delete',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 3: Apply migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head
```

Expected output ends with: `Running upgrade 022 -> 023`

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/023_add_aws_expansion_change_types.py backend/app/models/change_request.py
git commit -m "feat: add 13 new ChangeType enum values for AWS expansion"
```

---

### Task 2: IAM executors

**Files:**
- Create: `backend/app/connectors/executors/aws/create_iam_user.py`
- Create: `backend/app/connectors/executors/aws/delete_iam_user.py`

- [ ] **Step 1: Write `create_iam_user.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    username = parameters.get('username', 'nexplane-user')
    tags = [{"Key": "ManagedBy", "Value": "nexplane"}]

    if not creds:
        return {
            "action": "create_iam_user",
            "username": username,
            "access_key_id": "AKIAMOCKKEY0000001",
            "mock": True,
            "_auto_asset": {
                "name": username,
                "asset_type": "identity",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"username": username, "provider": "aws"},
                "tags": ["iam", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    iam = get_boto3_client(creds, 'iam')
    loop = asyncio.get_event_loop()

    def _call():
        iam.create_user(UserName=username, Tags=tags)
        key_resp = iam.create_access_key(UserName=username)
        return key_resp['AccessKey']

    key = await loop.run_in_executor(None, _call)
    return {
        "action": "create_iam_user",
        "username": username,
        "access_key_id": key['AccessKeyId'],
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "username": username,
                "access_key_id": key['AccessKeyId'],
                "region": creds.get('region', 'us-east-1'),
                "provider": "aws",
            },
            "tags": ["iam", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_iam_user import execute as delete
    return await delete({"username": execution_result.get('username', parameters.get('username'))}, [], connector)
```

- [ ] **Step 2: Write `delete_iam_user.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    username = parameters.get('username', '')

    if not creds:
        return {"action": "delete_iam_user", "username": username, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    iam = get_boto3_client(creds, 'iam')
    loop = asyncio.get_event_loop()

    def _call():
        # Delete all access keys
        keys = iam.list_access_keys(UserName=username)['AccessKeyMetadata']
        for k in keys:
            iam.delete_access_key(UserName=username, AccessKeyId=k['AccessKeyId'])
        # Detach all managed policies
        policies = iam.list_attached_user_policies(UserName=username)['AttachedPolicies']
        for p in policies:
            iam.detach_user_policy(UserName=username, PolicyArn=p['PolicyArn'])
        # Delete login profile if exists
        try:
            iam.delete_login_profile(UserName=username)
        except iam.exceptions.NoSuchEntityException:
            pass
        iam.delete_user(UserName=username)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_iam_user",
        "username": username,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_user is terminal — user data cannot be recovered"}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/aws/create_iam_user.py backend/app/connectors/executors/aws/delete_iam_user.py
git commit -m "feat: add create_iam_user and delete_iam_user executors"
```

---

### Task 3: S3 advanced executors

**Files:**
- Create: `backend/app/connectors/executors/aws/create_s3_bucket.py`
- Create: `backend/app/connectors/executors/aws/delete_s3_bucket.py`
- Create: `backend/app/connectors/executors/aws/set_s3_lifecycle.py`

- [ ] **Step 1: Write `create_s3_bucket.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', 'nexplane-bucket')
    region = (creds.get('region', 'us-east-1') if creds else 'us-east-1')

    if not creds:
        return {
            "action": "create_s3_bucket",
            "bucket_name": bucket_name,
            "mock": True,
            "_auto_asset": {
                "name": bucket_name,
                "asset_type": "storage_bucket",
                "environment": "prod",
                "criticality": "medium",
                "asset_metadata": {"bucket_name": bucket_name, "region": region, "provider": "aws"},
                "tags": ["s3", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = {"Bucket": bucket_name}
        if region != 'us-east-1':
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
        s3.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={"TagSet": [{"Key": "ManagedBy", "Value": "nexplane"}]},
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "create_s3_bucket",
        "bucket_name": bucket_name,
        "region": region,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": bucket_name,
            "asset_type": "storage_bucket",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {"bucket_name": bucket_name, "region": region, "provider": "aws"},
            "tags": ["s3", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_s3_bucket import execute as delete
    return await delete({"bucket_name": execution_result.get('bucket_name', parameters.get('bucket_name'))}, [], connector)
```

- [ ] **Step 2: Write `delete_s3_bucket.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', '')

    if not creds:
        return {"action": "delete_s3_bucket", "bucket_name": bucket_name, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        # Delete all object versions (required before bucket deletion)
        paginator = s3.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket_name):
            objects = []
            for v in page.get('Versions', []):
                objects.append({'Key': v['Key'], 'VersionId': v['VersionId']})
            for m in page.get('DeleteMarkers', []):
                objects.append({'Key': m['Key'], 'VersionId': m['VersionId']})
            if objects:
                s3.delete_objects(Bucket=bucket_name, Delete={'Objects': objects})
        s3.delete_bucket(Bucket=bucket_name)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_s3_bucket",
        "bucket_name": bucket_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_s3_bucket is terminal — bucket contents cannot be recovered"}
```

- [ ] **Step 3: Write `set_s3_lifecycle.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', '')
    rules = parameters.get('rules', [])  # list of S3 lifecycle rule dicts
    prior_rules = parameters.get('prior_rules', None)  # stored for rollback

    if not creds:
        return {"action": "set_s3_lifecycle", "bucket_name": bucket_name, "rules_applied": len(rules), "mock": True}

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        # Capture existing rules for rollback
        existing = []
        try:
            existing = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name).get('Rules', [])
        except s3.exceptions.ClientError:
            pass

        if rules:
            s3.put_bucket_lifecycle_configuration(
                Bucket=bucket_name,
                LifecycleConfiguration={"Rules": rules},
            )
        else:
            try:
                s3.delete_bucket_lifecycle(Bucket=bucket_name)
            except Exception:
                pass
        return existing

    existing_rules = await loop.run_in_executor(None, _call)
    return {
        "action": "set_s3_lifecycle",
        "bucket_name": bucket_name,
        "rules_applied": len(rules),
        "prior_rules": existing_rules,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    prior = execution_result.get('prior_rules', [])
    bucket = execution_result.get('bucket_name', parameters.get('bucket_name', ''))
    return await execute({"bucket_name": bucket, "rules": prior}, [], connector)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/aws/create_s3_bucket.py backend/app/connectors/executors/aws/delete_s3_bucket.py backend/app/connectors/executors/aws/set_s3_lifecycle.py
git commit -m "feat: add S3 bucket create/delete/lifecycle executors"
```

---

### Task 4: Route53 executors

**Files:**
- Create: `backend/app/connectors/executors/aws/create_route53_zone.py`
- Create: `backend/app/connectors/executors/aws/delete_route53_zone.py`
- Create: `backend/app/connectors/executors/aws/upsert_route53_record.py`
- Create: `backend/app/connectors/executors/aws/delete_route53_record.py`

- [ ] **Step 1: Write `create_route53_zone.py`**

```python
import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_name = parameters.get('zone_name', 'smoke-test.nexplane.internal')
    private = parameters.get('private', True)
    vpc_id = parameters.get('vpc_id', None)
    region = creds.get('region', 'us-east-1') if creds else 'us-east-1'

    if not creds:
        return {
            "action": "create_route53_zone",
            "zone_id": "Z_MOCKZONE01",
            "zone_name": zone_name,
            "mock": True,
            "_auto_asset": {
                "name": zone_name,
                "asset_type": "dns_zone",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"zone_id": "Z_MOCKZONE01", "zone_name": zone_name, "private_zone": private},
                "tags": ["route53", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    ec2 = get_boto3_client(creds, 'ec2') if private else None
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = {
            "Name": zone_name,
            "CallerReference": f"nexplane-{int(time.time())}",
            "HostedZoneConfig": {"Comment": "Created by Nexplane", "PrivateZone": private},
        }
        if private:
            # Use default VPC if none specified
            actual_vpc_id = vpc_id
            if not actual_vpc_id:
                vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])['Vpcs']
                actual_vpc_id = vpcs[0]['VpcId'] if vpcs else None
            if actual_vpc_id:
                kwargs["VPC"] = {"VPCRegion": region, "VPCId": actual_vpc_id}
        resp = r53.create_hosted_zone(**kwargs)
        return resp['HostedZone']['Id'].split('/')[-1], resp['HostedZone']['Name']

    zone_id, zone_name_returned = await loop.run_in_executor(None, _call)
    return {
        "action": "create_route53_zone",
        "zone_id": zone_id,
        "zone_name": zone_name_returned,
        "private": private,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": zone_name_returned.rstrip('.'),
            "asset_type": "dns_zone",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "zone_id": zone_id,
                "zone_name": zone_name_returned,
                "private_zone": private,
                "region": region,
            },
            "tags": ["route53", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_route53_zone import execute as delete
    return await delete({
        "zone_id": execution_result.get('zone_id'),
        "zone_name": execution_result.get('zone_name'),
    }, [], connector)
```

- [ ] **Step 2: Write `delete_route53_zone.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')

    if not creds:
        return {"action": "delete_route53_zone", "zone_id": zone_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        # Delete all non-SOA/NS records first
        paginator = r53.get_paginator('list_resource_record_sets')
        changes = []
        for page in paginator.paginate(HostedZoneId=zone_id):
            for rrs in page['ResourceRecordSets']:
                if rrs['Type'] in ('SOA', 'NS') and rrs['Name'] == rrs['Name']:
                    continue
                changes.append({'Action': 'DELETE', 'ResourceRecordSet': rrs})
        if changes:
            r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={'Changes': changes},
            )
        r53.delete_hosted_zone(Id=zone_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_route53_zone",
        "zone_id": zone_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_route53_zone is terminal"}
```

- [ ] **Step 3: Write `upsert_route53_record.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')
    name = parameters.get('name', '')
    record_type = parameters.get('record_type', 'A')
    values = parameters.get('values', [])  # list of strings e.g. ["10.0.0.1"]
    ttl = parameters.get('ttl', 60)
    weight = parameters.get('weight', None)
    set_identifier = parameters.get('set_identifier', None)

    if not creds:
        return {
            "action": "upsert_route53_record",
            "zone_id": zone_id,
            "name": name,
            "type": record_type,
            "values": values,
            "mock": True,
        }

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        # Capture prior record for rollback
        prior = None
        try:
            resp = r53.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=name,
                StartRecordType=record_type,
                MaxItems='1',
            )
            for rrs in resp['ResourceRecordSets']:
                if rrs['Name'].rstrip('.') == name.rstrip('.') and rrs['Type'] == record_type:
                    prior = rrs
                    break
        except Exception:
            pass

        rrs = {
            "Name": name,
            "Type": record_type,
            "TTL": ttl,
            "ResourceRecords": [{"Value": v} for v in values],
        }
        if weight is not None:
            rrs["Weight"] = weight
            rrs["SetIdentifier"] = set_identifier or name

        r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={"Changes": [{"Action": "UPSERT", "ResourceRecordSet": rrs}]},
        )
        return prior

    prior_record = await loop.run_in_executor(None, _call)
    return {
        "action": "upsert_route53_record",
        "zone_id": zone_id,
        "name": name,
        "type": record_type,
        "values": values,
        "prior_record": prior_record,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    prior = execution_result.get('prior_record')
    if prior:
        # Restore prior record
        return await execute({
            "zone_id": execution_result['zone_id'],
            "name": prior['Name'],
            "record_type": prior['Type'],
            "values": [r['Value'] for r in prior.get('ResourceRecords', [])],
            "ttl": prior.get('TTL', 60),
        }, [], connector)
    else:
        # No prior record — delete the one we created
        from app.connectors.executors.aws.delete_route53_record import execute as delete
        return await delete({
            "zone_id": execution_result['zone_id'],
            "name": execution_result['name'],
            "record_type": execution_result['type'],
            "values": execution_result['values'],
            "ttl": 60,
        }, [], connector)
```

- [ ] **Step 4: Write `delete_route53_record.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')
    name = parameters.get('name', '')
    record_type = parameters.get('record_type', 'A')
    values = parameters.get('values', [])
    ttl = parameters.get('ttl', 60)
    weight = parameters.get('weight', None)
    set_identifier = parameters.get('set_identifier', None)

    if not creds:
        return {"action": "delete_route53_record", "zone_id": zone_id, "name": name, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        rrs = {
            "Name": name,
            "Type": record_type,
            "TTL": ttl,
            "ResourceRecords": [{"Value": v} for v in values],
        }
        if weight is not None:
            rrs["Weight"] = weight
            rrs["SetIdentifier"] = set_identifier or name
        r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={"Changes": [{"Action": "DELETE", "ResourceRecordSet": rrs}]},
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_route53_record",
        "zone_id": zone_id,
        "name": name,
        "type": record_type,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.upsert_route53_record import execute as upsert
    return await upsert({
        "zone_id": execution_result.get('zone_id', parameters.get('zone_id')),
        "name": execution_result.get('name', parameters.get('name')),
        "record_type": execution_result.get('type', parameters.get('record_type', 'A')),
        "values": parameters.get('values', []),
        "ttl": parameters.get('ttl', 60),
    }, [], connector)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/aws/create_route53_zone.py backend/app/connectors/executors/aws/delete_route53_zone.py backend/app/connectors/executors/aws/upsert_route53_record.py backend/app/connectors/executors/aws/delete_route53_record.py
git commit -m "feat: add Route53 zone and record executors"
```

---

### Task 5: RDS executors

**Files:**
- Create: `backend/app/connectors/executors/aws/create_rds_instance.py`
- Create: `backend/app/connectors/executors/aws/delete_rds_instance.py`
- Create: `backend/app/connectors/executors/aws/delete_rds_snapshot.py`

- [ ] **Step 1: Write `create_rds_instance.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    db_id = parameters.get('db_instance_identifier', 'nexplane-db')
    engine = parameters.get('engine', 'mysql')
    engine_version = parameters.get('engine_version', '8.0')
    instance_class = parameters.get('db_instance_class', 'db.t3.micro')
    username = parameters.get('master_username', 'admin')
    password = parameters.get('master_password', 'Nexplane!Smoke1')
    storage = parameters.get('allocated_storage', 20)
    skip_final = parameters.get('skip_final_snapshot', True)

    if not creds:
        return {
            "action": "create_rds_instance",
            "db_instance_identifier": db_id,
            "endpoint": "mock-rds.rds.amazonaws.com",
            "port": 3306,
            "mock": True,
            "_auto_asset": {
                "name": db_id,
                "asset_type": "database",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"db_instance_identifier": db_id, "engine": engine, "provider": "aws"},
                "tags": ["rds", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        rds.create_db_instance(
            DBInstanceIdentifier=db_id,
            DBInstanceClass=instance_class,
            Engine=engine,
            EngineVersion=engine_version,
            MasterUsername=username,
            MasterUserPassword=password,
            AllocatedStorage=storage,
            PubliclyAccessible=False,
            SkipFinalSnapshot=skip_final,
            Tags=[{"Key": "ManagedBy", "Value": "nexplane"}],
        )
        waiter = rds.get_waiter('db_instance_available')
        waiter.wait(
            DBInstanceIdentifier=db_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 40},  # up to 20 min
        )
        info = rds.describe_db_instances(DBInstanceIdentifier=db_id)['DBInstances'][0]
        ep = info.get('Endpoint', {})
        return ep.get('Address', ''), ep.get('Port', 3306)

    endpoint, port = await loop.run_in_executor(None, _call)
    return {
        "action": "create_rds_instance",
        "db_instance_identifier": db_id,
        "endpoint": endpoint,
        "port": port,
        "engine": engine,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": db_id,
            "asset_type": "database",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "db_instance_identifier": db_id,
                "engine": engine,
                "endpoint": endpoint,
                "port": port,
                "region": creds.get('region', 'us-east-1'),
                "provider": "aws",
            },
            "tags": ["rds", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_rds_instance import execute as delete
    return await delete(
        {"db_instance_identifier": execution_result.get('db_instance_identifier', parameters.get('db_instance_identifier'))},
        [], connector,
    )
```

- [ ] **Step 2: Write `delete_rds_instance.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    db_id = parameters.get('db_instance_identifier', '')

    if not creds:
        return {"action": "delete_rds_instance", "db_instance_identifier": db_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        rds.delete_db_instance(
            DBInstanceIdentifier=db_id,
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True,
        )
        waiter = rds.get_waiter('db_instance_deleted')
        waiter.wait(
            DBInstanceIdentifier=db_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 40},  # up to 20 min
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_rds_instance",
        "db_instance_identifier": db_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_rds_instance is terminal — instance cannot be recreated automatically"}
```

- [ ] **Step 3: Write `delete_rds_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    snapshot_id = parameters.get('snapshot_identifier', '')

    if not creds:
        return {"action": "delete_rds_snapshot", "snapshot_identifier": snapshot_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_rds_snapshot",
        "snapshot_identifier": snapshot_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_rds_snapshot is terminal — snapshot data cannot be recovered"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/aws/create_rds_instance.py backend/app/connectors/executors/aws/delete_rds_instance.py backend/app/connectors/executors/aws/delete_rds_snapshot.py
git commit -m "feat: add RDS instance create/delete and snapshot delete executors"
```

---

### Task 6: CloudWatch + EBS rollback executors

**Files:**
- Create: `backend/app/connectors/executors/aws/create_cloudwatch_alarm.py`
- Create: `backend/app/connectors/executors/aws/delete_cloudwatch_alarm.py`
- Create: `backend/app/connectors/executors/aws/delete_ebs_snapshot.py`

- [ ] **Step 1: Write `create_cloudwatch_alarm.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    alarm_name = parameters.get('alarm_name', 'nexplane-alarm')
    metric_name = parameters.get('metric_name', 'CPUUtilization')
    namespace = parameters.get('namespace', 'AWS/EC2')
    threshold = parameters.get('threshold', 80.0)
    comparison = parameters.get('comparison_operator', 'GreaterThanThreshold')
    evaluation_periods = parameters.get('evaluation_periods', 1)
    period = parameters.get('period', 60)
    statistic = parameters.get('statistic', 'Average')
    dimensions = parameters.get('dimensions', [])  # list of {Name, Value}

    if not creds:
        return {
            "action": "create_cloudwatch_alarm",
            "alarm_name": alarm_name,
            "metric_name": metric_name,
            "namespace": namespace,
            "mock": True,
        }

    from ._client import get_boto3_client
    cw = get_boto3_client(creds, 'cloudwatch')
    loop = asyncio.get_event_loop()

    def _call():
        cw.put_metric_alarm(
            AlarmName=alarm_name,
            MetricName=metric_name,
            Namespace=namespace,
            Threshold=float(threshold),
            ComparisonOperator=comparison,
            EvaluationPeriods=evaluation_periods,
            Period=period,
            Statistic=statistic,
            Dimensions=dimensions,
            TreatMissingData='notBreaching',
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "create_cloudwatch_alarm",
        "alarm_name": alarm_name,
        "metric_name": metric_name,
        "namespace": namespace,
        "threshold": threshold,
        "comparison_operator": comparison,
        "dimensions": dimensions,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_cloudwatch_alarm import execute as delete
    return await delete({"alarm_name": execution_result.get('alarm_name', parameters.get('alarm_name'))}, [], connector)
```

- [ ] **Step 2: Write `delete_cloudwatch_alarm.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    alarm_name = parameters.get('alarm_name', '')
    alarm_names = parameters.get('alarm_names', [alarm_name] if alarm_name else [])

    if not creds:
        return {"action": "delete_cloudwatch_alarm", "alarm_names": alarm_names, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    cw = get_boto3_client(creds, 'cloudwatch')
    loop = asyncio.get_event_loop()

    def _call():
        if alarm_names:
            cw.delete_alarms(AlarmNames=alarm_names)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_cloudwatch_alarm",
        "alarm_names": alarm_names,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Recreate from stored config if available in execution_result
    from app.connectors.executors.aws.create_cloudwatch_alarm import execute as create
    return await create(parameters, [], connector)
```

- [ ] **Step 3: Write `delete_ebs_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    snapshot_id = parameters.get('snapshot_id', '')

    if not creds:
        return {"action": "delete_ebs_snapshot", "snapshot_id": snapshot_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()

    def _call():
        ec2.delete_snapshot(SnapshotId=snapshot_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_ebs_snapshot",
        "snapshot_id": snapshot_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_ebs_snapshot is terminal — snapshot data cannot be recovered"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/aws/create_cloudwatch_alarm.py backend/app/connectors/executors/aws/delete_cloudwatch_alarm.py backend/app/connectors/executors/aws/delete_ebs_snapshot.py
git commit -m "feat: add CloudWatch alarm and EBS snapshot delete executors"
```

---

### Task 7: Add rollback to existing change types

**Files:**
- Modify: `backend/app/connectors/change_type_definitions/ec2_stop.json`
- Modify: `backend/app/connectors/change_type_definitions/ec2_start.json`
- Modify: `backend/app/connectors/change_type_definitions/snapshot_asset.json`

- [ ] **Step 1: Update `ec2_stop.json`**

Read the current file, then replace its content with:

```json
{
  "change_type": "ec2_stop",
  "display_name": "Stop EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_validate", "required": true},
    {"generic_action": "create_ebs_snapshot",     "purpose": "preflight_validate", "required": false},
    {"generic_action": "stop_instance",           "purpose": "execute",            "required": true},
    {"generic_action": "wait_instance_state",     "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "start_instance",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 2: Update `ec2_start.json`**

```json
{
  "change_type": "ec2_start",
  "display_name": "Start EC2 Instance",
  "steps": [
    {"generic_action": "start_instance",      "purpose": "execute", "required": true},
    {"generic_action": "wait_instance_state", "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "stop_instance",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 3: Update `snapshot_asset.json`** (add rollback to delete the snapshot)

Read the current file and add `"rollback_action": "delete_ebs_snapshot"` and `"rollback_connector_type": "aws"` fields.

```json
{
  "change_type": "snapshot_asset",
  "display_name": "Create Asset Snapshot",
  "steps": [
    {"generic_action": "create_snapshot", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_ebs_snapshot",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/change_type_definitions/ec2_stop.json backend/app/connectors/change_type_definitions/ec2_start.json backend/app/connectors/change_type_definitions/snapshot_asset.json
git commit -m "feat: add rollback_action to ec2_stop, ec2_start, snapshot_asset change types"
```

---

### Task 8: New change type JSON definitions

**Files:** 12 new JSON files in `backend/app/connectors/change_type_definitions/`

- [ ] **Step 1: Write IAM change types**

`iam_user_create.json`:
```json
{
  "change_type": "iam_user_create",
  "display_name": "Create IAM User",
  "steps": [
    {"generic_action": "create_iam_user", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_iam_user",
  "rollback_connector_type": "aws"
}
```

`iam_user_delete.json`:
```json
{
  "change_type": "iam_user_delete",
  "display_name": "Delete IAM User",
  "steps": [
    {"generic_action": "delete_iam_user", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": null
}
```

- [ ] **Step 2: Write S3 change types**

`s3_bucket_create.json`:
```json
{
  "change_type": "s3_bucket_create",
  "display_name": "Create S3 Bucket",
  "steps": [
    {"generic_action": "create_s3_bucket", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_s3_bucket",
  "rollback_connector_type": "aws"
}
```

`s3_bucket_delete.json`:
```json
{
  "change_type": "s3_bucket_delete",
  "display_name": "Delete S3 Bucket",
  "steps": [
    {"generic_action": "delete_s3_bucket", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": null
}
```

`s3_lifecycle_configure.json`:
```json
{
  "change_type": "s3_lifecycle_configure",
  "display_name": "Configure S3 Lifecycle Policy",
  "steps": [
    {"generic_action": "set_s3_lifecycle", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "set_s3_lifecycle",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 3: Write Route53 change types**

`route53_zone_create.json`:
```json
{
  "change_type": "route53_zone_create",
  "display_name": "Create Route53 Hosted Zone",
  "steps": [
    {"generic_action": "create_route53_zone", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_route53_zone",
  "rollback_connector_type": "aws"
}
```

`route53_record_upsert.json`:
```json
{
  "change_type": "route53_record_upsert",
  "display_name": "Create / Update DNS Record",
  "steps": [
    {"generic_action": "upsert_route53_record", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_route53_record",
  "rollback_connector_type": "aws"
}
```

`route53_record_delete.json`:
```json
{
  "change_type": "route53_record_delete",
  "display_name": "Delete DNS Record",
  "steps": [
    {"generic_action": "delete_route53_record", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "upsert_route53_record",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 4: Write RDS change types**

`rds_instance_create.json`:
```json
{
  "change_type": "rds_instance_create",
  "display_name": "Create RDS Instance",
  "steps": [
    {"generic_action": "create_rds_instance", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_rds_instance",
  "rollback_connector_type": "aws"
}
```

`rds_instance_delete.json`:
```json
{
  "change_type": "rds_instance_delete",
  "display_name": "Delete RDS Instance",
  "steps": [
    {"generic_action": "create_rds_snapshot",  "purpose": "preflight_validate", "required": true},
    {"generic_action": "delete_rds_instance",  "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": null
}
```

`rds_snapshot_create.json`:
```json
{
  "change_type": "rds_snapshot_create",
  "display_name": "Create RDS Snapshot",
  "steps": [
    {"generic_action": "create_rds_snapshot", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_rds_snapshot",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 5: Write CloudWatch change types**

`cloudwatch_alarm_create.json`:
```json
{
  "change_type": "cloudwatch_alarm_create",
  "display_name": "Create CloudWatch Alarm",
  "steps": [
    {"generic_action": "create_cloudwatch_alarm", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_cloudwatch_alarm",
  "rollback_connector_type": "aws"
}
```

`cloudwatch_alarm_delete.json`:
```json
{
  "change_type": "cloudwatch_alarm_delete",
  "display_name": "Delete CloudWatch Alarm",
  "steps": [
    {"generic_action": "delete_cloudwatch_alarm", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "create_cloudwatch_alarm",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 6: Commit all change type JSONs**

```bash
git add backend/app/connectors/change_type_definitions/iam_user_create.json backend/app/connectors/change_type_definitions/iam_user_delete.json backend/app/connectors/change_type_definitions/s3_bucket_create.json backend/app/connectors/change_type_definitions/s3_bucket_delete.json backend/app/connectors/change_type_definitions/s3_lifecycle_configure.json backend/app/connectors/change_type_definitions/route53_zone_create.json backend/app/connectors/change_type_definitions/route53_record_upsert.json backend/app/connectors/change_type_definitions/route53_record_delete.json backend/app/connectors/change_type_definitions/rds_instance_create.json backend/app/connectors/change_type_definitions/rds_instance_delete.json backend/app/connectors/change_type_definitions/rds_snapshot_create.json backend/app/connectors/change_type_definitions/cloudwatch_alarm_create.json backend/app/connectors/change_type_definitions/cloudwatch_alarm_delete.json
git commit -m "feat: add 12 new change type definitions for IAM, S3, Route53, RDS, CloudWatch"
```

---

### Task 9: AWS catalog entries

**Files:**
- Modify: `backend/app/connectors/catalog/aws.json`

- [ ] **Step 1: Add 15 catalog action entries**

Open `backend/app/connectors/catalog/aws.json`. In the `"actions"` array, append the following entries. Follow the exact format of existing entries (see `create_key_pair` as reference — it has `action_id`, `generic_action`, `action_type`, `execution_tier`, `display_name`, `description`, `applicable_asset_types`, `parameters`, `executor`, `rollback_action`, `rollback_connector_type`, `estimated_duration_seconds`):

```json
{
  "action_id": "create_iam_user",
  "generic_action": "create_iam_user",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Create IAM User",
  "description": "Create an IAM user with programmatic access.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "username", "type": "string", "required": true}
  ],
  "executor": "aws.create_iam_user",
  "rollback_action": "delete_iam_user",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 5
},
{
  "action_id": "delete_iam_user",
  "generic_action": "delete_iam_user",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete IAM User",
  "description": "Delete an IAM user and all associated access keys and policies.",
  "applicable_asset_types": ["identity"],
  "parameters": [
    {"name": "username", "type": "string", "required": true}
  ],
  "executor": "aws.delete_iam_user",
  "rollback_action": null,
  "estimated_duration_seconds": 5
},
{
  "action_id": "create_s3_bucket",
  "generic_action": "create_s3_bucket",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Create S3 Bucket",
  "description": "Create a tagged S3 bucket.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true}
  ],
  "executor": "aws.create_s3_bucket",
  "rollback_action": "delete_s3_bucket",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 5
},
{
  "action_id": "delete_s3_bucket",
  "generic_action": "delete_s3_bucket",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete S3 Bucket",
  "description": "Empty and delete an S3 bucket.",
  "applicable_asset_types": ["storage_bucket"],
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true}
  ],
  "executor": "aws.delete_s3_bucket",
  "rollback_action": null,
  "estimated_duration_seconds": 30
},
{
  "action_id": "set_s3_lifecycle",
  "generic_action": "set_s3_lifecycle",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Set S3 Lifecycle Policy",
  "description": "Configure lifecycle expiration/transition rules on an S3 bucket.",
  "applicable_asset_types": ["storage_bucket"],
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true},
    {"name": "rules", "type": "array", "required": true}
  ],
  "executor": "aws.set_s3_lifecycle",
  "rollback_action": "set_s3_lifecycle",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 5
},
{
  "action_id": "create_route53_zone",
  "generic_action": "create_route53_zone",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Create Route53 Hosted Zone",
  "description": "Create a private or public Route53 hosted zone.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "zone_name", "type": "string", "required": true},
    {"name": "private", "type": "boolean", "required": false}
  ],
  "executor": "aws.create_route53_zone",
  "rollback_action": "delete_route53_zone",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 10
},
{
  "action_id": "delete_route53_zone",
  "generic_action": "delete_route53_zone",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete Route53 Hosted Zone",
  "description": "Delete all records and the hosted zone.",
  "applicable_asset_types": ["dns_zone"],
  "parameters": [
    {"name": "zone_id", "type": "string", "required": true}
  ],
  "executor": "aws.delete_route53_zone",
  "rollback_action": null,
  "estimated_duration_seconds": 10
},
{
  "action_id": "upsert_route53_record",
  "generic_action": "upsert_route53_record",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Create / Update DNS Record",
  "description": "Upsert an A, CNAME, or weighted record in a Route53 hosted zone.",
  "applicable_asset_types": ["dns_zone"],
  "parameters": [
    {"name": "zone_id",      "type": "string",  "required": true},
    {"name": "name",         "type": "string",  "required": true},
    {"name": "record_type",  "type": "string",  "required": true},
    {"name": "values",       "type": "array",   "required": true},
    {"name": "ttl",          "type": "integer", "required": false},
    {"name": "weight",       "type": "integer", "required": false},
    {"name": "set_identifier","type": "string", "required": false}
  ],
  "executor": "aws.upsert_route53_record",
  "rollback_action": "delete_route53_record",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 10
},
{
  "action_id": "delete_route53_record",
  "generic_action": "delete_route53_record",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete DNS Record",
  "description": "Delete a specific record set from a Route53 hosted zone.",
  "applicable_asset_types": ["dns_zone"],
  "parameters": [
    {"name": "zone_id",     "type": "string",  "required": true},
    {"name": "name",        "type": "string",  "required": true},
    {"name": "record_type", "type": "string",  "required": true},
    {"name": "values",      "type": "array",   "required": true},
    {"name": "ttl",         "type": "integer", "required": false}
  ],
  "executor": "aws.delete_route53_record",
  "rollback_action": "upsert_route53_record",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 10
},
{
  "action_id": "create_rds_instance",
  "generic_action": "create_rds_instance",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Create RDS Instance",
  "description": "Launch a new RDS database instance and wait for it to become available.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "db_instance_identifier", "type": "string", "required": true},
    {"name": "engine",                 "type": "string", "required": false},
    {"name": "db_instance_class",      "type": "string", "required": false},
    {"name": "master_username",        "type": "string", "required": false},
    {"name": "master_password",        "type": "string", "required": true},
    {"name": "allocated_storage",      "type": "integer","required": false}
  ],
  "executor": "aws.create_rds_instance",
  "rollback_action": "delete_rds_instance",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 900
},
{
  "action_id": "delete_rds_instance",
  "generic_action": "delete_rds_instance",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete RDS Instance",
  "description": "Delete an RDS instance. Skips final snapshot. Irreversible.",
  "applicable_asset_types": ["database"],
  "parameters": [
    {"name": "db_instance_identifier", "type": "string", "required": true}
  ],
  "executor": "aws.delete_rds_instance",
  "rollback_action": null,
  "estimated_duration_seconds": 900
},
{
  "action_id": "delete_rds_snapshot",
  "generic_action": "delete_rds_snapshot",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete RDS Snapshot",
  "description": "Delete a manual RDS snapshot.",
  "applicable_asset_types": ["database", "cloud_account"],
  "parameters": [
    {"name": "snapshot_identifier", "type": "string", "required": true}
  ],
  "executor": "aws.delete_rds_snapshot",
  "rollback_action": null,
  "estimated_duration_seconds": 10
},
{
  "action_id": "create_cloudwatch_alarm",
  "generic_action": "create_cloudwatch_alarm",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Create CloudWatch Alarm",
  "description": "Create a metric alarm in CloudWatch.",
  "applicable_asset_types": ["server", "database", "cloud_account"],
  "parameters": [
    {"name": "alarm_name",          "type": "string",  "required": true},
    {"name": "metric_name",         "type": "string",  "required": true},
    {"name": "namespace",           "type": "string",  "required": true},
    {"name": "threshold",           "type": "number",  "required": true},
    {"name": "comparison_operator", "type": "string",  "required": false},
    {"name": "evaluation_periods",  "type": "integer", "required": false},
    {"name": "dimensions",          "type": "array",   "required": false}
  ],
  "executor": "aws.create_cloudwatch_alarm",
  "rollback_action": "delete_cloudwatch_alarm",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 5
},
{
  "action_id": "delete_cloudwatch_alarm",
  "generic_action": "delete_cloudwatch_alarm",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete CloudWatch Alarm",
  "description": "Delete one or more CloudWatch alarms.",
  "applicable_asset_types": ["server", "database", "cloud_account"],
  "parameters": [
    {"name": "alarm_name",  "type": "string", "required": false},
    {"name": "alarm_names", "type": "array",  "required": false}
  ],
  "executor": "aws.delete_cloudwatch_alarm",
  "rollback_action": "create_cloudwatch_alarm",
  "rollback_connector_type": "aws",
  "estimated_duration_seconds": 5
},
{
  "action_id": "delete_ebs_snapshot",
  "generic_action": "delete_ebs_snapshot",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Delete EBS Snapshot",
  "description": "Delete an EBS snapshot.",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "snapshot_id", "type": "string", "required": true}
  ],
  "executor": "aws.delete_ebs_snapshot",
  "rollback_action": null,
  "estimated_duration_seconds": 10
}
```

- [ ] **Step 2: Verify JSON is valid**

```bash
python3 -c "import json; json.load(open('backend/app/connectors/catalog/aws.json')); print('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/aws.json
git commit -m "feat: add 15 new action entries to AWS catalog"
```

---

### Task 10: Smoke test mock verification

Run the existing backend tests to confirm nothing is broken by the new enum values and migration:

- [ ] **Step 1: Run backend tests**

```bash
docker exec nexplane-backend-1 pytest backend/tests/ -x -q 2>&1 | tail -20
```

Expected: all tests pass (or same count as before this plan — no new failures)

- [ ] **Step 2: Verify mock executors work**

```bash
docker exec nexplane-backend-1 python3 -c "
import asyncio, sys
sys.path.insert(0, '/app')

async def test_all_mocks():
    from app.connectors.executors.aws.create_iam_user import execute as f1
    from app.connectors.executors.aws.delete_iam_user import execute as f2
    from app.connectors.executors.aws.create_s3_bucket import execute as f3
    from app.connectors.executors.aws.delete_s3_bucket import execute as f4
    from app.connectors.executors.aws.set_s3_lifecycle import execute as f5
    from app.connectors.executors.aws.create_route53_zone import execute as f6
    from app.connectors.executors.aws.delete_route53_zone import execute as f7
    from app.connectors.executors.aws.upsert_route53_record import execute as f8
    from app.connectors.executors.aws.delete_route53_record import execute as f9
    from app.connectors.executors.aws.create_rds_instance import execute as f10
    from app.connectors.executors.aws.delete_rds_instance import execute as f11
    from app.connectors.executors.aws.delete_rds_snapshot import execute as f12
    from app.connectors.executors.aws.create_cloudwatch_alarm import execute as f13
    from app.connectors.executors.aws.delete_cloudwatch_alarm import execute as f14
    from app.connectors.executors.aws.delete_ebs_snapshot import execute as f15

    results = await asyncio.gather(
        f1({'username': 'test-user'}, [], None),
        f2({'username': 'test-user'}, [], None),
        f3({'bucket_name': 'test-bucket'}, [], None),
        f4({'bucket_name': 'test-bucket'}, [], None),
        f5({'bucket_name': 'test-bucket', 'rules': []}, [], None),
        f6({'zone_name': 'test.internal'}, [], None),
        f7({'zone_id': 'ZTEST'}, [], None),
        f8({'zone_id': 'ZTEST', 'name': 'web.test.internal', 'record_type': 'A', 'values': ['10.0.0.1']}, [], None),
        f9({'zone_id': 'ZTEST', 'name': 'web.test.internal', 'record_type': 'A', 'values': ['10.0.0.1']}, [], None),
        f10({'db_instance_identifier': 'test-db', 'master_password': 'Test!1234'}, [], None),
        f11({'db_instance_identifier': 'test-db'}, [], None),
        f12({'snapshot_identifier': 'snap-test'}, [], None),
        f13({'alarm_name': 'test-alarm', 'metric_name': 'CPUUtilization', 'namespace': 'AWS/EC2', 'threshold': 80}, [], None),
        f14({'alarm_name': 'test-alarm'}, [], None),
        f15({'snapshot_id': 'snap-test'}, [], None),
    )
    for r in results:
        assert r.get('mock') == True or r.get('deleted') == True or r.get('deleted') is not None, f'Unexpected result: {r}'
    print('All 15 mock executors: OK')

asyncio.run(test_all_mocks())
"
```

Expected: `All 15 mock executors: OK`

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "test: verify all new AWS executor mocks pass"
```

---

### Task 11: Route53 discovery executor

The `dns_zone` asset type already exists in the backend enum. This task adds the ingest executor so existing Route53 zones appear in inventory.

**Files:**
- Create: `backend/app/connectors/executors/aws/discover_route53_zones.py`
- Modify: `backend/app/connectors/catalog/aws.json`

- [ ] **Step 1: Write `discover_route53_zones.py`**

```python
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})

    if not creds:
        return {
            "assets": [
                {
                    "name": "mock.nexplane.internal",
                    "asset_type": "dns_zone",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "zone_id": "Z_MOCK01",
                        "zone_name": "mock.nexplane.internal.",
                        "private_zone": True,
                        "record_count": 2,
                        "provider": "aws",
                    },
                    "tags": ["route53"],
                    "mock": True,
                }
            ]
        }

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        assets = []
        paginator = r53.get_paginator('list_hosted_zones')
        for page in paginator.paginate():
            for zone in page['HostedZones']:
                zone_id = zone['Id'].split('/')[-1]
                count = zone.get('Config', {}).get('Comment', '')
                assets.append({
                    "name": zone['Name'].rstrip('.'),
                    "asset_type": "dns_zone",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "zone_id": zone_id,
                        "zone_name": zone['Name'],
                        "private_zone": zone['Config']['PrivateZone'],
                        "record_count": zone.get('ResourceRecordSetCount', 0),
                        "region": creds.get('region', 'us-east-1'),
                        "provider": "aws",
                    },
                    "tags": ["route53", "private" if zone['Config']['PrivateZone'] else "public"],
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"assets": assets}
```

- [ ] **Step 2: Add catalog entry for `discover_route53_zones`**

In `backend/app/connectors/catalog/aws.json`, in the `"actions"` array, add:

```json
{
  "action_id": "discover_route53_zones",
  "generic_action": "discover_route53_zones",
  "action_type": "discover",
  "execution_tier": 1,
  "display_name": "Discover Route53 Hosted Zones",
  "description": "List all Route53 hosted zones and create dns_zone assets.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "executor": "aws.discover_route53_zones",
  "rollback_action": null,
  "estimated_duration_seconds": 10
}
```

- [ ] **Step 3: Verify JSON valid**

```bash
python3 -c "import json; json.load(open('backend/app/connectors/catalog/aws.json')); print('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/aws/discover_route53_zones.py backend/app/connectors/catalog/aws.json
git commit -m "feat: add discover_route53_zones executor for dns_zone asset ingest"
```

---

**Plan 1 complete.** Proceed to Plan 2 (Smoke Test Phases E–H).
