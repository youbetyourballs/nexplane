# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Route53 DNSSEC Enable

Creates a public Route53 hosted zone, runs route53_dnssec_enable CR through the
full lifecycle (create→plan→approve→execute), verifies SIGNING status and DS
record extraction, rolls back (disable+delete KSK+schedule KMS deletion), then
tears down the zone.

NOTE: The KMS key created during execute will enter a 7-day pending deletion
window on rollback — AWS enforces a minimum of 7 days and there is no way to
force-delete it sooner.

Run:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_route53_dnssec.py -v -s

Must be run from EC2 runner on Tailscale, not local Docker.
"""

import asyncio
import hashlib
import os
import time
import uuid

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


def _r53_client_from_creds(creds: dict):
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
            json={"decision": "approved", "comment": "route53 dnssec smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"approve failed: {r.text}"


async def _poll_cr(token: str, cr_id: str, terminal: set, timeout: int = 600) -> dict:
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
        if detail.get("status") in terminal:
            return detail
    pytest.fail(f"CR {cr_id} did not reach {terminal} in {timeout}s")


def _extract_exec_result(detail: dict) -> dict:
    runs = detail.get("execution_runs", [])
    for run in reversed(runs):
        steps = run.get("result", {}).get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result", {})
    return detail.get("execution_result", {})


def _delete_zone_records(r53, zone_id: str) -> None:
    """Delete all non-NS/SOA records so the zone can be deleted."""
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


# ---------------------------------------------------------------------------
# Phase 1: create the smoke zone
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_01_create_zone():
    """Create a public hosted zone for smoke testing DNSSEC."""
    token = _env("API_TOKEN")
    creds, connector_id = await _get_aws_creds()
    r53 = _r53_client_from_creds(creds)

    uid = uuid.uuid4().hex[:8]
    zone_name = f"smoke-dnssec-{uid}.example.com."
    caller_ref = f"nexplane-smoke-dnssec-{uid}"

    loop = asyncio.get_event_loop()

    def _create():
        return r53.create_hosted_zone(
            Name=zone_name,
            CallerReference=caller_ref,
            HostedZoneConfig={"Comment": "nexplane smoke dnssec", "PrivateZone": False},
        )

    resp = await loop.run_in_executor(None, _create)
    zone_id = resp["HostedZone"]["Id"].split("/")[-1]

    _STATE["token"] = token
    _STATE["connector_id"] = connector_id
    _STATE["zone_id"] = zone_id
    _STATE["zone_name"] = zone_name
    _STATE["uid"] = uid

    assert zone_id, "Zone creation failed — no zone ID returned"
    print(f"\n[smoke] Created zone {zone_name} → {zone_id}")


# ---------------------------------------------------------------------------
# Phase 2: full CR lifecycle (create → plan → approve → execute)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_02_execute_cr():
    """Run route53_dnssec_enable CR through full lifecycle."""
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
                "title": f"[smoke] Enable Route53 DNSSEC for {_STATE.get('zone_name', zone_id)}",
                "change_type": "route53_dnssec_enable",
                "desired_outcome": {
                    "summary": "Enable DNSSEC signing on smoke test zone",
                    "zone_id": zone_id,
                    "kms_key_id": "auto",
                    "ksk_name": "nexplane-smoke-ksk",
                    "key_signing_algorithm": "ECDSAP256SHA256",
                },
                "connector_id": connector_id,
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"create CR failed: {r.text}"
        cr = r.json()

    cr_id = cr["id"]
    _STATE["cr_id"] = cr_id
    print(f"\n[smoke] Created CR {cr_id}")

    await _plan_and_approve(token, cr_id)

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/execute",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"execute failed: {r.text}"

    # DNSSEC enabling can take up to 5 minutes → 600s timeout
    detail = await _poll_cr(token, cr_id, {"completed", "failed"}, timeout=600)
    assert detail["status"] == "completed", f"CR did not complete: {detail.get('status')}"
    _STATE["detail"] = detail
    print(f"\n[smoke] CR {cr_id} completed")


# ---------------------------------------------------------------------------
# Phase 3: verify SIGNING status and DS record
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_03_verify_dnssec_signing():
    """Assert the zone is now SIGNING and has a non-empty DS record."""
    detail = _STATE.get("detail")
    assert detail, "test_02 must run first"

    result = _extract_exec_result(detail)
    print(f"\n[smoke] execution_result: {result}")

    assert result.get("status") == "signing", (
        f"Expected status='signing', got: {result.get('status')}"
    )
    assert result.get("ds_record"), "ds_record is empty — DNSSEC not fully enabled"
    assert result.get("kms_key_arn"), "kms_key_arn missing from result"
    assert result.get("zone_id") == _STATE["zone_id"], "zone_id mismatch in result"

    _STATE["kms_key_arn"] = result.get("kms_key_arn")
    _STATE["kms_key_created"] = result.get("kms_key_created_by_nexplane", False)

    print(f"\n[smoke] DS record: {result['ds_record']}")
    print(f"[smoke] KMS key: {result['kms_key_arn']} (created_by_nexplane={_STATE['kms_key_created']})")

    # Verify directly via boto3 as well
    creds, _ = await _get_aws_creds()
    r53 = _r53_client_from_creds(creds)
    loop = asyncio.get_event_loop()
    dnssec_resp = await loop.run_in_executor(
        None, lambda: r53.get_dnssec(HostedZoneId=_STATE["zone_id"])
    )
    live_status = dnssec_resp.get("Status", {}).get("ServeSignature", "")
    assert live_status == "SIGNING", f"Live boto3 check: zone not SIGNING, got {live_status!r}"
    print(f"[smoke] boto3 live check: ServeSignature={live_status} ✓")


# ---------------------------------------------------------------------------
# Phase 4: rollback
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_04_rollback():
    """Roll back the CR — disables DNSSEC, deletes KSK, schedules KMS key deletion."""
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

    detail = await _poll_cr(token, cr_id, {"rolled_back", "rollback_failed"}, timeout=300)
    assert detail["status"] == "rolled_back", (
        f"Rollback did not complete: {detail.get('status')}"
    )
    print(f"\n[smoke] CR {cr_id} rolled back ✓")

    # Verify zone is no longer SIGNING via boto3
    creds, _ = await _get_aws_creds()
    r53 = _r53_client_from_creds(creds)
    loop = asyncio.get_event_loop()
    dnssec_resp = await loop.run_in_executor(
        None, lambda: r53.get_dnssec(HostedZoneId=_STATE["zone_id"])
    )
    live_status = dnssec_resp.get("Status", {}).get("ServeSignature", "")
    assert live_status != "SIGNING", (
        f"After rollback, zone still reports SIGNING — rollback may have failed"
    )
    print(f"[smoke] boto3 post-rollback check: ServeSignature={live_status!r} ✓")


# ---------------------------------------------------------------------------
# Phase 5: teardown — delete the zone
# (KMS key remains in 7-day pending deletion — AWS enforces minimum)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROUTE53_DNSSEC")
async def test_05_teardown():
    """Delete the smoke zone. KMS key stays in 7-day pending deletion window."""
    zone_id = _STATE.get("zone_id")
    if not zone_id:
        pytest.skip("No zone_id in state — nothing to clean up")

    creds, _ = await _get_aws_creds()
    r53 = _r53_client_from_creds(creds)
    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(None, lambda: _delete_zone_records(r53, zone_id))
    except Exception as exc:
        print(f"[smoke] Warning: failed to delete zone records: {exc}")

    try:
        await loop.run_in_executor(None, lambda: r53.delete_hosted_zone(Id=zone_id))
        print(f"\n[smoke] Deleted zone {zone_id} ✓")
    except Exception as exc:
        print(f"[smoke] Warning: failed to delete zone {zone_id}: {exc}")

    kms_arn = _STATE.get("kms_key_arn")
    if kms_arn and _STATE.get("kms_key_created"):
        print(
            f"[smoke] KMS key {kms_arn} is in 7-day pending deletion "
            f"(AWS minimum — cannot force-delete sooner)"
        )
    print("[smoke] Teardown complete ✓")
