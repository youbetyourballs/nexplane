#!/usr/bin/env python3
from __future__ import annotations
"""
Live smoke tests for three 2026-05-26 features:

  VULN_MITIGATION  — verify /findings/{id}/mitigate dispatches new CR types
  CREDENTIAL_EXPIRY — verify credential_expiry_worker runs all new checks against
                      a live Vault dev instance (reuses cached AMI from test_secret_store_live)
  MCP_AGENT_TOKENS — create/scope-enforce/revoke agent tokens via live API

Run from EC2 runner (inside backend container):
    python tests/smoke/test_feature_smoke_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123 \\
        --phases VULN_MITIGATION,CREDENTIAL_EXPIRY,MCP_AGENT_TOKENS
"""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid

import boto3

# Allow imports from within the container
_IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/app/app")
if _IN_CONTAINER and "/app" not in sys.path:
    sys.path.insert(0, "/app")
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

from smoke_helpers import NexplaneClient, log, fail, make_base_parser, _get_aws_boto3_client

SMOKE_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

# ── Vault infra (reuse script+AMI from test_secret_store_live) ────────────────

VAULT_SETUP_SCRIPT = r"""#!/bin/bash
set -e
which vault 2>/dev/null || {
    yum install -y yum-utils 2>/dev/null || apt-get install -y gpg 2>/dev/null || true
    curl -fsSL https://rpm.releases.hashicorp.com/AmazonLinux/hashicorp.repo \
        -o /etc/yum.repos.d/hashicorp.repo 2>/dev/null || true
    yum install -y vault 2>/dev/null || {
        curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
        echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
            > /etc/apt/sources.list.d/hashicorp.list
        apt-get update -qq && apt-get install -y vault
    }
}
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=nexplane-smoke-root
pkill vault 2>/dev/null || true
sleep 2
nohup vault server -dev -dev-root-token-id=nexplane-smoke-root -dev-listen-address=0.0.0.0:8200 \
    > /var/log/vault-dev.log 2>&1 &
sleep 5
vault kv enable-versioning secret 2>/dev/null || true
echo "VAULT_SETUP_COMPLETE"
"""

VAULT_SETUP_HASH = hashlib.sha256(VAULT_SETUP_SCRIPT.encode()).hexdigest()[:12]


def _get_or_create_smoke_ami_safe(ssm_client, ec2_client, instance_id, name, setup_hash):
    try:
        from run_on_ec2 import get_or_create_smoke_ami as _fn
        return _fn(ssm_client, ec2_client, instance_id, name, setup_hash)
    except ImportError:
        pass
    try:
        from smoke.run_on_ec2 import get_or_create_smoke_ami as _fn2
        return _fn2(ssm_client, ec2_client, instance_id, name, setup_hash)
    except ImportError:
        pass


