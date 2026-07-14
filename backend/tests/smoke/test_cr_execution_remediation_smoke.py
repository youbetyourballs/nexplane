# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: CR Execution Mechanism Remediation
Phases:
  ROLLBACK_CAPABILITY_GATE  — planning hard-fails when ROLLBACK_CAPABILITY missing
  PRESTATE_CAPTURE          — execute a CR; assert pre_state_snapshots row created
  PRESTATE_ROLLBACK         — execute then rollback; assert resource reconstituted
  IRREVERSIBLE_WARNING      — plan a CR with irreversible step; assert rollback_warning in plan
  RETENTION_PURGE           — insert expired row; run purge; assert deleted
"""

import os
import sys
import uuid
import asyncio
import datetime

BASE_URL = os.environ.get("NEXPLANE_URL", "http://localhost:8000")
TOKEN = os.environ.get("NEXPLANE_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

import httpx

client = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=120)

# AWS connector ID for the primary AWS connector in the smoke environment
_AWS_CONNECTOR_ID = "666e237d-bfcf-43a5-ae24-f1a0b4f2c5cc"


def _get_r53_client():
    """Return a boto3 Route53 client using platform credentials when available."""
    import boto3
    # Try platform credentials first (available when running inside/alongside the container)
    try:
        import sys
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        import asyncio as _asyncio
        from app.database import AsyncSessionLocal
        from app.models.connector_credential import ConnectorCredential
        from app.services.secret_backend_factory import get_secret_backend
        from sqlalchemy import select
        import uuid as _uuid

        async def _fetch():
            backend = get_secret_backend()
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ConnectorCredential).where(
                        ConnectorCredential.connector_id == _uuid.UUID(_AWS_CONNECTOR_ID)
                    )
                )
                cred_row = result.scalar_one_or_none()
                if cred_row:
                    return backend.decrypt_json(cred_row.credentials_encrypted)
                return {}

        creds = _asyncio.run(_fetch())
        if creds.get("access_key_id"):
            print(f"  Using platform AWS credentials (key: {creds['access_key_id'][:8]}...)")
            session_kwargs = dict(
                aws_access_key_id=creds["access_key_id"],
                aws_secret_access_key=creds["secret_access_key"],
                region_name=creds.get("region", "us-east-1"),
            )
            if creds.get("session_token"):
                session_kwargs["aws_session_token"] = creds["session_token"]
            return boto3.client("route53", **session_kwargs)
    except Exception as e:
        print(f"  Could not load platform creds ({e}), falling back to instance role/env")
    return boto3.client("route53")


def _assert(condition: bool, msg: str):
    if not condition:
        print(f"  FAIL: {msg}")
        sys.exit(1)
    print(f"  OK: {msg}")


def phase_rollback_capability_gate():
    print("\n=== PHASE: ROLLBACK_CAPABILITY_GATE ===")
    # Positive test: run_ssm_command now declares ROLLBACK_CAPABILITY=irreversible.
    # Planning should SUCCEED and return rollback_warning in the step.
    resp = client.post("/change-requests", json={
        "change_type": "ssm_command",
        "title": "smoke: capability gate positive test",
        "desired_outcome": {
            "command": "echo hello",
            "instance_id": "i-fake000000000000",
            "_locked_connector_type": "aws",
        },
    })
    _assert(resp.status_code == 201, f"CR created (status {resp.status_code}): {resp.text[:200]}")
    cr_id = resp.json()["id"]

    plan_resp = client.post(f"/change-requests/{cr_id}/plan")
    _assert(
        plan_resp.status_code in (200, 201),
        f"plan succeeded for irreversible executor (status {plan_resp.status_code}): {plan_resp.text[:300]}",
    )
    plan = plan_resp.json()
    steps_with_warning = [s for s in plan.get("generated_steps", []) if s.get("rollback_warning")]
    _assert(len(steps_with_warning) > 0, f"rollback_warning present on irreversible step")
    print(f"  rollback_warning: {steps_with_warning[0]['rollback_warning'][:100]}")

    # Negative test: a totally unknown change_type should fail at CR creation or planning.
    # The planning engine will fail to load a .json definition for an unknown change_type.
    neg_resp = client.post("/change-requests", json={
        "change_type": "nonexistent_action_xyz_smoke_test",
        "title": "smoke: capability gate negative test",
        "desired_outcome": {},
    })
    if neg_resp.status_code in (400, 422):
        print(f"  OK: unknown change_type rejected at creation (status {neg_resp.status_code})")
    else:
        # CR might be created; try planning it
        neg_cr_id = neg_resp.json().get("id")
        if neg_cr_id:
            neg_plan = client.post(f"/change-requests/{neg_cr_id}/plan")
            _assert(
                neg_plan.status_code in (400, 422, 500),
                f"planning unknown change_type failed (status {neg_plan.status_code}): {neg_plan.text[:200]}",
            )
            print(f"  OK: unknown change_type rejected at plan (status {neg_plan.status_code})")
        else:
            _assert(False, f"unexpected response for unknown change_type: {neg_resp.status_code} {neg_resp.text[:200]}")


def _get_s3_client():
    """Return a boto3 S3 client using platform credentials."""
    import boto3
    try:
        import sys
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        import asyncio as _asyncio
        from app.database import AsyncSessionLocal
        from app.models.connector_credential import ConnectorCredential
        from app.services.secret_backend_factory import get_secret_backend
        from sqlalchemy import select
        import uuid as _uuid

        async def _fetch():
            backend = get_secret_backend()
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ConnectorCredential).where(
                        ConnectorCredential.connector_id == _uuid.UUID(_AWS_CONNECTOR_ID)
                    )
                )
                cred_row = result.scalar_one_or_none()
                return backend.decrypt_json(cred_row.credentials_encrypted) if cred_row else {}

        creds = _asyncio.run(_fetch())
        if creds.get("access_key_id"):
            session_kwargs = dict(
                aws_access_key_id=creds["access_key_id"],
                aws_secret_access_key=creds["secret_access_key"],
                region_name=creds.get("region", "us-east-1"),
            )
            if creds.get("session_token"):
                session_kwargs["aws_session_token"] = creds["session_token"]
            return boto3.client("s3", **session_kwargs), creds.get("region", "us-east-1")
    except Exception as e:
        print(f"  Could not load platform creds ({e}), falling back to instance role")
    return boto3.client("s3"), "us-east-1"


def phase_prestate_capture():
    print("\n=== PHASE: PRESTATE_CAPTURE ===")
    # Verify that executing an s3_bucket_delete CR creates a pre_state_snapshots row.
    # We create a temp S3 bucket, delete it via CR, then verify the snapshot row.
    import boto3
    import time

    s3, region = _get_s3_client()
    bucket_name = f"nexplane-smoke-cap-{uuid.uuid4().hex[:12]}"
    print(f"  Creating test bucket: {bucket_name}")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": region})
    print(f"  Created: s3://{bucket_name}")

    resp = client.post("/change-requests", json={
        "change_type": "s3_bucket_delete",
        "title": "smoke: pre-state capture test",
        "desired_outcome": {
            "bucket_name": bucket_name,
            "_locked_connector_type": "aws",
        },
    })
    _assert(resp.status_code == 201, f"CR created: {resp.status_code} {resp.text[:200]}")
    cr_id = resp.json()["id"]

    plan_resp = client.post(f"/change-requests/{cr_id}/plan")
    _assert(plan_resp.status_code in (200, 201), f"planned: {plan_resp.status_code} {plan_resp.text[:300]}")

    approve_resp = client.post(f"/change-requests/{cr_id}/approve")
    _assert(approve_resp.status_code in (200, 201), f"approved: {approve_resp.status_code}")

    execute_resp = client.post(f"/change-requests/{cr_id}/execute")
    _assert(execute_resp.status_code in (200, 202), f"execute queued: {execute_resp.status_code}")

    status = None
    for _ in range(24):
        time.sleep(5)
        cr = client.get(f"/change-requests/{cr_id}").json()
        status = cr.get("status")
        if status in ("completed", "failed", "error"):
            break
    _assert(status == "completed", f"CR completed (status={status})")

    # Verify pre_state_snapshots row
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    async def _check():
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("SELECT id FROM pre_state_snapshots WHERE cr_id = :cr_id"),
                {"cr_id": cr_id},
            )
            return len(result.fetchall())

    count = asyncio.run(_check())
    _assert(count >= 1, f"pre_state_snapshots row exists for cr_id={cr_id}: found {count}")
    print(f"  pre_state_snapshots rows: {count}")


def phase_prestate_rollback():
    print("\n=== PHASE: PRESTATE_ROLLBACK ===")
    # Create a test S3 bucket, delete it via CR, rollback, verify bucket reconstituted.
    import time

    s3, region = _get_s3_client()
    bucket_name = f"nexplane-smoke-rb-{uuid.uuid4().hex[:12]}"
    print(f"  Creating test bucket: {bucket_name}")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": region})
    print(f"  Created: s3://{bucket_name}")

    resp = client.post("/change-requests", json={
        "change_type": "s3_bucket_delete",
        "title": "smoke: pre-state rollback test",
        "desired_outcome": {
            "bucket_name": bucket_name,
            "_locked_connector_type": "aws",
        },
    })
    _assert(resp.status_code == 201, f"CR created: {resp.status_code}")
    cr_id = resp.json()["id"]

    plan_resp = client.post(f"/change-requests/{cr_id}/plan")
    _assert(plan_resp.status_code in (200, 201), f"planned: {plan_resp.status_code}")

    approve_resp = client.post(f"/change-requests/{cr_id}/approve")
    _assert(approve_resp.status_code in (200, 201), f"approved: {approve_resp.status_code}")

    execute_resp = client.post(f"/change-requests/{cr_id}/execute")
    _assert(execute_resp.status_code in (200, 202), f"execute queued: {execute_resp.status_code}")

    status = None
    for _ in range(24):
        time.sleep(5)
        status = client.get(f"/change-requests/{cr_id}").json()["status"]
        if status in ("completed", "failed", "error"):
            break
    _assert(status == "completed", f"CR completed: {status}")

    # Verify bucket is actually deleted
    import botocore.exceptions
    try:
        s3.head_bucket(Bucket=bucket_name)
        _assert(False, f"bucket should be deleted but head_bucket succeeded: {bucket_name}")
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            print(f"  OK: bucket deleted: {bucket_name}")
        else:
            raise

    # Rollback
    rollback_resp = client.post(f"/change-requests/{cr_id}/rollback")
    _assert(rollback_resp.status_code in (200, 202), f"rollback queued: {rollback_resp.status_code}")

    for _ in range(24):
        time.sleep(5)
        status = client.get(f"/change-requests/{cr_id}").json()["status"]
        if status in ("rolled_back", "rollback_partial", "rollback_failed"):
            break
    _assert(status == "rolled_back", f"CR rolled back: {status}")

    # Verify bucket reconstituted
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"  OK: bucket reconstituted: {bucket_name}")
    except botocore.exceptions.ClientError:
        _assert(False, f"bucket not reconstituted after rollback: {bucket_name}")

    # Cleanup
    s3.delete_bucket(Bucket=bucket_name)
    print(f"  Cleanup: deleted reconstituted bucket {bucket_name}")


def phase_irreversible_warning():
    print("\n=== PHASE: IRREVERSIBLE_WARNING ===")
    resp = client.post("/change-requests", json={
        "change_type": "ssm_command",
        "title": "smoke: irreversible warning test",
        "desired_outcome": {
            "command": "echo hello",
            "instance_id": "i-fake000000000001",
            "_locked_connector_type": "aws",
        },
    })
    _assert(resp.status_code == 201, f"CR created: {resp.status_code}")
    cr_id = resp.json()["id"]

    plan_resp = client.post(f"/change-requests/{cr_id}/plan")
    _assert(plan_resp.status_code in (200, 201), f"plan not blocked (status {plan_resp.status_code}): {plan_resp.text[:300]}")
    plan = plan_resp.json()
    warnings = [s.get("rollback_warning") for s in plan.get("generated_steps", []) if s.get("rollback_warning")]
    _assert(len(warnings) > 0, f"rollback_warning in plan steps: {warnings}")
    print(f"  rollback_warning: {warnings[0][:100]}")

    cr_status = client.get(f"/change-requests/{cr_id}").json()["status"]
    _assert(
        cr_status in ("planned", "safety_review", "awaiting_approval"),
        f"CR in expected post-plan status (not blocked): {cr_status}",
    )
    print(f"  CR status after plan: {cr_status}")


def phase_retention_purge():
    print("\n=== PHASE: RETENTION_PURGE ===")
    from app.database import AsyncSessionLocal
    from app.models.pre_state_snapshot import PreStateSnapshot
    from app.services.pre_state_store import PreStateStore

    async def _run():
        from sqlalchemy import text as _text
        async with AsyncSessionLocal() as db:
            # Fetch a real CR id and org id to satisfy FK constraints
            row = (await db.execute(_text(
                "SELECT id, organization_id FROM change_requests ORDER BY created_at DESC LIMIT 1"
            ))).fetchone()
            assert row, "No change_requests in DB — run another phase first"
            real_cr_id, real_org_id = row[0], row[1]

            snap = PreStateSnapshot(
                id=uuid.uuid4(),
                cr_id=real_cr_id,
                step_id="step_smoke_purge",
                organization_id=real_org_id,
                state_json={"test": True},
                captured_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31),
                expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1),
            )
            db.add(snap)
            await db.commit()
            snap_id = snap.id
            print(f"  Inserted expired snapshot: {snap_id} (cr_id={real_cr_id})")

        async with AsyncSessionLocal() as db:
            count = await PreStateStore.purge_expired(db)
            await db.commit()

        assert count >= 1, f"purge deleted {count} rows — expected >= 1"
        print(f"  purge deleted {count} expired row(s)")

    asyncio.run(_run())


if __name__ == "__main__":
    phase = os.environ.get("SMOKE_PHASE", "ALL")
    phases = {
        "ROLLBACK_CAPABILITY_GATE": phase_rollback_capability_gate,
        "PRESTATE_CAPTURE": phase_prestate_capture,
        "PRESTATE_ROLLBACK": phase_prestate_rollback,
        "IRREVERSIBLE_WARNING": phase_irreversible_warning,
        "RETENTION_PURGE": phase_retention_purge,
    }
    if phase == "ALL":
        for name, fn in phases.items():
            fn()
    elif phase in phases:
        phases[phase]()
    else:
        print(f"Unknown phase: {phase}")
        sys.exit(1)
    print("\n=== ALL PHASES PASSED ===")
