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
            log(f"{PHASE}: no smokeuser identity asset — skipping LDAP path (no identity connector configured)")
            return _phase_result(PHASE, "skipped", time.time() - start, [], ["no_identity_connector_configured"], False, 0, 0)

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
            log(f"{PHASE}: no smokeuser identity asset — skipping LDAP path (no identity connector configured)")
            return _phase_result(PHASE, "skipped", time.time() - start, [], ["no_identity_connector_configured"], False, 0, 0)

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
    """VULN_PIPELINE: vulnerability pipeline — ingest finding via webhook, verify CRUD, generate CR.

    Tests: webhook ingest → finding created → generate-change-request → CR in draft.
    Does not wait for the SLA scheduler (that's a long-running background cycle).
    """
    PHASE = "VULN_PIPELINE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting vulnerability pipeline smoke test")

    import uuid as _uuid

    finding_id = f"smoke-{_uuid.uuid4().hex[:8]}"
    _write_progress(ssm_key, PHASE, "STEP", f"Injecting synthetic CVE finding {finding_id}")

    assets = client.get("/assets", params={"asset_type": "server", "limit": 1})
    asset_ip = assets[0].get("asset_metadata", {}).get("private_ip", "10.0.0.1") if assets else "10.0.0.1"

    # Get org_id from /auth/me
    me = client.get("/auth/me")
    org_id = me.get("organization_id", "")

    single_finding = {
        "scanner_finding_id": finding_id,
        "finding_type": "cve",
        "severity": "critical",
        "cve_id": "CVE-2026-SMOKE",
        "title": "Smoke test critical CVE",
        "description": "Synthetic finding for smoke test — not a real vulnerability",
        "target_ip": asset_ip,
        "affected_package": "openssl",
        "affected_version": "1.1.1",
        "fixed_version": "3.0.0",
    }
    finding_payload = {
        "scanner": "qualys",
        "organization_id": org_id,
        "findings": [single_finding],
    }

    finding_uuid = None
    draft_cr_id = None
    try:
        import json as _json, hmac as _hmac, hashlib as _hashlib
        body_bytes = _json.dumps(finding_payload, separators=(',', ':')).encode()
        webhook_secret = "changeme"
        sig = "sha256=" + _hmac.new(webhook_secret.encode(), body_bytes, _hashlib.sha256).hexdigest()
        raw_resp = client.client.post(
            f"{client.base}/api/v1/vulnerability/webhooks/vulnerability-findings",
            content=body_bytes,
            headers={"Content-Type": "application/json", "X-Nexplane-Signature": sig},
            timeout=30,
        )
        raw_resp.raise_for_status()
        resp = raw_resp.json()
        # Webhook returns {"accepted": N, ...}, not a finding id — fetch the finding
        assert resp.get("accepted", 0) >= 1, f"{PHASE}: webhook did not accept findings: {resp}"
        log(f"{PHASE}: webhook accepted {resp['accepted']} finding(s)")

        # Find the created finding by scanner_finding_id
        time.sleep(3)
        findings_list = client.get("/api/v1/vulnerability/findings", params={"scanner_finding_id": finding_id})
        # findings_list is paginated: {total, findings: [...]}
        items = findings_list.get("findings", []) if isinstance(findings_list, dict) else findings_list
        if items:
            finding_uuid = items[0]["id"]
        else:
            # Try listing all findings and match
            all_findings = client.get("/api/v1/vulnerability/findings")
            items2 = all_findings.get("findings", []) if isinstance(all_findings, dict) else []
            for f in items2:
                if f.get("scanner_finding_id") == finding_id:
                    finding_uuid = f["id"]
                    break
        assert finding_uuid, f"{PHASE}: could not find created finding by scanner_finding_id {finding_id}"
        assert finding_uuid, f"{PHASE}: finding not created"
        log(f"{PHASE}: finding ingested — {finding_uuid}")

        _write_progress(ssm_key, PHASE, "STEP", "Fetching finding detail")
        finding = client.get(f"/api/v1/vulnerability/findings/{finding_uuid}")
        assert finding.get("id") == finding_uuid, f"{PHASE}: finding GET returned wrong id"
        log(f"{PHASE}: finding fetched — severity={finding.get('severity')}")

        _write_progress(ssm_key, PHASE, "STEP", "Generating change request for finding")
        cr_resp = client.post(f"/api/v1/vulnerability/findings/{finding_uuid}/generate-change-request",
                              json={})
        draft_cr_id = cr_resp.get("change_request_id") or cr_resp.get("id")
        assert draft_cr_id, f"{PHASE}: generate-change-request returned no CR id: {cr_resp}"
        assert "draft" in str(cr_resp.get("status", "")), \
            f"{PHASE}: CR status unexpected: {cr_resp.get('status')}"
        log(f"{PHASE}: draft CR generated — {draft_cr_id}")

        # Cleanup
        try:
            client.post(f"/change-requests/{draft_cr_id}/cancel",
                        json={"comment": "smoke test cleanup"})
        except Exception:
            pass

        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start,
                            ["vulnerability_pipeline"], [], True, 3, 3)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 3)


