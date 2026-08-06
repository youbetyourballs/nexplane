# DNSSEC Signing Workflow — Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-08-05-dnssec-signing-workflow-design.md`
**Date:** 2026-08-05
**Estimated tasks:** 3 (executor, wiring, smoke)

---

## Pre-flight notes

- KMS key **must** be in `us-east-1` regardless of the connector's configured region — Route53 DNSSEC requires us-east-1 KMS.
- The `_client.py` helper in `backend/app/connectors/executors/aws/` provides `get_boto3_client(creds, service, region)`. Always use it (not raw boto3) so credential/session logic is centralised.
- Migration revision ID pattern: `YYYYMMDD_NNN`. The current head is `20260803_001`. New revision: `20260805_001`, `down_revision = '20260803_001'`.
- ChangeType enum values are added near Route53 siblings (around line 45–50 in `change_request.py`).
- Rollback signature for this executor style is `async def rollback(parameters, execution_result, connector)` — note the arg order matches `dns_zone_migrate.py`, not `aws_account_baseline_monitoring.py` (which uses keyword-only `execution_result`). Use the `dns_zone_migrate` pattern.

---

## Task 1 — Executor: `route53_dnssec.py`

**File to create:** `backend/app/connectors/executors/aws/route53_dnssec.py`

### Step 1.1 — Write the executor

Create the file with the content below. No other files are touched in this task.

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Route53 DNSSEC enable/disable executor.

Enables DNSSEC signing on an existing Route53 hosted zone using a
KMS-backed Key Signing Key (KSK). Polls until the zone reaches SIGNING
status and surfaces DS record values for registrar submission.

KMS key MUST be in us-east-1 (Route53 DNSSEC requirement).
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Route53 DNSSEC requires the KMS key to be in us-east-1 unconditionally.
_KMS_REGION = "us-east-1"

# Required KMS key spec for Route53 DNSSEC
_KEY_SPEC = "ECC_NIST_P256"  # used for ECDSAP256SHA256
_KEY_USAGE = "SIGN_VERIFY"


def _r53(connector):
    from app.connectors.executors.aws._client import get_boto3_client
    creds = getattr(connector, "credentials", {})
    # Route53 is a global service; us-east-1 is the canonical endpoint.
    return get_boto3_client(creds, "route53", "us-east-1")


def _kms(connector):
    from app.connectors.executors.aws._client import get_boto3_client
    creds = getattr(connector, "credentials", {})
    return get_boto3_client(creds, "kms", _KMS_REGION)


async def _run(fn):
    return await asyncio.get_event_loop().run_in_executor(None, fn)


# ── Preflight ─────────────────────────────────────────────────────────────────

async def _preflight(r53_client, zone_id: str) -> dict:
    """Verify zone exists and return zone name + current DNSSEC status."""
    def _do():
        resp = r53_client.get_hosted_zone(Id=zone_id)
        zone_name = resp["HostedZone"]["Name"]
        dnssec_resp = r53_client.get_dnssec(HostedZoneId=zone_id)
        status = dnssec_resp.get("Status", {}).get("ServeSignature", "NOT_SIGNING")
        ksks = dnssec_resp.get("KeySigningKeys", [])
        return zone_name, status, ksks
    zone_name, status, ksks = await _run(_do)
    return {"zone_name": zone_name, "current_status": status, "existing_ksks": ksks}


# ── KMS key resolution ────────────────────────────────────────────────────────

def _build_route53_key_policy(account_id: str) -> str:
    """Return the KMS key policy JSON that grants Route53 DNSSEC usage."""
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Enable IAM User Permissions",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                "Action": "kms:*",
                "Resource": "*",
            },
            {
                "Sid": "Allow Route53 DNSSEC Service",
                "Effect": "Allow",
                "Principal": {"Service": "dnssec-route53.amazonaws.com"},
                "Action": [
                    "kms:DescribeKey",
                    "kms:GetPublicKey",
                    "kms:Sign",
                ],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": account_id,
                    }
                },
            },
            {
                "Sid": "Allow Route53 DNSSEC to CreateGrant",
                "Effect": "Allow",
                "Principal": {"Service": "dnssec-route53.amazonaws.com"},
                "Action": "kms:CreateGrant",
                "Resource": "*",
                "Condition": {
                    "Bool": {"kms:GrantIsForAWSResource": True}
                },
            },
        ],
    }
    return json.dumps(policy)