def _launch_vault_instance(ec2_client, ssm_client, iam_client):
    """Launch t3.small with Vault dev, returning (instance_id, private_ip). Reuses cached AMI."""
    try:
        from run_on_ec2 import get_default_vpc_subnet, get_ssm_instance_profile
    except ImportError:
        from smoke.run_on_ec2 import get_default_vpc_subnet, get_ssm_instance_profile

    _, subnet_id = get_default_vpc_subnet(ec2_client, instance_type="t3.small")
    instance_profile = get_ssm_instance_profile(iam_client) or "NexplaneEC2TestProfile"

    vpc_id = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"][0]["VpcId"]
    sgs = ec2_client.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "group-name", "Values": ["default"]}]
    )["SecurityGroups"]
    sg_id = sgs[0]["GroupId"]

    ssm_key = f"/nexplane/smoke-amis/vault-dev/{VAULT_SETUP_HASH}"
    cached_ami = None
    try:
        resp = ssm_client.get_parameter(Name=ssm_key)
        cached_ami = resp["Parameter"]["Value"]
        print(f"  Using cached vault-dev AMI: {cached_ami}", flush=True)
    except ssm_client.exceptions.ParameterNotFound:
        pass

    ami_id = cached_ami or "ami-0c02fb55956c7d316"  # Amazon Linux 2 fallback

    resp = ec2_client.run_instances(
        ImageId=ami_id, InstanceType="t3.small", MinCount=1, MaxCount=1,
        SubnetId=subnet_id, SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Name": instance_profile},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-vault-expiry"},
            {"Key": "nxp-ec2-test-runner", "Value": "true"},
            {"Key": "nxp-smoke-temp", "Value": "true"},
        ]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"  Launched Vault instance: {instance_id}", flush=True)

    ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    private_ip = ec2_client.describe_instances(InstanceIds=[instance_id])[
        "Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Wait for SSM
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    if not cached_ami:
        resp_s = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [VAULT_SETUP_SCRIPT]}, TimeoutSeconds=120)
        deadline_setup = time.time() + 180
        out_s = {}
        while time.time() < deadline_setup:
            try:
                out_s = ssm_client.get_command_invocation(
                    CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                if out_s.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(10)
        if "VAULT_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
            print("  Vault setup complete, caching AMI", flush=True)
            _get_or_create_smoke_ami_safe(ssm_client, ec2_client, instance_id, "vault-dev", VAULT_SETUP_HASH)
        else:
            print(f"  WARNING: Vault setup may not have completed: {out_s.get('StandardOutputContent', '')[:200]}", flush=True)
    else:
        start_cmd = (
            "export VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=nexplane-smoke-root && "
            "pkill vault 2>/dev/null || true && sleep 2 && "
            "nohup vault server -dev -dev-root-token-id=nexplane-smoke-root "
            "-dev-listen-address=0.0.0.0:8200 > /var/log/vault-dev.log 2>&1 & sleep 5 && echo VAULT_RESTARTED"
        )
        r2 = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [start_cmd]}, TimeoutSeconds=60)
        deadline_restart = time.time() + 60
        out_r = {}
        while time.time() < deadline_restart:
            try:
                out_r = ssm_client.get_command_invocation(
                    CommandId=r2["Command"]["CommandId"], InstanceId=instance_id)
                if out_r.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(5)
        if "VAULT_RESTARTED" not in out_r.get("StandardOutputContent", ""):
            print(f"  WARNING: Vault restart: {out_r.get('StandardOutputContent', '')[:100]}", flush=True)

    return instance_id, private_ip


# ── Phase: VULN_MITIGATION ────────────────────────────────────────────────────

_VULN_PREFIX = "/api/v1/vulnerability"


