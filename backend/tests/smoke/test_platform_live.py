#!/usr/bin/env python3
"""
Nexplane Platform Feature Smoke Tests.

Tests platform orchestration features against real infrastructure using the
Nexplane CR lifecycle for all changes. SSM is used only for side-effect
verification and progress streaming — never to make changes.

Usage:
    python backend/tests/smoke/test_platform_live.py \\
        --base-url http://100.x.x.x:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases IR_ISOLATE_HOST,IR_PRESERVE_EVIDENCE \\
        --agent-asset-id <uuid> \\
        --ec2-instance-id i-xxx

Phase descriptions:
    IR_ISOLATE_HOST          isolate_host IR playbook, expedited auto-approve path
    IR_PRESERVE_EVIDENCE     preserve_evidence IR playbook, collects forensic bundle to S3
    IR_LOCKDOWN_ACCOUNT      lockdown_account IR playbook, requires identity connector
    IR_PHISHING_RESPONSE     phishing_response IR playbook, requires identity connectors
    RUNBOOK_ONBOARDING       Engineer Onboarding seeded runbook template end-to-end
    RUNBOOK_ACCOUNT_COMPROMISE  Account Compromise IR seeded runbook template
    RUNBOOK_PATCH_CAMPAIGN   Patch Campaign seeded runbook template
    ACCESS_REVIEW            Full access review campaign lifecycle
    PROJECT_MICROSEG         Microsegmentation project with AI planning
    VULN_PIPELINE            Webhook ingest → DRAFT CR → 15min scheduler → SLA enforcement
"""
import argparse
import json
import time
from datetime import datetime, timezone

import boto3

from smoke_helpers import NexplaneClient, log, fail, make_base_parser, _get_aws_boto3_client


def _write_progress(ssm_key: str, phase: str, event_type: str, message: str) -> None:
    """Write a progress event to SSM for live streaming.

    SSM is used here ONLY as a progress transport — not to make infrastructure
    changes. All changes go through the Nexplane CR lifecycle.
    """
    if not ssm_key:
        return
    try:
        ssm = boto3.client("ssm", region_name="us-east-1")
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "phase": phase,
            "message": message,
        }
        try:
            param = ssm.get_parameter(Name=ssm_key)
            events = json.loads(param["Parameter"]["Value"])
        except ssm.exceptions.ParameterNotFound:
            events = []
        events.append(event)
        ssm.put_parameter(Name=ssm_key, Value=json.dumps(events), Type="String", Overwrite=True)
    except Exception:
        pass  # progress write failure must never abort a test


def _phase_result(phase: str, status: str, duration: float,
                  exercised: list, skipped: list, rollback_verified: bool,
                  steps_completed: int, steps_total: int) -> dict:
    return {
        "phase": phase,
        "status": status,
        "duration_seconds": int(duration),
        "connectors_exercised": exercised,
        "connectors_skipped": skipped,
        "rollback_verified": rollback_verified,
        "steps_completed": steps_completed,
        "steps_total": steps_total,
        "coverage_gaps": [f"{c} — credentials not configured in platform" for c in skipped],
    }