async def _get_account_id(connector) -> str:
    from app.connectors.executors.aws._client import get_boto3_client
    creds = getattr(connector, "credentials", {})
    def _do():
        sts = get_boto3_client(creds, "sts", "us-east-1")
        return sts.get_caller_identity()["Account"]
    return await _run(_do)


async def _resolve_kms_key(kms_client, kms_key_id: str, zone_id: str, account_id: str) -> tuple[str, bool]:
    """Return (kms_key_arn, created_by_nexplane).

    If kms_key_id == 'auto':
      1. Search for existing key tagged nexplane-dnssec-<zone_id_safe>.
      2. If not found, create a new ECC_NIST_P256 key with the Route53 policy.
    Otherwise validate the provided ARN is accessible.
    """
    zone_id_safe = zone_id.replace("/hostedzone/", "").strip("/")
    tag_key = "nexplane-dnssec"
    tag_value = zone_id_safe

    if kms_key_id != "auto":
        # Validate the key is accessible
        def _check():
            resp = kms_client.describe_key(KeyId=kms_key_id)
            meta = resp["KeyMetadata"]
            if meta["KeyState"] != "Enabled":
                raise ValueError(f"KMS key {kms_key_id} is not in Enabled state (state={meta['KeyState']})")
            # Ensure policy includes Route53 permission; patch if missing
            pol_resp = kms_client.get_key_policy(KeyId=kms_key_id, PolicyName="default")
            pol = json.loads(pol_resp["Policy"])
            sids = [s.get("Sid", "") for s in pol.get("Statement", [])]
            if "Allow Route53 DNSSEC Service" not in sids:
                new_pol = json.loads(_build_route53_key_policy(account_id))
                existing_statements = pol.get("Statement", [])
                # Merge — add the Route53 statements
                for stmt in new_pol["Statement"]:
                    if stmt["Sid"] not in sids:
                        existing_statements.append(stmt)
                pol["Statement"] = existing_statements
                kms_client.put_key_policy(
                    KeyId=kms_key_id,
                    PolicyName="default",
                    Policy=json.dumps(pol),
                )
            return meta["Arn"]
        arn = await _run(_check)
        return arn, False

    # Auto mode: find or create
    def _find_existing():
        paginator = kms_client.get_paginator("list_keys")
        for page in paginator.paginate():
            for key_entry in page["Keys"]:
                kid = key_entry["KeyId"]
                try:
                    tags_resp = kms_client.list_resource_tags(KeyId=kid)
                    tags = {t["TagKey"]: t["TagValue"] for t in tags_resp.get("Tags", [])}
                    if tags.get(tag_key) == tag_value:
                        meta = kms_client.describe_key(KeyId=kid)["KeyMetadata"]
                        if meta["KeyState"] == "Enabled":
                            return meta["Arn"]
                except Exception:
                    pass
        return None

    existing_arn = await _run(_find_existing)
    if existing_arn:
        logger.info("Reusing existing KMS key %s for zone %s", existing_arn, zone_id_safe)
        return existing_arn, False

    # Create new key
    def _create_key():
        policy = _build_route53_key_policy(account_id)
        resp = kms_client.create_key(
            Description=f"Nexplane DNSSEC signing key for Route53 zone {zone_id_safe}",
            KeyUsage=_KEY_USAGE,
            KeySpec=_KEY_SPEC,
            Policy=policy,
            Tags=[
                {"TagKey": tag_key, "TagValue": tag_value},
                {"TagKey": "ManagedBy", "TagValue": "nexplane"},
            ],
        )
        return resp["KeyMetadata"]["Arn"]

    new_arn = await _run(_create_key)
    logger.info("Created new KMS key %s for zone %s", new_arn, zone_id_safe)
    return new_arn, True


# ── Enable DNSSEC ─────────────────────────────────────────────────────────────

async def _enable_dnssec(r53_client, zone_id: str, kms_key_arn: str, ksk_name: str) -> None:
    """Create KSK and enable DNSSEC on the hosted zone."""
    def _do():
        import uuid as _uuid
        caller_ref = f"nexplane-{_uuid.uuid4().hex[:8]}"
        r53_client.create_key_signing_key(
            CallerReference=caller_ref,
            HostedZoneId=zone_id,
            KeyManagementServiceArn=kms_key_arn,
            Name=ksk_name,
            Status="ACTIVE",
        )
        r53_client.enable_hosted_zone_dnssec(HostedZoneId=zone_id)
    await _run(_do)