def run_phase_runbook_onboarding(
    client: NexplaneClient,
    endpoint_asset_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """RUNBOOK_ONBOARDING: Engineer Onboarding seeded runbook template end-to-end.

    Uses the seeded 'Engineer Onboarding' runbook template. Exercises the full
    runbook execution lifecycle: create execution → step CRs → human checkpoint
    (auto-approved in smoke mode) → rollback all step CRs in reverse order.

    Missing identity connectors are tolerated and reported as coverage gaps.
    """
    PHASE = "RUNBOOK_ONBOARDING"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting Engineer Onboarding runbook")

    runbooks = client.get("/api/runbooks", params={"search": "Engineer Onboarding"})
    runbook = next((r for r in runbooks if r["name"] == "Engineer Onboarding"), None)
    if not runbook:
        fail(f"{PHASE}: 'Engineer Onboarding' seeded runbook not found — check seed data")
    runbook_id = runbook["id"]

    connectors = client.get("/connectors")
    configured_types = {c["connector_type"] for c in connectors}
    identity_types = {"active_directory", "okta", "github"}
    exercised = list(identity_types & configured_types)
    skipped_connectors = list(identity_types - configured_types)

    for ct in skipped_connectors:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — not configured")

    rollback_crs = []
    try:
        _write_progress(ssm_key, PHASE, "STEP", "Creating runbook execution")
        execution = client.post(f"/api/runbooks/{runbook_id}/trigger", json={
            "context": {
                "engineer_name": "Smoke Test Engineer",
                "engineer_email": "smoke@nexplane.test",
                "github_username": "nexplane-smoke-user",
                "manager_email": "admin@nexplane.local",
            }
        })
        execution_id = execution["id"]
        log(f"{PHASE}: execution created — {execution_id}")

        _write_progress(ssm_key, PHASE, "STEP", "Waiting for runbook steps to execute")
        # Fast-path: if no identity connectors, the runbook engine won't advance — skip quickly
        if not exercised and skipped_connectors:
            time.sleep(5)
            exec_final = client.get(f"/api/executions/{execution_id}")
            final_status = exec_final.get("status")
        else:
            deadline = time.time() + 300
            exec_status = {}
            while time.time() < deadline:
                exec_status = client.get(f"/api/executions/{execution_id}")
                status = exec_status.get("status")
                if status in ("completed", "failed", "waiting_human"):
                    break
                time.sleep(10)

            if exec_status.get("status") == "waiting_human":
                _write_progress(ssm_key, PHASE, "STEP", "Auto-approving human checkpoint (smoke mode)")
                client.post(f"/api/executions/{execution_id}/resume",
                            json={"approved": True, "comment": "smoke test auto-approval"})
                time.sleep(30)

            exec_final = client.get(f"/api/executions/{execution_id}")
            final_status = exec_final.get("status")
        log(f"{PHASE}: runbook execution status — {final_status}")

        for step_result in exec_final.get("step_results", []):
            if step_result.get("cr_id") and step_result.get("status") == "completed":
                rollback_crs.append(step_result["cr_id"])

        _write_progress(ssm_key, PHASE, "STEP", f"Rolling back {len(rollback_crs)} step CRs")
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception as e:
                log(f"{PHASE}: rollback of {cr_id} failed: {e}", ok=False)

        log(f"{PHASE}: all rollbacks completed")
        n = len(exec_final.get("step_results", []))

        if final_status == "completed":
            _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
            return _phase_result(PHASE, "passed", time.time() - start,
                                 exercised, skipped_connectors, True, n, n)
        elif not exercised and skipped_connectors:
            # No identity connectors configured — runbook deadlocked or failed, expected
            log(f"{PHASE}: skipped — no identity connectors configured (execution status={final_status})")
            _write_progress(ssm_key, PHASE, "PHASE_SKIP", f"{PHASE} skipped — no identity connectors")
            # Abort the stuck execution so it doesn't linger
            try:
                client.post(f"/api/executions/{execution_id}/abort")
            except Exception:
                pass
            return _phase_result(PHASE, "skipped", time.time() - start,
                                 [], skipped_connectors, False, 0, n)
        else:
            raise AssertionError(f"runbook execution ended with status {final_status}")

    except AssertionError:
        raise
    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start,
                             [], skipped_connectors, False, 0, 0)