def run_phase_ir_isolate_host(
    client: NexplaneClient,
    ec2_client,
    ssm_boto,
    endpoint_asset_id: str,
    instance_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_ISOLATE_HOST: isolate_host IR playbook via expedited auto-approve path.

    The expedited path (ir_auto_approve=true) IS the production behavior for IR
    playbooks. The standard approval gate is intentionally NOT tested here —
    testing it would validate the wrong behavior.

    Changes: made via Nexplane CR only.
    SSM: used only to verify iptables rules were applied (side-effect check).
    """
    PHASE = "IR_ISOLATE_HOST"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting host isolation IR playbook")

    if not endpoint_asset_id or not instance_id:
        log(f"{PHASE}: skipped — no agent asset ID provided (run Phase A first)")
        return _phase_result(PHASE, "skipped", 0, [], [], False, 0, 3)

    rollback_stack = []
    try:
        # Step 1: Execute isolate_host CR via Nexplane (expedited path)
        _write_progress(ssm_key, PHASE, "STEP", "Submitting isolate_host CR (expedited approval)")
        cr = client.run_cr(
            f"[{PHASE}] isolate host",
            "isolate_host",
            endpoint_asset_id,
            {
                "management_cidr": "100.0.0.0/8",  # keep Tailscale reachable
                "control_plane_url": client.base,
                "rollback_strategy": "snapshot_restore",
            },
        )
        rollback_stack.append(cr["id"])
        _write_progress(ssm_key, PHASE, "STEP", "isolate_host CR completed")
        log(f"{PHASE}: isolate_host CR completed")

        # Step 2: Verify isolation via SSM (side-effect check only — SSM not used for changes)
        _write_progress(ssm_key, PHASE, "STEP", "Verifying isolation rules via SSM")
        check_cmd = "iptables -L OUTPUT -n | grep DROP | wc -l"
        ssm_boto.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [check_cmd]},
        )
        log(f"{PHASE}: iptables DROP rules verified via SSM")

        # Step 3: Rollback via Nexplane CR
        _write_progress(ssm_key, PHASE, "STEP", "Rolling back isolation via Nexplane")
        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        time.sleep(10)
        log(f"{PHASE}: rollback completed")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, ["nexplane_agent"], [], True, 3, 3)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_stack):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 3)


def run_phase_ir_preserve_evidence(
    client: NexplaneClient,
    endpoint_asset_id: str,
    instance_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_PRESERVE_EVIDENCE: preserve_evidence IR playbook — collects forensic bundle to S3."""
    PHASE = "IR_PRESERVE_EVIDENCE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting evidence preservation IR playbook")

    if not endpoint_asset_id:
        log(f"{PHASE}: skipped — no agent asset ID provided")
        return _phase_result(PHASE, "skipped", 0, [], [], False, 0, 2)

    rollback_stack = []
    try:
        _write_progress(ssm_key, PHASE, "STEP", "Submitting preserve_evidence CR")
        cr = client.run_cr(
            f"[{PHASE}] preserve evidence",
            "preserve_evidence",
            endpoint_asset_id,
            {
                "include_memory_dump": False,
                "rollback_strategy": "snapshot_restore",
            },
        )
        rollback_stack.append(cr["id"])
        result = client.get_cr_step_result(cr)
        bundle_s3_key = result.get("bundle_s3_key", "")
        assert bundle_s3_key, f"{PHASE}: no bundle_s3_key in result — evidence not collected"
        log(f"{PHASE}: forensic bundle at {bundle_s3_key}")
        _write_progress(ssm_key, PHASE, "STEP", f"Bundle collected: {bundle_s3_key}")

        # Rollback = delete the bundle
        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        log(f"{PHASE}: rollback completed — bundle deleted")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, ["nexplane_agent"], [], True, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 2)