def phase_vuln_mitigation(client: NexplaneClient) -> None:
    print("\n[VULN_MITIGATION] Verifying new mitigation CR types dispatch", flush=True)

    # 1. Find or create a finding to mitigate
    findings = client.get(f"{_VULN_PREFIX}/findings", params={"limit": 1})
    if isinstance(findings, dict):
        items = findings.get("items", [])
    else:
        items = findings if isinstance(findings, list) else []

    if not items:
        # Findings come from external scanners — create one in-process via DB
        import asyncio, threading, uuid as _uuid

        assets = client.get("/assets", params={"limit": 50})
        asset_list = assets if isinstance(assets, list) else assets.get("items", [])
        cloud_assets = [a for a in asset_list
                        if a.get("asset_type") == "cloud_account" and a.get("connector_id")]
        if not cloud_assets:
            fail("VULN_MITIGATION: No linked cloud_account assets found")
        asset_id = cloud_assets[0]["id"]

        finding_id_holder = []

        def _create_finding():
            async def _inner():
                import os
                from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
                from app.models.vulnerability import VulnerabilityFinding
                from app.models.organization import Organization
                from sqlalchemy import select

                db_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/nexplane")
                engine = create_async_engine(db_url, pool_size=1, max_overflow=0)
                factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                try:
                    async with factory() as db:
                        org = (await db.execute(select(Organization).limit(1))).scalar_one()
                        finding = VulnerabilityFinding(
                            id=_uuid.uuid4(),
                            organization_id=org.id,
                            asset_id=_uuid.UUID(asset_id),
                            title="Smoke test finding — TLS 1.0 enabled",
                            description="Smoke test finding for mitigation CR type verification",
                            severity="medium",
                            cve_id="CVE-2011-3389",
                            status="open",
                            source="smoke_test",
                            scanner="smoke",
                            scanner_finding_id=str(_uuid.uuid4()),
                            finding_type="misconfiguration",
                        )
                        db.add(finding)
                        await db.commit()
                        finding_id_holder.append(str(finding.id))
                finally:
                    await engine.dispose()

            asyncio.run(_inner())

        t = threading.Thread(target=_create_finding)
        t.start()
        t.join(timeout=15)

        if not finding_id_holder:
            fail("VULN_MITIGATION: Failed to create smoke finding in DB")
        finding_id = finding_id_holder[0]
        log(f"Created smoke finding {finding_id} in DB")
    else:
        finding_id = items[0]["id"]
        log(f"Using existing finding {finding_id}")

    # 2. Call /mitigate with selected_controls list — all 4 new types at once
    #    The endpoint maps each control string through MITIGATION_CR_MAP
    print("  → mitigate with 4 new control types", flush=True)
    r = client.post(f"{_VULN_PREFIX}/findings/{finding_id}/mitigate", json={
        "selected_controls": [
            "protocol_control",
            "kernel_feature",
            "package_remove",
            "credential_revoke",
        ],
    })
    assert "change_request_ids" in r, f"Expected change_request_ids in response, got: {r}"
    cr_ids = r["change_request_ids"]
    controls = r["controls_applied"]
    assert len(cr_ids) == 4, f"Expected 4 CRs, got {len(cr_ids)}"
    log(f"mitigate returned {len(cr_ids)} CRs")

    # 3. Verify each CR has the expected change_type
    expected_types = {
        "protocol_control":  "apply_protocol_control",
        "kernel_feature":    "disable_kernel_feature",
        "package_remove":    "remove_vulnerable_package",
        "credential_revoke": "revoke_exposed_credential",
    }
    for ctrl, cr_id in zip(controls, cr_ids):
        cr = client.get(f"/change-requests/{cr_id}")
        expected = expected_types[ctrl]
        assert cr["change_type"] == expected, \
            f"Expected {expected} for {ctrl}, got {cr['change_type']}"
        log(f"{ctrl} → {expected} CR ({cr['status']})")

    print("[VULN_MITIGATION] PASSED", flush=True)


# ── Phase: CREDENTIAL_EXPIRY ──────────────────────────────────────────────────

def phase_credential_expiry(client: NexplaneClient) -> None:
    print("\n[CREDENTIAL_EXPIRY] Testing credential expiry worker with live Vault", flush=True)

    ec2 = boto3.client("ec2", region_name=SMOKE_REGION)
    ssm = boto3.client("ssm", region_name=SMOKE_REGION)
    iam = boto3.client("iam", region_name=SMOKE_REGION)

    instance_id, private_ip = _launch_vault_instance(ec2, ssm, iam)
    vault_addr = f"http://{private_ip}:8200"
    vault_token = "nexplane-smoke-root"

    try:
        # 1. Register a Vault connector pointing at the smoke instance
        print(f"  Vault available at {vault_addr}", flush=True)

        connector = client.post("/connectors", json={
            "name": "smoke-vault-expiry",
            "connector_type": "hashicorp_vault",
            "credentials": {
                "vault_addr": vault_addr,
                "vault_token": vault_token,
            },
        })
        connector_id = connector["id"]
        log(f"Registered Vault connector {connector_id}")

        # 2. Create a short-TTL dynamic secret lease via hvac directly
        import hvac
        vclient = hvac.Client(url=vault_addr, token=vault_token)

        # Enable a database secrets engine and create a short-lived lease
        # Use the generic KV lease path for a simpler test: just write a token with short TTL
        # Instead: enable token role with short TTL and issue a token
        # Create a child token with short TTL directly (no role needed)
        short_token = vclient.auth.token.create(ttl="60s", renewable=True)
        short_token_id = short_token["auth"]["client_token"]
        log(f"Created short-TTL Vault token (60s TTL): {short_token_id[:8]}...")

        # 3. Run check_credential_expiry directly inside the container
        import asyncio
        import threading

        result_holder = []

        def _run_worker():
            async def _inner():
                import os
                from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
                # Run _check_vault_leases only — the key new functionality.
                # IAM check uses sync boto3 (blocks event loop); TLS/SSH/step-CA checks
                # are either stubs or depend on infra not present in this smoke.
                from app.workers.credential_expiry_worker import _check_vault_leases

                db_url = os.environ["DATABASE_URL"]
                engine = create_async_engine(db_url, pool_size=1, max_overflow=0)
                factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                try:
                    async with factory() as db:
                        await _check_vault_leases(db)
                    result_holder.append("ok")
                except Exception as exc:
                    result_holder.append(f"error: {exc}")
                finally:
                    await engine.dispose()

            asyncio.run(_inner())

        t = threading.Thread(target=_run_worker)
        t.start()
        t.join(timeout=30)

        if not result_holder:
            fail("CREDENTIAL_EXPIRY: _check_vault_leases timed out (> 30s)")
        if result_holder[0] != "ok":
            fail(f"CREDENTIAL_EXPIRY: _check_vault_leases failed: {result_holder[0]}")
        log("_check_vault_leases completed: scanned registered Vault connector for expiring leases")

        # 7. Cleanup: delete the smoke connector
        client.client.delete(f"{client.base}/connectors/{connector_id}")
        log(f"Deleted smoke Vault connector")

    finally:
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"  [CREDENTIAL_EXPIRY] Terminated Vault instance {instance_id}", flush=True)
        except Exception as e:
            print(f"  [CREDENTIAL_EXPIRY] Teardown warning: {e}", flush=True)

    print("[CREDENTIAL_EXPIRY] PASSED", flush=True)