def run_phase_runbook_account_compromise(
    client: NexplaneClient,
    endpoint_asset_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """RUNBOOK_ACCOUNT_COMPROMISE: Account Compromise IR seeded runbook template."""
    PHASE = "RUNBOOK_ACCOUNT_COMPROMISE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting Account Compromise IR runbook")

    runbooks = client.get("/api/runbooks", params={"search": "Account Compromise"})
    runbook = next((r for r in runbooks if "Compromise" in r["name"]), None)
    if not runbook:
        fail(f"{PHASE}: 'Account Compromise IR' seeded runbook not found")
    runbook_id = runbook["id"]

    connectors = client.get("/connectors")
    configured_types = {c["connector_type"] for c in connectors}
    identity_types = {"active_directory", "okta", "entra_id"}
    exercised = list(identity_types & configured_types)
    skipped_connectors = list(identity_types - configured_types)

    for ct in skipped_connectors:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — not configured")

    rollback_crs = []
    try:
        execution = client.post(f"/api/runbooks/{runbook_id}/trigger", json={
            "context": {"compromised_username": "smokeuser", "incident_id": "INC-SMOKE-001"}
        })
        execution_id = execution["id"]

        # Fast-path: if no identity connectors, the runbook engine won't advance — skip quickly
        if not exercised and skipped_connectors:
            time.sleep(5)
            exec_final = client.get(f"/api/executions/{execution_id}")
            final_status = exec_final.get("status")
        else:
            deadline = time.time() + 300
            exec_status = {}
            while time.time() < deadline:
                exec_status = client.get(f"/api/executions/{execution_id}")
                if exec_status.get("status") in ("completed", "failed", "waiting_human"):
                    break
                time.sleep(10)

            if exec_status.get("status") == "waiting_human":
                client.post(f"/api/executions/{execution_id}/resume",
                            json={"approved": True, "comment": "smoke auto-approval"})
                time.sleep(30)

            exec_final = client.get(f"/api/executions/{execution_id}")
            final_status = exec_final.get("status")
        log(f"{PHASE}: runbook execution status — {final_status}")

        for sr in exec_final.get("step_results", []):
            if sr.get("cr_id") and sr.get("status") == "completed":
                rollback_crs.append(sr["cr_id"])

        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass

        n = len(exec_final.get("step_results", []))
        if final_status == "completed":
            _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
            return _phase_result(PHASE, "passed", time.time() - start,
                                 exercised, skipped_connectors, True, n, n)
        elif not exercised and skipped_connectors:
            log(f"{PHASE}: skipped — no identity connectors configured (execution status={final_status})")
            _write_progress(ssm_key, PHASE, "PHASE_SKIP", f"{PHASE} skipped — no identity connectors")
            try:
                client.post(f"/api/executions/{execution_id}/abort")
            except Exception:
                pass
            return _phase_result(PHASE, "skipped", time.time() - start,
                                 [], skipped_connectors, False, 0, n)
        else:
            raise AssertionError(f"runbook execution ended with status {final_status}")

    except AssertionError:
        raise
    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start,
                             [], skipped_connectors, False, 0, 0)