# ── Poll until SIGNING ────────────────────────────────────────────────────────

async def _poll_signing(r53_client, zone_id: str, timeout_s: int = 300, interval_s: int = 10) -> dict:
    """Poll get_dnssec until ServeSignature == SIGNING or FAILED."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        def _check():
            return r53_client.get_dnssec(HostedZoneId=zone_id)
        resp = await _run(_check)
        sig_status = resp.get("Status", {}).get("ServeSignature", "")
        if sig_status == "SIGNING":
            return resp
        if sig_status == "FAILED":
            msg = resp.get("Status", {}).get("StatusMessage", "unknown")
            raise RuntimeError(f"DNSSEC signing FAILED: {msg}")
        logger.info("DNSSEC status for %s: %s — waiting %ds", zone_id, sig_status, interval_s)
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"Zone {zone_id} did not reach SIGNING within {timeout_s}s")


# ── Extract DS record ─────────────────────────────────────────────────────────

def _extract_ds_record(dnssec_resp: dict, ksk_name: str) -> dict:
    """Pull DS record fields from a get_dnssec response."""
    ksks = dnssec_resp.get("KeySigningKeys", [])
    # Prefer the named KSK; fall back to first active one
    ksk = next((k for k in ksks if k.get("Name") == ksk_name), None)
    if not ksk:
        ksk = next((k for k in ksks if k.get("Status") == "ACTIVE"), None)
    if not ksk:
        raise ValueError("No active KSK found in get_dnssec response")
    return {
        "ds_record": ksk.get("DSRecord", ""),
        "key_tag": ksk.get("KeyTag"),
        "digest_algorithm": ksk.get("DigestAlgorithmMnemonic", "SHA-256"),
        "digest_algorithm_type": ksk.get("DigestAlgorithmType", 2),
        "digest_value": ksk.get("DigestValue", ""),
        "signing_algorithm_mnemonic": ksk.get("SigningAlgorithmMnemonic", "ECDSAP256SHA256"),
        "signing_algorithm_type": ksk.get("SigningAlgorithmType", 13),
    }


# ── Main execute ──────────────────────────────────────────────────────────────

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    zone_id = parameters["zone_id"]
    kms_key_id = parameters.get("kms_key_id", "auto")
    ksk_name = parameters.get("ksk_name", "nexplane-ksk")
    dry_run = parameters.get("dry_run", False)

    r53 = _r53(connector)
    kms = _kms(connector)

    # Phase 1: Preflight
    preflight = await _preflight(r53, zone_id)
    zone_name = preflight["zone_name"]
    current_status = preflight["current_status"]

    if current_status == "SIGNING":
        return {
            "status": "already_enabled",
            "zone_id": zone_id,
            "zone_name": zone_name,
            "message": "DNSSEC is already in SIGNING state — no changes made.",
        }

    if dry_run:
        return {
            "status": "dry_run",
            "zone_id": zone_id,
            "zone_name": zone_name,
            "current_dnssec_status": current_status,
            "kms_key_id_requested": kms_key_id,
            "ksk_name": ksk_name,
            "message": "Dry run — no changes applied.",
        }

    # Phase 2: Resolve KMS key
    account_id = await _get_account_id(connector)
    kms_key_arn, created_by_nexplane = await _resolve_kms_key(kms, kms_key_id, zone_id, account_id)

    # Phase 3: Enable DNSSEC
    await _enable_dnssec(r53, zone_id, kms_key_arn, ksk_name)

    # Phase 4: Poll until SIGNING
    dnssec_resp = await _poll_signing(r53, zone_id)

    # Phase 5: Extract DS record
    ds_info = _extract_ds_record(dnssec_resp, ksk_name)
    key_tag = ds_info["key_tag"]
    digest_value = ds_info["digest_value"]
    digest_alg_type = ds_info["digest_algorithm_type"]
    signing_alg_type = ds_info["signing_algorithm_type"]

    registrar_instructions = (
        f"Add the following DS record at your domain registrar for {zone_name.rstrip('.')}:\n"
        f"  Key Tag: {key_tag}\n"
        f"  Algorithm: {signing_alg_type} ({ds_info['signing_algorithm_mnemonic']})\n"
        f"  Digest Type: {digest_alg_type} ({ds_info['digest_algorithm']})\n"
        f"  Digest: {digest_value}\n"
        "This record delegates DNSSEC trust from the parent zone to this zone. "
        "Submit it to your registrar to complete the chain of trust."
    )

    return {
        "status": "signing",
        "zone_id": zone_id,
        "zone_name": zone_name,
        "ksk_name": ksk_name,
        "kms_key_arn": kms_key_arn,
        "kms_key_created_by_nexplane": created_by_nexplane,
        "ds_record": ds_info["ds_record"],
        "key_tag": key_tag,
        "digest_algorithm": ds_info["digest_algorithm"],
        "digest_algorithm_type": digest_alg_type,
        "digest_value": digest_value,
        "signing_algorithm_mnemonic": ds_info["signing_algorithm_mnemonic"],
        "registrar_instructions": registrar_instructions,
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Rollback ──────────────────────────────────────────────────────────────────

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Disable DNSSEC, deactivate+delete KSK, schedule KMS key deletion if we created it."""
    zone_id = parameters["zone_id"]
    ksk_name = execution_result.get("ksk_name", parameters.get("ksk_name", "nexplane-ksk"))
    kms_key_arn = execution_result.get("kms_key_arn")
    created_key = execution_result.get("kms_key_created_by_nexplane", False)

    r53 = _r53(connector)
    kms = _kms(connector)

    phases = []

    # Step 1: Disable DNSSEC signing on the zone
    def _disable_dnssec():
        r53.disable_hosted_zone_dnssec(HostedZoneId=zone_id)
    try:
        await _run(_disable_dnssec)
        phases.append({"step": "disable_dnssec", "status": "ok"})
    except Exception as e:
        logger.warning("disable_hosted_zone_dnssec failed (may already be disabled): %s", e)
        phases.append({"step": "disable_dnssec", "status": "warning", "message": str(e)})

    # Step 2: Deactivate KSK (required before deletion)
    def _deactivate_ksk():
        r53.update_key_signing_key(HostedZoneId=zone_id, Name=ksk_name, Status="INACTIVE")
    try:
        await _run(_deactivate_ksk)
        phases.append({"step": "deactivate_ksk", "status": "ok"})
    except Exception as e:
        logger.warning("deactivate KSK failed: %s", e)
        phases.append({"step": "deactivate_ksk", "status": "warning", "message": str(e)})

    # Step 3: Delete KSK
    def _delete_ksk():
        r53.delete_key_signing_key(HostedZoneId=zone_id, Name=ksk_name)
    try:
        await _run(_delete_ksk)
        phases.append({"step": "delete_ksk", "status": "ok"})
    except Exception as e:
        logger.warning("delete KSK failed: %s", e)
        phases.append({"step": "delete_ksk", "status": "warning", "message": str(e)})

    # Step 4: If we created the KMS key, schedule deletion (7-day minimum)
    kms_deletion_scheduled = False
    if created_key and kms_key_arn:
        def _schedule_deletion():
            kms.schedule_key_deletion(KeyId=kms_key_arn, PendingWindowInDays=7)
        try:
            await _run(_schedule_deletion)
            kms_deletion_scheduled = True
            phases.append({"step": "schedule_kms_key_deletion", "status": "ok",
                           "note": "KMS key enters 7-day pending deletion (minimum allowed by AWS)"})
        except Exception as e:
            logger.warning("schedule_key_deletion failed: %s", e)
            phases.append({"step": "schedule_kms_key_deletion", "status": "warning", "message": str(e)})

    return {
        "rolled_back": True,
        "zone_id": zone_id,
        "ksk_name": ksk_name,
        "kms_key_arn": kms_key_arn,
        "kms_key_deletion_scheduled": kms_deletion_scheduled,
        "phases": phases,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
```

### Step 1.2 — Commit

```
git add backend/app/connectors/executors/aws/route53_dnssec.py
git commit -m "feat: add route53_dnssec executor (enable + rollback)"
```

**Expected output:** `[master ...] feat: add route53_dnssec executor (enable + rollback)`

---

## Task 2 — Wiring: ChangeType enum + migration + change_type_definition + catalog

### Step 2.1 — Add ChangeType enum value

**File:** `backend/app/models/change_request.py`

Find the Route53 block (around line 45):
```python
    route53_zone_create = "route53_zone_create"
    route53_record_upsert = "route53_record_upsert"
    route53_record_delete = "route53_record_delete"
```

Add immediately after `route53_record_delete`:
```python
    route53_dnssec_enable = "route53_dnssec_enable"
```

### Step 2.2 — Create the Alembic migration

**File to create:** `backend/alembic/versions/20260805_001_add_route53_dnssec_enable_change_type.py`

```python
"""add_route53_dnssec_enable_change_type

Revision ID: 20260805_001
Revises: 20260803_001
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260805_001'
down_revision: Union[str, None] = '20260803_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'route53_dnssec_enable'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
```

### Step 2.3 — Create change_type_definition JSON

**File to create:** `backend/app/connectors/change_type_definitions/route53_dnssec_enable.json`

```json
{
  "change_type": "route53_dnssec_enable",
  "display_name": "Route53 DNSSEC Enable",
  "steps": [
    {
      "generic_action": "route53_dnssec_enable",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "route53_dnssec_enable",
  "rollback_connector_type": "aws"
}
```

### Step 2.4 — Add catalog entry to `aws.json`

**File:** `backend/app/connectors/catalog/aws.json`

Locate the `"catalog_actions"` array. Add the following entry in the Route53 section (after any existing `route53_record_delete` entry, or at an appropriate Route53 grouping):

```json
{
  "action_id": "route53_dnssec_enable",
  "generic_action": "route53_dnssec_enable",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Enable DNSSEC on Route53 Zone",
  "description": "Enables DNSSEC signing on a Route53 hosted zone using a KMS-backed Key Signing Key. Surfaces DS record values for registrar submission.",
  "applicable_asset_types": ["cloud_account", "dns_zone"],
  "parameters": [
    {"name": "zone_id", "type": "string", "required": true, "description": "Route53 hosted zone ID e.g. Z1234567890ABC"},
    {"name": "kms_key_id", "type": "string", "required": false, "default": "auto", "description": "ARN of existing KMS key in us-east-1, or 'auto' to create one"},
    {"name": "key_signing_algorithm", "type": "string", "required": false, "default": "ECDSAP256SHA256", "description": "ECDSAP256SHA256 | ECDSAP384SHA384 | RSA_2048_SHA256"},
    {"name": "ksk_name", "type": "string", "required": false, "default": "nexplane-ksk", "description": "Name for the Key Signing Key"},
    {"name": "dry_run", "type": "boolean", "required": false, "default": false}
  ],
  "executor": "aws.route53_dnssec",
  "rollback_strategy": "executor",
  "estimated_duration_seconds": 120,
  "safety_notes": [
    "Requires KMS key in us-east-1",
    "DS record must be submitted to registrar manually after execution",
    "KMS key enters 7-day pending deletion on rollback (minimum allowed by AWS)"
  ],
  "smoke_verified": false
}
```

**How to find the insertion point in `aws.json`:** Read the file, search for `"route53_record_delete"` action_id, and add the new entry after the closing `}` of that object (before the next entry or before the closing `]` of the actions array).

### Step 2.5 — Register the executor in the dispatcher

Search the codebase for where AWS executor modules are registered/dispatched. Common patterns:

```bash
grep -r "dns_zone_migrate" backend/app/connectors/ --include="*.py" -l
grep -r "route53_zone_create\|action_id.*route53" backend/app/connectors/ --include="*.py" -l
```

Find the registry file (likely `backend/app/connectors/executor_registry.py` or similar) and add:

```python
"route53_dnssec_enable": "app.connectors.executors.aws.route53_dnssec",
```

following the same pattern as the existing Route53 executor registrations. If executors are loaded by convention from the `generic_action` name mapped to a module path, verify the `executor` field `"aws.route53_dnssec"` resolves correctly using the existing dispatch logic.

### Step 2.6 — Commit

```
git add backend/app/models/change_request.py \
        backend/alembic/versions/20260805_001_add_route53_dnssec_enable_change_type.py \
        backend/app/connectors/change_type_definitions/route53_dnssec_enable.json \
        backend/app/connectors/catalog/aws.json
# (also add the registry file if changed)
git commit -m "feat: wire route53_dnssec_enable CR type — enum, migration, definition, catalog"
```

**Expected output:** `[master ...] feat: wire route53_dnssec_enable CR type — enum, migration, definition, catalog`

### Step 2.7 — Apply migration on EC2

SSH to EC2 (or use platform API / SSM runner):

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
cd /home/ec2-user/nexplane
git pull
docker exec nexplane-backend-1 alembic upgrade head
```

**Expected output:**
```
INFO  [alembic.runtime.migration] Running upgrade 20260803_001 -> 20260805_001, add_route53_dnssec_enable_change_type
```

---

## Task 3 — Smoke test: `test_smoke_route53_dnssec.py`

**File to create:** `backend/tests/smoke/test_smoke_route53_dnssec.py`

### Step 3.1 — Write the smoke test

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Route53 DNSSEC Enable (route53_dnssec_enable)

Creates a fresh public Route53 hosted zone, enables DNSSEC via the CR
lifecycle, verifies SIGNING status, rolls back, and deletes the zone.

Note: The KMS key created by the executor enters 7-day pending deletion
on rollback — it cannot be immediately deleted. This is the AWS minimum.

Run:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_route53_dnssec.py -v -s

Must be run from EC2 runner on Tailscale, not local Docker.
"""

import asyncio
import hashlib
import os
import time

import boto3
import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_STATE: dict = {}


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set")
    return val


async def _get_aws_creds() -> tuple[dict, str]:
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Connector).where(Connector.connector_type == "aws"))
        connector = r.scalars().first()
        if not connector:
            pytest.skip("No AWS connector registered")
        cr = await db.execute(
            select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
        )
        cc = cr.scalars().first()
        backend = get_secret_backend()
        return backend.decrypt_json(cc.credentials_encrypted), str(connector.id)