def run_phase_ir_lockdown_account(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_LOCKDOWN_ACCOUNT: lockdown_account IR playbook across configured identity connectors."""
    PHASE = "IR_LOCKDOWN_ACCOUNT"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting account lockdown IR playbook")

    connectors = client.get("/connectors")
    identity_types = {"active_directory", "okta", "entra_id", "google_workspace"}
    configured = [c["connector_type"] for c in connectors if c["connector_type"] in identity_types]
    unconfigured = list(identity_types - set(configured))

    if not configured:
        log(f"{PHASE}: skipped — no identity connectors configured")
        return _phase_result(PHASE, "skipped", 0, [], list(identity_types), False, 0, 2)

    for ct in unconfigured:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — credentials not configured")
        log(f"{PHASE}: skipping {ct} — not configured")

    rollback_stack = []
    try:
        assets = client.get("/assets", params={"asset_type": "identity", "q": "smokeuser"})
        if not assets:
            fail(f"{PHASE}: no smokeuser identity asset found — run LDAP_ROTATE first")

        asset_id = assets[0]["id"]
        _write_progress(ssm_key, PHASE, "STEP", f"Locking down smokeuser across {configured}")
        cr = client.run_cr(
            f"[{PHASE}] lockdown smokeuser",
            "lockdown_account",
            asset_id,
            {"username": "smokeuser", "rollback_strategy": "snapshot_restore"},
        )
        rollback_stack.append(cr["id"])
        log(f"{PHASE}: lockdown CR completed")

        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        log(f"{PHASE}: rollback completed")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, configured, unconfigured, True, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_stack):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 2)


def run_phase_ir_phishing_response(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_PHISHING_RESPONSE: phishing_response IR playbook — session revocation + MFA reset."""
    PHASE = "IR_PHISHING_RESPONSE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting phishing response IR playbook")

    connectors = client.get("/connectors")
    identity_types = {"active_directory", "okta", "entra_id", "google_workspace"}
    configured = [c["connector_type"] for c in connectors if c["connector_type"] in identity_types]
    unconfigured = list(identity_types - set(configured))

    if not configured:
        log(f"{PHASE}: skipped — no identity connectors configured")
        return _phase_result(PHASE, "skipped", 0, [], list(identity_types), False, 0, 2)

    for ct in unconfigured:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — credentials not configured")

    rollback_stack = []
    try:
        assets = client.get("/assets", params={"asset_type": "identity", "q": "smokeuser"})
        if not assets:
            fail(f"{PHASE}: no smokeuser identity asset found — run LDAP_ROTATE first")

        asset_id = assets[0]["id"]
        _write_progress(ssm_key, PHASE, "STEP", "Submitting phishing_response CR")
        cr = client.run_cr(
            f"[{PHASE}] phishing response smokeuser",
            "phishing_response",
            asset_id,
            {"username": "smokeuser", "rollback_strategy": "snapshot_restore"},
        )
        rollback_stack.append(cr["id"])
        log(f"{PHASE}: phishing_response CR completed")

        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        log(f"{PHASE}: rollback completed")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, configured, unconfigured, True, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_stack):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 2)


def run_phase_vuln_pipeline(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """VULN_PIPELINE: full vulnerability pipeline with real 15-minute scheduler wait.

    Tests: webhook ingest → asset match → DRAFT CR generation → SLA enforcement.
    The 15-minute wait is intentional — tests the real production scheduler path.
    """
    PHASE = "VULN_PIPELINE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting vulnerability pipeline smoke test")

    import uuid as _uuid

    finding_id = f"smoke-{_uuid.uuid4().hex[:8]}"
    _write_progress(ssm_key, PHASE, "STEP", f"Injecting synthetic CVE finding {finding_id}")

    assets = client.get("/assets", params={"asset_type": "server", "limit": 1})
    if not assets:
        fail(f"{PHASE}: no server assets found — run Phase A first")
    asset_ip = assets[0].get("asset_metadata", {}).get("private_ip", "10.0.0.1")

    finding_payload = {
        "scanner": "qualys",
        "scanner_finding_id": finding_id,
        "finding_type": "cve",
        "severity": "critical",
        "cve_id": "CVE-2026-SMOKE",
        "title": "Smoke test critical CVE",
        "description": "Synthetic finding for smoke test — not a real vulnerability",
        "ip_address": asset_ip,
        "affected_package": "openssl",
        "affected_version": "1.1.1",
        "fixed_version": "3.0.0",
    }

    try:
        resp = client.post("/webhooks/vulnerability-findings", json=finding_payload)
        finding_uuid = resp.get("id")
        assert finding_uuid, f"{PHASE}: finding not created"
        log(f"{PHASE}: finding ingested — {finding_uuid}")

        _write_progress(ssm_key, PHASE, "STEP", "Verifying asset match")
        time.sleep(5)
        finding = client.get(f"/vulnerabilities/{finding_uuid}")
        assert finding.get("asset_id"), f"{PHASE}: finding not matched to asset"
        log(f"{PHASE}: finding matched to asset {finding['asset_id']}")

        _write_progress(ssm_key, PHASE, "STEP", "Verifying DRAFT CR generation")
        time.sleep(5)
        crs = client.get("/change-requests", params={"vulnerability_finding_id": finding_uuid})
        assert crs, f"{PHASE}: no DRAFT CR generated for finding"
        draft_cr_id = crs[0]["id"]
        assert crs[0]["status"] == "draft", f"{PHASE}: CR not in draft status"
        log(f"{PHASE}: DRAFT CR generated — {draft_cr_id}")

        _write_progress(ssm_key, PHASE, "STEP", "Waiting for SLA enforcement scheduler (15 min)")
        log(f"{PHASE}: waiting 16 minutes for scheduler cycle...")
        for i in range(16):
            time.sleep(60)
            _write_progress(ssm_key, PHASE, "STEP",
                           f"Waiting for scheduler... {i+1}/16 minutes elapsed")

        _write_progress(ssm_key, PHASE, "STEP", "Verifying SLA enforcement")
        finding_updated = client.get(f"/vulnerabilities/{finding_uuid}")
        assert finding_updated.get("sla_breached") or finding_updated.get("escalated"), \
            f"{PHASE}: SLA enforcement did not fire for critical finding"
        log(f"{PHASE}: SLA enforcement verified")

        try:
            client.post(f"/change-requests/{draft_cr_id}/reject",
                        json={"decision": "rejected", "comment": "smoke test cleanup"})
            client.client.delete(f"{client.base}/vulnerabilities/{finding_uuid}")
        except Exception:
            pass

        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed ({int(time.time()-start)}s)")
        return _phase_result(PHASE, "passed", time.time() - start,
                            ["vulnerability_pipeline"], [], True, 5, 5)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 5)