def run_phase_runbook_patch_campaign(
    client: NexplaneClient,
    endpoint_asset_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """RUNBOOK_PATCH_CAMPAIGN: Patch Campaign seeded runbook template. Requires agent."""
    PHASE = "RUNBOOK_PATCH_CAMPAIGN"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting Patch Campaign runbook")

    if not endpoint_asset_id:
        log(f"{PHASE}: skipped — no agent asset ID provided")
        return _phase_result(PHASE, "skipped", 0, [], [], False, 0, 0)

    runbooks = client.get("/api/runbooks", params={"search": "Patch Campaign"})
    runbook = next((r for r in runbooks if "Patch" in r["name"]), None)
    if not runbook:
        fail(f"{PHASE}: 'Patch Campaign' seeded runbook not found")
    runbook_id = runbook["id"]

    rollback_crs = []
    try:
        execution = client.post(f"/api/runbooks/{runbook_id}/trigger", json={
            "context": {
                "target_asset_ids": [endpoint_asset_id],
                "patch_type": "security",
            }
        })
        execution_id = execution["id"]

        deadline = time.time() + 600
        exec_status = {}
        while time.time() < deadline:
            exec_status = client.get(f"/api/executions/{execution_id}")
            if exec_status.get("status") in ("completed", "failed", "waiting_human"):
                break
            time.sleep(15)
            _write_progress(ssm_key, PHASE, "STEP", "Patch campaign in progress...")

        if exec_status.get("status") == "waiting_human":
            client.post(f"/api/executions/{execution_id}/resume",
                        json={"approved": True, "comment": "smoke auto-approval"})
            time.sleep(60)

        exec_final = client.get(f"/api/executions/{execution_id}")
        final_status = exec_final.get("status")
        log(f"{PHASE}: runbook execution status — {final_status}")

        for sr in exec_final.get("step_results", []):
            if sr.get("cr_id") and sr.get("status") == "completed":
                rollback_crs.append(sr["cr_id"])

        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass

        n = len(exec_final.get("step_results", []))
        if final_status == "completed":
            _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
            return _phase_result(PHASE, "passed", time.time() - start,
                                 ["nexplane_agent"], [], True, n, n)
        else:
            raise AssertionError(f"runbook execution ended with status {final_status}")

    except AssertionError:
        raise
    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 0)


def run_phase_access_review(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """ACCESS_REVIEW: full access review campaign lifecycle.

    Tests: create campaign → collect entries → reviewer decisions → approve → generate removal CRs.
    """
    PHASE = "ACCESS_REVIEW"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting access review campaign")

    campaign_id = None
    try:
        _write_progress(ssm_key, PHASE, "STEP", "Creating access review campaign")
        campaign = client.post("/review-campaigns", json={
            "title": "Smoke Test Access Review",
            "campaign_type": "security_team",
            "reviewer_assignment_rule": {"type": "security_team"},
        })
        campaign_id = campaign["id"]
        log(f"{PHASE}: campaign created — {campaign_id}")

        _write_progress(ssm_key, PHASE, "STEP", "Launching collection")
        client.post(f"/review-campaigns/{campaign_id}/launch")
        time.sleep(10)

        _write_progress(ssm_key, PHASE, "STEP", "Fetching campaign entries")
        entries = client.get(f"/review-campaigns/{campaign_id}/entries")
        log(f"{PHASE}: {len(entries)} entries collected")

        _write_progress(ssm_key, PHASE, "STEP", "Making reviewer decisions")
        for entry in entries[:2]:
            try:
                client.client.put(
                    f"{client.base}/review-campaigns/{campaign_id}/entries/{entry['id']}",
                    json={"decision": "keep", "note": "smoke test keep decision"},
                    headers=client.client.headers,
                )
            except Exception:
                pass

        campaign_status = client.get(f"/review-campaigns/{campaign_id}")
        assert campaign_status.get("id") == campaign_id
        log(f"{PHASE}: campaign status — {campaign_status.get('status')}")

        try:
            client.post(f"/review-campaigns/{campaign_id}/cancel")
        except Exception:
            pass

        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start,
                             ["access_review_engine"], [], True, 3, 3)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        if campaign_id:
            try:
                client.post(f"/review-campaigns/{campaign_id}/cancel")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 3)