def _r53_client(creds: dict):
    return boto3.client(
        "route53",
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name="us-east-1",
    )


async def _get_jwt(api_token: str) -> str:
    from app.database import AsyncSessionLocal
    from app.models.api_token import ApiToken
    from app.services.auth_service import create_access_token
    from sqlalchemy import select

    token_hash = hashlib.sha256(api_token.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked == False,  # noqa: E712
            )
        )
        tok = r.scalar_one()
        return create_access_token(subject=str(tok.user_id))


async def _plan_and_approve(token: str, cr_id: str) -> None:
    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "dnssec smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"approve failed: {r.text}"


async def _poll_cr(token: str, cr_id: str, terminal: set, timeout: int = 360) -> dict:
    interval = 10
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        jwt = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200
            detail = r.json()
        status = detail.get("status")
        print(f"  [poll] CR {cr_id} status={status}")
        if status in terminal:
            return detail
    pytest.fail(f"CR {cr_id} did not reach {terminal} in {timeout}s")


def _extract_exec_result(detail: dict) -> dict:
    runs = detail.get("execution_runs", [])
    for run in reversed(runs):
        steps = run.get("result", {}).get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result", {})
    return detail.get("execution_result", {})


def _delete_zone_safely(r53, zone_id: str) -> None:
    """Delete all non-NS/SOA records then delete the zone."""
    try:
        paginator = r53.get_paginator("list_resource_record_sets")
        for page in paginator.paginate(HostedZoneId=zone_id):
            batch = []
            for rrs in page["ResourceRecordSets"]:
                if rrs["Type"] in ("NS", "SOA"):
                    continue
                batch.append({"Action": "DELETE", "ResourceRecordSet": rrs})
            if batch:
                r53.change_resource_record_sets(
                    HostedZoneId=zone_id, ChangeBatch={"Changes": batch}
                )
        r53.delete_hosted_zone(Id=zone_id)
        print(f"  [teardown] Deleted zone {zone_id}")
    except Exception as e:
        print(f"  [teardown] Warning: could not delete zone {zone_id}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Setup — create smoke zone
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_01_setup_zone():
    """Create a fresh public Route53 hosted zone for the smoke test."""
    token = _env("API_TOKEN")
    creds, connector_id = await _get_aws_creds()
    r53 = _r53_client(creds)

    ts = int(time.time())
    # Public zone — DNSSEC works on both public and private zones.
    # Public zones allow us to verify get_dnssec returns a real SIGNING state.
    zone_name = f"nexplane-smoke-dnssec-{ts}.example.com."

    loop = asyncio.get_event_loop()

    def _create():
        resp = r53.create_hosted_zone(
            Name=zone_name,
            CallerReference=f"nexplane-dnssec-smoke-{ts}",
            HostedZoneConfig={"Comment": "nexplane dnssec smoke", "PrivateZone": False},
        )
        raw_id = resp["HostedZone"]["Id"]
        # raw_id is like /hostedzone/Z1234567890
        return raw_id.split("/")[-1]

    zone_id = await loop.run_in_executor(None, _create)

    _STATE["zone_id"] = zone_id
    _STATE["zone_name"] = zone_name
    _STATE["connector_id"] = connector_id
    _STATE["token"] = token

    print(f"  [setup] Created zone {zone_name} id={zone_id}")
    assert zone_id, "Zone creation returned no ID"

    # Brief settle time
    await asyncio.sleep(3)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Execute CR
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_02_execute_cr():
    """Create + plan + approve + execute route53_dnssec_enable CR."""
    token = _STATE.get("token", _env("API_TOKEN"))
    zone_id = _STATE.get("zone_id")
    connector_id = _STATE.get("connector_id")
    assert zone_id, "test_01 must run first"

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(
            "/change-requests",
            json={
                "title": "[smoke] Route53 DNSSEC enable",
                "change_type": "route53_dnssec_enable",
                "desired_outcome": {
                    "summary": "Enable DNSSEC on smoke test zone",
                    "zone_id": zone_id,
                    "kms_key_id": "auto",
                    "ksk_name": "nexplane-ksk-smoke",
                },
                "connector_id": connector_id,
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"create CR failed: {r.text}"
        cr = r.json()

    cr_id = cr["id"]
    _STATE["cr_id"] = cr_id
    print(f"  [execute] CR created id={cr_id}")

    await _plan_and_approve(token, cr_id)

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/execute",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"execute failed: {r.text}"

    # DNSSEC enable can take up to 5 minutes; poll for 6 minutes
    detail = await _poll_cr(token, cr_id, {"completed", "failed"}, timeout=360)
    assert detail["status"] == "completed", (
        f"CR did not complete. Status={detail.get('status')}. "
        f"Result={_extract_exec_result(detail)}"
    )
    _STATE["cr_detail"] = detail
    print(f"  [execute] CR completed")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 assertions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_03_verify_execution_result():
    """Assert execution_result contains SIGNING status and DS record."""
    detail = _STATE.get("cr_detail")
    assert detail, "test_02 must run first"

    result = _extract_exec_result(detail)
    print(f"  [verify] execution_result keys: {list(result.keys())}")

    # Status must be 'signing'
    assert result.get("status") == "signing", (
        f"Expected status='signing', got: {result.get('status')!r}\nFull result: {result}"
    )

    # DS record must be a non-empty string
    ds_record = result.get("ds_record", "")
    assert ds_record and isinstance(ds_record, str), (
        f"ds_record is missing or empty: {ds_record!r}"
    )

    # Registrar instructions must mention the zone name
    zone_name = _STATE["zone_name"].rstrip(".")
    instructions = result.get("registrar_instructions", "")
    assert zone_name in instructions, (
        f"registrar_instructions does not mention zone name {zone_name!r}: {instructions!r}"
    )

    # key_tag must be an integer
    assert isinstance(result.get("key_tag"), int), (
        f"key_tag missing or not an int: {result.get('key_tag')!r}"
    )

    # kms_key_arn must be present and look like an ARN
    kms_arn = result.get("kms_key_arn", "")
    assert kms_arn.startswith("arn:aws:kms:"), (
        f"kms_key_arn missing or malformed: {kms_arn!r}"
    )

    _STATE["exec_result"] = result
    print(f"  [verify] ds_record={ds_record[:40]}... key_tag={result.get('key_tag')}")

    # Direct boto3 verify: confirm Route53 reports SIGNING
    creds, _ = await _get_aws_creds()
    r53 = _r53_client(creds)
    zone_id = _STATE["zone_id"]
    loop = asyncio.get_event_loop()
    dnssec_resp = await loop.run_in_executor(None, lambda: r53.get_dnssec(HostedZoneId=zone_id))
    sig_status = dnssec_resp.get("Status", {}).get("ServeSignature", "")
    assert sig_status == "SIGNING", (
        f"Route53 get_dnssec reports {sig_status!r} — expected SIGNING"
    )
    print(f"  [verify] Route53 DNSSEC status confirmed: {sig_status}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Rollback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_04_rollback():
    """Trigger rollback and assert zone returns to non-SIGNING state."""
    token = _STATE.get("token", _env("API_TOKEN"))
    cr_id = _STATE.get("cr_id")
    assert cr_id, "test_02 must run first"

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"rollback trigger failed: {r.text}"

    detail = await _poll_cr(token, cr_id, {"rolled_back", "rollback_failed"}, timeout=180)
    assert detail["status"] == "rolled_back", (
        f"Rollback did not complete. Status={detail.get('status')}"
    )
    print(f"  [rollback] CR rolled back successfully")

    # Direct boto3 verify: DNSSEC should no longer be SIGNING
    creds, _ = await _get_aws_creds()
    r53 = _r53_client(creds)
    zone_id = _STATE["zone_id"]
    loop = asyncio.get_event_loop()

    # Brief pause to let Route53 propagate the disable
    await asyncio.sleep(5)

    dnssec_resp = await loop.run_in_executor(None, lambda: r53.get_dnssec(HostedZoneId=zone_id))
    sig_status = dnssec_resp.get("Status", {}).get("ServeSignature", "")
    assert sig_status != "SIGNING", (
        f"Zone is still SIGNING after rollback — expected NOT_SIGNING or similar, got {sig_status!r}"
    )
    print(f"  [rollback] Route53 DNSSEC status after rollback: {sig_status}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Teardown
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_05_teardown():
    """Delete the smoke test hosted zone.

    Note: The KMS key (if created by the executor) entered 7-day pending
    deletion during rollback. AWS does not allow immediate deletion of KMS
    keys — the minimum pending window is 7 days. No action needed here.
    """
    creds, _ = await _get_aws_creds()
    r53 = _r53_client(creds)
    zone_id = _STATE.get("zone_id")

    if zone_id:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _delete_zone_safely(r53, zone_id))

    kms_arn = _STATE.get("exec_result", {}).get("kms_key_arn")
    print(
        f"  [teardown] Done. "
        f"{'KMS key ' + kms_arn + ' is in 7-day pending deletion.' if kms_arn else 'No KMS key to clean up.'}"
    )
```

### Step 3.2 — Commit the smoke test

```
git add backend/tests/smoke/test_smoke_route53_dnssec.py
git commit -m "test: add route53_dnssec smoke test (4 phases)"
```

**Expected output:** `[master ...] test: add route53_dnssec smoke test (4 phases)`

### Step 3.3 — SCP to EC2 and run smoke

```bash
# From local Windows — scp all changed files
scp -i ~/.ssh/id_ed25519 \
  backend/app/connectors/executors/aws/route53_dnssec.py \
  backend/app/connectors/change_type_definitions/route53_dnssec_enable.json \
  backend/app/models/change_request.py \
  backend/app/connectors/catalog/aws.json \
  backend/alembic/versions/20260805_001_add_route53_dnssec_enable_change_type.py \
  backend/tests/smoke/test_smoke_route53_dnssec.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/<matching-paths>
```

Or (preferred) just `git pull` on EC2 after pushing to master:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull && \
   docker exec nexplane-backend-1 alembic upgrade head && \
   docker compose restart backend"
```

Then run the smoke from inside the container:

```bash
docker exec -it nexplane-backend-1 bash -c \
  "cd /app && API_TOKEN=\$NEXPLANE_API_TOKEN \
   pytest tests/smoke/test_smoke_route53_dnssec.py -v -s -m smoke 2>&1 | tee /tmp/dnssec_smoke.log"
```

**Expected output (all 5 tests pass):**

```
tests/smoke/test_smoke_route53_dnssec.py::test_01_setup_zone PASSED
tests/smoke/test_smoke_route53_dnssec.py::test_02_execute_cr PASSED
tests/smoke/test_smoke_route53_dnssec.py::test_03_verify_execution_result PASSED
tests/smoke/test_smoke_route53_dnssec.py::test_04_rollback PASSED
tests/smoke/test_smoke_route53_dnssec.py::test_05_teardown PASSED
5 passed in ...
```

### Step 3.4 — Mark catalog entry smoke_verified and final commit

After all 5 phases pass, update `backend/app/connectors/catalog/aws.json`:
- Find the `route53_dnssec_enable` entry
- Change `"smoke_verified": false` → `"smoke_verified": true`

```
git add backend/app/connectors/catalog/aws.json
git commit -m "chore: mark route53_dnssec_enable smoke_verified=true"
```

---

## Completion checklist

- [ ] Task 1: `route53_dnssec.py` executor created and committed
- [ ] Task 2: ChangeType enum updated, migration created, change_type_definition created, catalog entry added, executor registered in dispatcher, migration applied on EC2
- [ ] Task 3: Smoke test created; all 5 phases pass on EC2 runner; `smoke_verified` flipped to `true`
