# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: DNS Zone Migration (Route53)

Creates two Route53 private hosted zones, runs dns_zone_migrate CR to
lower TTLs and switch NS delegation, then rolls back and deletes both zones.

Run:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_dns_zone_migrate.py -v -s

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


def _r53_client():
    return boto3.client(
        "route53",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
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
        assert r.status_code == 200
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "dns zone migrate smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"approve failed: {r.text}"


async def _poll_cr(token: str, cr_id: str, terminal: set, timeout: int = 300) -> dict:
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


def _wait_change_insync(r53, change_id: str, timeout: int = 60) -> None:
    for _ in range(timeout // 5):
        resp = r53.get_change(Id=change_id)
        if resp["ChangeInfo"]["Status"] == "INSYNC":
            return
        time.sleep(5)


def _create_private_zone(r53, name: str, caller_ref: str) -> str:
    resp = r53.create_hosted_zone(
        Name=name,
        CallerReference=caller_ref,
        HostedZoneConfig={"Comment": "nexplane smoke", "PrivateZone": False},
    )
    return resp["HostedZone"]["Id"].split("/")[-1]


def _delete_zone(r53, zone_id: str) -> None:
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


@pytest.mark.smoke
@pytest.mark.smoke_phase("DNS_ZONE_MIGRATE")
async def test_01_create_zones():
    token = _env("API_TOKEN")
    r53 = _r53_client()
    ts = int(time.time())
    source_name = f"nexplane-smoke-src-{ts}.internal."
    target_name = f"nexplane-smoke-tgt-{ts}.internal."

    loop = asyncio.get_event_loop()
    source_id = await loop.run_in_executor(
        None, lambda: _create_private_zone(r53, source_name, f"src-{ts}")
    )
    target_id = await loop.run_in_executor(
        None, lambda: _create_private_zone(r53, target_name, f"tgt-{ts}")
    )

    _STATE["source_id"] = source_id
    _STATE["target_id"] = target_id
    _STATE["source_name"] = source_name
    _STATE["target_name"] = target_name
    _STATE["token"] = token

    await asyncio.sleep(2)

    # Add a test A record to source zone
    def _add_record():
        return r53.change_resource_record_sets(
            HostedZoneId=source_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "CREATE",
                    "ResourceRecordSet": {
                        "Name": f"test.{source_name}",
                        "Type": "A",
                        "TTL": 300,
                        "ResourceRecords": [{"Value": "10.0.0.1"}],
                    },
                }]
            },
        )

    resp = await loop.run_in_executor(None, _add_record)
    _STATE["test_record_added"] = True

    assert source_id, "Source zone creation failed"
    assert target_id, "Target zone creation failed"


@pytest.mark.smoke
@pytest.mark.smoke_phase("DNS_ZONE_MIGRATE")
async def test_02_create_and_execute_cr():
    token = _STATE.get("token", _env("API_TOKEN"))
    source_id = _STATE.get("source_id")
    target_id = _STATE.get("target_id")
    assert source_id and target_id, "test_01 must run first"

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        headers = {"Authorization": f"Bearer {jwt}"}

        # Find AWS connector
        r = await client.get("/connectors", params={"connector_type": "aws"}, headers=headers)
        assert r.status_code == 200
        connectors = r.json()
        aws_connector = connectors[0] if connectors else None
        assert aws_connector, "No AWS connector registered"

        r = await client.post(
            "/change-requests",
            json={
                "title": "[smoke] DNS zone migration test",
                "change_type": "dns_zone_migrate",
                "connector_id": aws_connector["id"],
                "parameters": {
                    "source_zone_id": source_id,
                    "target_zone_id": target_id,
                    "ttl_lower_value": 30,
                    "propagation_wait_seconds": 5,
                    "verify_resolvers": ["8.8.8.8"],
                },
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"create CR failed: {r.text}"
        cr = r.json()

    _STATE["cr_id"] = cr["id"]
    await _plan_and_approve(token, cr["id"])

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr['id']}/execute",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"execute failed: {r.text}"

    detail = await _poll_cr(token, cr["id"], {"completed", "failed"}, timeout=180)
    assert detail["status"] == "completed", f"CR did not complete: {detail.get('status')}"
    _STATE["detail"] = detail


@pytest.mark.smoke
@pytest.mark.smoke_phase("DNS_ZONE_MIGRATE")
async def test_03_verify_execution_result():
    detail = _STATE.get("detail")
    assert detail, "test_02 must run first"

    result = _extract_exec_result(detail)
    assert "phases" in result, f"Missing 'phases' in result: {result}"
    assert "rollback_data" in result, f"Missing 'rollback_data' in result: {result}"

    phases = {p["phase"]: p for p in result["phases"]}
    for expected in ("preflight", "lower_ttl", "wait_propagation", "switch_ns", "verify"):
        assert expected in phases, f"Missing phase: {expected}"
        assert phases[expected]["status"] == "ok", (
            f"Phase {expected} failed: {phases[expected]}"
        )

    assert "original_ns" in result["rollback_data"], "rollback_data missing original_ns"
    assert "original_ttls" in result["rollback_data"], "rollback_data missing original_ttls"


@pytest.mark.smoke
@pytest.mark.smoke_phase("DNS_ZONE_MIGRATE")
async def test_04_rollback():
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
    assert detail["status"] == "rolled_back", f"Rollback did not complete: {detail.get('status')}"


@pytest.mark.smoke
@pytest.mark.smoke_phase("DNS_ZONE_MIGRATE")
async def test_05_cleanup():
    r53 = _r53_client()
    loop = asyncio.get_event_loop()
    for zone_id in (_STATE.get("source_id"), _STATE.get("target_id")):
        if zone_id:
            try:
                await loop.run_in_executor(None, lambda zid=zone_id: _delete_zone(r53, zid))
            except Exception as e:
                print(f"Warning: cleanup of zone {zone_id} failed: {e}")