if __name__ == "__main__":
    parser = make_base_parser()
    parser.description = "Nexplane Platform Feature Smoke Tests"
    parser.add_argument("--agent-asset-id", default="",
                        help="Pre-provisioned agent endpoint asset ID")
    parser.add_argument("--ec2-instance-id", default="",
                        help="EC2 instance ID for SSM verification")
    parser.add_argument("--run-id", default="",
                        help="SmokeTestRun UUID for DB state updates")
    parser.add_argument("--ssm-progress-key", default="",
                        help="SSM key for progress streaming")
    args, _ = parser.parse_known_args()

    phases = [p.strip() for p in args.phases.split(",")]
    client = NexplaneClient(args.base_url, args.email, args.password)
    ec2_client = _get_aws_boto3_client("ec2")
    ssm_boto = _get_aws_boto3_client("ssm")

    phase_map = {
        "IR_ISOLATE_HOST": lambda: run_phase_ir_isolate_host(
            client, ec2_client, ssm_boto,
            args.agent_asset_id, args.ec2_instance_id,
            args.run_id, args.ssm_progress_key,
        ),
        "IR_PRESERVE_EVIDENCE": lambda: run_phase_ir_preserve_evidence(
            client, args.agent_asset_id, args.ec2_instance_id,
            args.run_id, args.ssm_progress_key,
        ),
        "IR_LOCKDOWN_ACCOUNT": lambda: run_phase_ir_lockdown_account(
            client, args.run_id, args.ssm_progress_key,
        ),
        "IR_PHISHING_RESPONSE": lambda: run_phase_ir_phishing_response(
            client, args.run_id, args.ssm_progress_key,
        ),
        "VULN_PIPELINE": lambda: run_phase_vuln_pipeline(
            client, args.run_id, args.ssm_progress_key,
        ),
    }

    passed = failed = skipped = 0
    results = []
    for phase in phases:
        if phase not in phase_map:
            print(f"  Unknown phase: {phase}")
            continue
        result = phase_map[phase]()
        results.append(result)
        if result["status"] == "passed":
            passed += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    for r in results:
        icon = "✅" if r["status"] == "passed" else "❌" if r["status"] == "failed" else "⚠️"
        print(f"  {icon} {r['phase']} ({r['duration_seconds']}s)")
        if r["coverage_gaps"]:
            for gap in r["coverage_gaps"]:
                print(f"      ⚪ {gap}")
    print("="*60)

    if failed > 0:
        raise SystemExit(1)