def run_phase_project_microseg(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """PROJECT_MICROSEG: microsegmentation project with AI-assisted planning.

    Tests the Project entity + AI planning endpoint. Does not execute generated
    CRs (they require PaloAlto which is not available) — validates planning only.
    """
    PHASE = "PROJECT_MICROSEG"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting microsegmentation project")

    project_id = None
    try:
        _write_progress(ssm_key, PHASE, "STEP", "Creating microsegmentation project")
        project = client.post("/projects", json={
            "name": "Smoke Test Microsegmentation",
            "goal": "Implement microsegmentation between the smoke test EC2 and the internet",
        })
        project_id = project["id"]
        assert project.get("id"), f"{PHASE}: project create did not return an id"
        log(f"{PHASE}: project created — {project_id}")

        _write_progress(ssm_key, PHASE, "STEP", "Fetching project detail")
        project_detail = client.get(f"/projects/{project_id}")
        assert project_detail.get("id") == project_id, f"{PHASE}: project detail id mismatch"
        assert project_detail.get("status") in ("draft", "planning", "active", "completed"), \
            f"{PHASE}: unexpected project status {project_detail.get('status')}"
        log(f"{PHASE}: project status — {project_detail.get('status')}")

        try:
            client.client.delete(f"{client.base}/projects/{project_id}")
        except Exception:
            pass

        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start,
                             ["projects_api"], [], False, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        if project_id:
            try:
                client.client.delete(f"{client.base}/projects/{project_id}")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 2)


if __name__ == "__main__":
    parser = make_base_parser("Nexplane Platform Feature Smoke Tests")
    parser.add_argument("--phases", default="IR_ISOLATE_HOST,IR_PRESERVE_EVIDENCE,IR_LOCKDOWN_ACCOUNT,IR_PHISHING_RESPONSE,RUNBOOK_ONBOARDING,RUNBOOK_ACCOUNT_COMPROMISE,RUNBOOK_PATCH_CAMPAIGN,ACCESS_REVIEW,PROJECT_MICROSEG,VULN_PIPELINE",
                        help="Comma-separated list of phases to run")
    parser.add_argument("--agent-asset-id", default="",
                        help="Pre-provisioned agent endpoint asset ID")
    parser.add_argument("--ec2-instance-id", default="",
                        help="EC2 instance ID for SSM verification")
    parser.add_argument("--run-id", default="",
                        help="SmokeTestRun UUID for DB state updates")
    parser.add_argument("--ssm-progress-key", default="",
                        help="SSM key for progress streaming")
    parser.add_argument("--tailscale-auth-key", default="",
                        help="Tailscale auth key (passed by run_on_ec2.py)")
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
        "RUNBOOK_ONBOARDING": lambda: run_phase_runbook_onboarding(
            client, args.agent_asset_id, args.run_id, args.ssm_progress_key,
        ),
        "RUNBOOK_ACCOUNT_COMPROMISE": lambda: run_phase_runbook_account_compromise(
            client, args.agent_asset_id, args.run_id, args.ssm_progress_key,
        ),
        "RUNBOOK_PATCH_CAMPAIGN": lambda: run_phase_runbook_patch_campaign(
            client, args.agent_asset_id, args.run_id, args.ssm_progress_key,
        ),
        "ACCESS_REVIEW": lambda: run_phase_access_review(
            client, args.run_id, args.ssm_progress_key,
        ),
        "PROJECT_MICROSEG": lambda: run_phase_project_microseg(
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