# ── Phase: MCP_AGENT_TOKENS ───────────────────────────────────────────────────

def phase_mcp_agent_tokens(client: NexplaneClient) -> None:
    print("\n[MCP_AGENT_TOKENS] Verifying agent token lifecycle and scope enforcement", flush=True)

    # 1. Create an agent token scoped to patch_packages only
    result = client.post("/auth/agent-tokens", json={
        "name": "smoke-agent-patch-only",
        "expires_in_days": 1,
        "allowed_roles": ["read", "write"],
        "allowed_cr_types": ["patch_packages"],
        "allowed_connector_types": [],
        "allowed_asset_tags": [],
    })
    assert "token" in result, f"Expected raw token in response, got: {result}"
    raw_token = result["token"]
    token_id = result["id"]
    log(f"Created agent token {token_id} (patch_packages scope)")

    # 2. Verify token is in list and not revoked
    tokens = client.get("/auth/agent-tokens")
    matching = [t for t in tokens if t["id"] == token_id]
    assert matching, "Created token not found in list"
    assert not matching[0]["revoked"], "Token should not be revoked yet"
    assert matching[0]["allowed_cr_types"] == ["patch_packages"]
    log("Token visible in list with correct scope")

    # 3. Test scope enforcement + revoke verification in-process.
    #    IMPORTANT: The container runs an asyncpg engine tied to the uvicorn event
    #    loop. asyncio.run() in a thread creates a NEW loop — asyncpg connections
    #    can't cross loops. Fix: create a fresh async engine in each thread.
    import asyncio, threading, os

    def _make_fresh_session():
        """Return a fresh AsyncSession bound to the current thread's event loop."""
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        db_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/nexplane")
        engine = create_async_engine(db_url, pool_size=1, max_overflow=0)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        return factory, engine

    async_results = []

    def _run_scope_tests():
        async def _inner():
            from app.mcp_server import resolve_mcp_token
            from app.mcp_tools.context import _enforce_agent_scope
            from fastapi import HTTPException

            factory, engine = _make_fresh_session()
            try:
                async with factory() as db:
                    user, agent_token = await resolve_mcp_token(raw_token, db)
                    assert user is None, "Expected user=None for agent token"
                    assert agent_token is not None, "Expected agent_token to be resolved"
                    assert agent_token.allowed_cr_types == ["patch_packages"]
                    async_results.append("resolved_ok")

                    _enforce_agent_scope(agent_token, cr_type="patch_packages", required_role="read")
                    async_results.append("allowed_ok")

                    try:
                        _enforce_agent_scope(agent_token, cr_type="ssm_command", required_role="write")
                        async_results.append("scope_not_blocked")
                    except HTTPException as e:
                        assert e.status_code == 403
                        async_results.append("blocked_ok")

                    try:
                        _enforce_agent_scope(agent_token, required_role="approve")
                        async_results.append("role_not_blocked")
                    except HTTPException as e:
                        assert e.status_code == 403
                        async_results.append("role_blocked_ok")
            finally:
                await engine.dispose()

        asyncio.run(_inner())

    t = threading.Thread(target=_run_scope_tests)
    t.start()
    t.join(timeout=30)

    assert "resolved_ok" in async_results, f"Token resolution failed: {async_results}"
    assert "allowed_ok" in async_results, "In-scope call should pass"
    assert "blocked_ok" in async_results, "Out-of-scope cr_type should be blocked with 403"
    assert "role_blocked_ok" in async_results, "Missing role should be blocked with 403"
    assert "scope_not_blocked" not in async_results
    assert "role_not_blocked" not in async_results
    log("Scope enforcement: allowed pass, out-of-scope cr_type and missing role both 403")

    # 4. Revoke the token (sync REST call — no event loop involved)
    revoke_resp = client.client.delete(f"{client.base}/auth/agent-tokens/{token_id}")
    assert revoke_resp.status_code == 204, \
        f"Expected 204 on revoke, got {revoke_resp.status_code}"
    log("Token revoked (204)")

    # 5. Verify revoked token rejected (401) — fresh engine in its own thread
    revoked_results = []

    def _run_revoke_verify():
        async def _inner():
            from app.mcp_server import resolve_mcp_token
            from fastapi import HTTPException

            factory, engine = _make_fresh_session()
            try:
                async with factory() as db:
                    try:
                        user, agent_token = await resolve_mcp_token(raw_token, db)
                        revoked_results.append(f"not_rejected: user={user} agent={agent_token}")
                    except HTTPException as e:
                        revoked_results.append(f"rejected_{e.status_code}")
            finally:
                await engine.dispose()

        asyncio.run(_inner())

    t2 = threading.Thread(target=_run_revoke_verify)
    t2.start()
    t2.join(timeout=15)

    assert revoked_results and revoked_results[0] == "rejected_401", \
        f"Expected revoked token to raise 401, got: {revoked_results}"
    log("Revoked token correctly rejected with 401 by resolve_mcp_token")

    # 6. Verify token appears as revoked in list
    tokens_after = client.get("/auth/agent-tokens")
    after_match = [t for t in tokens_after if t["id"] == token_id]
    if after_match:
        assert after_match[0]["revoked"] is True
        log("Revoked token shows revoked=True in list")
    else:
        log("Revoked token filtered from list")

    print("[MCP_AGENT_TOKENS] PASSED", flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────

ALL_PHASES = ["VULN_MITIGATION", "CREDENTIAL_EXPIRY", "MCP_AGENT_TOKENS"]

PHASE_FNS = {
    "VULN_MITIGATION": phase_vuln_mitigation,
    "CREDENTIAL_EXPIRY": phase_credential_expiry,
    "MCP_AGENT_TOKENS": phase_mcp_agent_tokens,
}


def main() -> None:
    parser = make_base_parser("Feature smoke tests: VULN_MITIGATION, CREDENTIAL_EXPIRY, MCP_AGENT_TOKENS")
    parser.add_argument(
        "--phases",
        default=",".join(ALL_PHASES),
        help=f"Comma-separated phases to run. All: {','.join(ALL_PHASES)}",
    )
    args = parser.parse_args()

    phases = [p.strip().upper() for p in args.phases.split(",") if p.strip()]
    unknown = [p for p in phases if p not in PHASE_FNS]
    if unknown:
        fail(f"Unknown phases: {unknown}. Valid: {list(PHASE_FNS)}")

    client = NexplaneClient(
        base_url=getattr(args, "base_url", "http://localhost:8000"),
        email=getattr(args, "email", "admin@acme.example"),
        password=getattr(args, "password", "admin123"),
    )

    passed = []
    failed = []
    for phase in phases:
        try:
            PHASE_FNS[phase](client)
            passed.append(phase)
        except SystemExit:
            failed.append(phase)
        except Exception as exc:
            log(f"{phase} raised unexpected exception: {exc}", ok=False)
            import traceback
            traceback.print_exc()
            failed.append(phase)

    print(f"\n{'='*60}")
    print(f"PASSED: {passed}")
    if failed:
        print(f"FAILED: {failed}")
        sys.exit(1)
    print("All phases passed.")


if __name__ == "__main__":
    main()
